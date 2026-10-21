"""Synthetic loopback HTTP tests for the model-free in-container helper."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HELPER = Path(__file__).resolve().parents[1] / "agents/penguin_agent/helper.mjs"
TOKEN = "SYNTHETIC_TOKEN_NEVER_PRINT_123456789"


@unittest.skipUnless(shutil.which("node"), "Node runtime required")
class HelperReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-penguin-ready-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.requests = []
        self.responses = []
        self.response_delay = 0
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self):
                test.requests.append((self.command, self.path, self.headers.get("Authorization")))
                if self.headers.get("Content-Length"):
                    self.rfile.read(int(self.headers["Content-Length"]))
                status, body = test.responses.pop(0)
                time.sleep(test.response_delay)
                raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "text/plain" if isinstance(body, str) else "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except BrokenPipeError:
                    pass  # Expected when the readiness deadline aborts the request.

            do_GET = respond
            do_PUT = respond

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        (self.root / "server.lock").write_text(json.dumps({"port": self.server.server_port}))
        (self.root / "api-token").write_text(TOKEN)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def invoke(self, command, *args):
        env = {k: v for k, v in os.environ.items()
               if k.upper() not in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NODE_OPTIONS")}
        result = subprocess.run(["node", str(HELPER), command, str(self.root), *args],
                                env=env, capture_output=True, text=True, timeout=10)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        return result, json.loads(result.stdout)

    def test_starting_503_is_one_check_and_not_ready(self):
        self.responses = [(503, "penguin-server is starting")]
        result, body = self.invoke("ready")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(body, {"ready": False, "http_status": 503})
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0], ("GET", "/api/projects", "Bearer " + TOKEN))

    def test_authenticated_200_is_ready_without_emitting_body_or_token(self):
        self.responses = [(200, {"projects": [], "synthetic_secret": TOKEN})]
        result, body = self.invoke("ready")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(body, {"ready": True, "http_status": 200})

    def test_401_and_missing_token_are_not_ready(self):
        self.responses = [(401, {"error": {"message": TOKEN}})]
        result, body = self.invoke("ready")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(body["ready"])
        (self.root / "api-token").unlink()
        result, body = self.invoke("ready")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(body, {"ready": False, "http_status": None})
        self.assertEqual(len(self.requests), 1)

    def test_expired_startup_deadline_does_not_make_a_request(self):
        self.responses = [(200, {"projects": []})]
        result, body = self.invoke("ready", "1")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(body, {"ready": False, "http_status": None})
        self.assertEqual(self.requests, [])

    def test_slow_response_is_capped_by_remaining_startup_time(self):
        self.responses = [(200, {"projects": []})]
        self.response_delay = 1
        started = time.monotonic()
        result, body = self.invoke("ready", str(int(time.time() * 1000) + 300))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(body, {"ready": False, "http_status": None})
        self.assertEqual(len(self.requests), 1)
        self.assertLess(time.monotonic() - started, 0.9)

    def test_existing_config_api_retries_starting_and_401(self):
        self.responses = [(503, "penguin-server is starting"), (401, {}),
                          (200, {}), (200, {"config": {"maxTurns": -1}})]
        result, body = self.invoke("set-max-turns", "synthetic", "fixture", "-1")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(body["max_turns"], -1)
        self.assertEqual([method for method, _, _ in self.requests], ["PUT", "PUT", "PUT", "GET"])
        self.assertTrue(all(auth == "Bearer " + TOKEN for _, _, auth in self.requests))

    def test_non_startup_503_is_not_retried(self):
        self.responses = [(503, "penguin-server has no running platform: synthetic failure"),
                          (503, "penguin-server has no running platform: synthetic failure")]
        result, body = self.invoke("set-max-turns", "synthetic", "fixture", "-1")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(body["put_status"], 503)
        self.assertEqual([method for method, _, _ in self.requests], ["PUT", "GET"])


class ShellReadinessDeadlineTests(unittest.TestCase):
    def test_slow_readiness_keeps_sixty_second_allowance_and_runs_cleanup(self):
        # A virtual clock makes every failed HTTP request take one second and
        # every poll sleep consume its real configured duration. Sixty attempts
        # would use 120s; the deadline must stop at 60s and still run cleanup.
        with tempfile.TemporaryDirectory(prefix="synthetic-penguin-deadline-") as tmp:
            root = Path(tmp)
            prefix, home, logs = root / "prefix", root / "home", root / "logs"
            (prefix / "node/bin").mkdir(parents=True)
            (prefix / "bin").mkdir()
            home.mkdir()
            clock = root / "clock"
            clock.write_text("0")
            node = prefix / "node/bin/node"
            node.write_text("#!" + sys.executable + "\n" + '''
import json, os, sys
from pathlib import Path
clock = Path(os.environ['FIXTURE_CLOCK'])
command, args = sys.argv[2], sys.argv[3:]
if command == 'now': print(clock.read_text())
elif command == 'ready':
    assert args[1] == '60000', args
    with Path(os.environ['FIXTURE_CHECKS']).open('a') as out: out.write('check\\n')
    clock.write_text(str(int(clock.read_text()) + 1000))
    print('{"ready":false,"http_status":503}')
    sys.exit(1)
elif command == 'redact': print('{"redacted":0}')
elif command == 'outcome': Path(args[0]).write_text(args[1])
else: raise SystemExit('unexpected helper command: ' + command)
''')
            penguin = prefix / "bin/penguin"
            penguin.write_text("#!" + sys.executable + "\n" + '''
import os, signal, sys, time
from pathlib import Path
pid = Path(os.environ['FIXTURE_SERVER_PID'])
if sys.argv[1:] == ['server']:
    pid.write_text(str(os.getpid()))
    time.sleep(8)
elif sys.argv[1:3] == ['server', 'stop']:
    Path(os.environ['FIXTURE_STOP_CLOCK']).write_text(Path(os.environ['FIXTURE_CLOCK']).read_text())
    if pid.exists():
        try: os.kill(int(pid.read_text()), signal.SIGTERM)
        except ProcessLookupError: pass
    print('{"stopped":true}')
else: raise SystemExit('must not start a model run: ' + repr(sys.argv))
''')
            sleeper = prefix / "bin/sleep"
            sleeper.write_text("#!" + sys.executable + "\n" + '''
import os, sys
from pathlib import Path
clock = Path(os.environ['FIXTURE_CLOCK'])
clock.write_text(str(int(clock.read_text()) + round(float(sys.argv[1])*1000)))
''')
            for executable in (node, penguin, sleeper):
                executable.chmod(0o755)
            env = dict(os.environ, PENGUIN_HOME=str(home), PB_PREFIX=str(prefix), PB_LOGS=str(logs),
                       PB_PROJECT="project", PB_AGENT="fixture", PB_PROVIDER="fixture", PB_MODEL="fixture",
                       PB_THINKING="max", PB_TIMEOUT="900s", PB_ABORT_WAIT="60", PB_MAX_TURNS="-1",
                       FIXTURE_CLOCK=str(clock), FIXTURE_CHECKS=str(root / "checks"),
                       FIXTURE_SERVER_PID=str(root / "pid"), FIXTURE_STOP_CLOCK=str(root / "stop-clock"),
                       PATH=str(prefix / "bin") + os.pathsep + os.environ["PATH"])
            result = subprocess.run(["bash", str(HELPER.with_name("run.sh"))], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertEqual(len((root / "checks").read_text().splitlines()), 30)
            self.assertEqual((root / "stop-clock").read_text(), "60000")
            outcome = json.loads((logs / "penguin-outcome.json").read_text())
            self.assertEqual(outcome["status"], "server_failed")
            self.assertFalse(outcome["server_ready"])
            self.assertTrue((logs / "penguin/server-stop.json").is_file())
            self.assertTrue((logs / "penguin/redaction.json").is_file())


if __name__ == "__main__":
    unittest.main()
