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
            do_POST = respond
            do_DELETE = respond

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

    @staticmethod
    def session(session_id="root", **extra):
        return {"sessionId": session_id, "projectId": "project", "agentId": "fixture",
                "status": "idle", "pendingFollowUpCount": 0, **extra}

    def listing(self, sessions):
        return [(200, {"agents": [{"agentId": "fixture"}]}), (200, {"sessions": sessions})]

    def test_idle_root_and_children_allow_cost_collection(self):
        self.responses = self.listing([self.session(), self.session("child")])
        result, body = self.invoke("quiesce", "project", "root", "2")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(body["native_sessions_idle"])
        self.assertFalse(body["had_active"])
        self.assertEqual(body["sessions_checked"], 2)
        self.assertEqual(body["abort_requests"], 0)
        self.assertTrue(all(method == "GET" for method, _, _ in self.requests))
        self.assertNotIn("?", self.requests[-1][1])  # Filtered native lists omit children.

    def test_idle_child_row_does_not_hide_running_child_in_parent_runtime(self):
        active = self.session(backgroundTasks={"processes": 0, "subagents": 1})
        event = {"type": "task_state", "state": "idle", "subagents": [
            {"sessionId": "child", "subagentId": "subagent-child", "running": True}]}
        self.responses = self.listing([active, self.session("child")]) + [
            (200, "event: server_event\ndata: " + json.dumps(event) + "\n\n"), (202, ""),
        ] + self.listing([self.session(), self.session("child")])
        result, body = self.invoke("quiesce", "project", "root", "2")
        self.assertEqual(result.returncode, 5)  # Finishing after abort is still a method failure.
        self.assertTrue(body["native_sessions_idle"])
        self.assertTrue(body["had_active"])
        self.assertEqual(body["abort_requests"], 1)
        writes = [(method, url) for method, url, _ in self.requests if method != "GET"]
        self.assertEqual(writes, [("POST", "/api/sessions/root/subagents/child/abort")])

    def test_soft_timeout_aborts_root_and_child_within_one_cleanup_window(self):
        active = self.session(status="running", backgroundTasks={"processes": 0, "subagents": 1})
        event = {"type": "task_state", "state": "running", "subagents": [
            {"sessionId": "child", "running": True}]}
        self.responses = self.listing([active, self.session("child")]) + [
            (200, "data: " + json.dumps(event) + "\n\n"), (202, ""), (202, ""),
        ] + self.listing([self.session(), self.session("child")])
        result, body = self.invoke("quiesce", "project", "root", "2")
        self.assertEqual(result.returncode, 5)
        self.assertTrue(body["native_sessions_idle"])
        self.assertEqual(body["abort_requests"], 2)
        self.assertLess(body["waited_ms"], 2000)
        self.assertEqual([url for method, url, _ in self.requests if method == "POST"], [
            "/api/sessions/root/subagents/child/abort", "/api/sessions/root/abort"])

    def test_cleanup_deadline_retains_unknown_cost_without_relaunching(self):
        active = self.session(status="running")
        self.responses = self.listing([active]) + [(202, "")] + self.listing([active]) * 20
        result, body = self.invoke("quiesce", "project", "root", "1")
        self.assertEqual(result.returncode, 6)
        self.assertFalse(body["native_sessions_idle"])
        self.assertTrue(body["had_active"])
        self.assertLess(body["waited_ms"], 1500)
        self.assertEqual([url for method, url, _ in self.requests if method == "POST"], [
            "/api/sessions/root/abort"])

    def test_failed_status_lookup_never_counts_as_idle_or_exposes_body(self):
        self.responses = [(503, TOKEN)]
        result, body = self.invoke("quiesce", "project", "root", "2")
        self.assertEqual(result.returncode, 6)
        self.assertFalse(body["native_sessions_idle"])
        self.assertEqual(body["error"], "could_not_verify_all_trial_work_idle")

    def test_root_missing_from_unfiltered_list_does_not_count_as_idle(self):
        self.responses = self.listing([self.session("unrelated")])
        result, body = self.invoke("quiesce", "project", "root", "2")
        self.assertEqual(result.returncode, 6)
        self.assertFalse(body["native_sessions_idle"])

    def test_background_processes_are_stopped_through_native_api(self):
        active = self.session(backgroundTasks={"processes": 1, "subagents": 0})
        self.responses = self.listing([active]) + [
            (200, {"processes": [{"processId": "process-fixture", "running": True}]}), (204, ""),
        ] + self.listing([self.session()])
        result, body = self.invoke("quiesce", "project", "root", "2")
        self.assertEqual(result.returncode, 5)
        self.assertTrue(body["native_sessions_idle"])
        self.assertEqual([url for method, url, _ in self.requests if method == "POST"], [
            "/api/sessions/root/processes/process-fixture/kill"])


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
elif command == 'verify-stop': print('{"valid":true}')
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
    time.sleep(60)
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
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertEqual(len((root / "checks").read_text().splitlines()), 30)
            self.assertEqual((root / "stop-clock").read_text(), "60000")
            outcome = json.loads((logs / "penguin-outcome.json").read_text())
            self.assertEqual(outcome["status"], "server_failed")
            self.assertFalse(outcome["server_ready"])
            self.assertTrue((logs / "penguin/server-stop.json").is_file())
            self.assertTrue((logs / "penguin/redaction.json").is_file())

    def test_cost_is_read_only_after_idle_and_active_children_never_look_completed(self):
        for code, cost_failure in ((0, ""), (5, ""), (6, ""), (0, "nonzero"), (0, "invalid"), (0, "stop")):
            with self.subTest(quiescence_exit=code, cost_failure=cost_failure), tempfile.TemporaryDirectory(prefix="synthetic-penguin-cost-") as tmp:
                root = Path(tmp)
                prefix, home, logs = root / "prefix", root / "home", root / "logs"
                (prefix / "node/bin").mkdir(parents=True)
                (prefix / "bin").mkdir()
                home.mkdir()
                (prefix / "instruction.md").write_text("SYNTHETIC no-model fixture")
                node = prefix / "node/bin/node"
                node.write_text("#!" + sys.executable + "\n" + '''
import json, os, sys, time
from pathlib import Path
command, args = sys.argv[2], sys.argv[3:]
if command == 'now': print(int(time.time()*1000))
elif command == 'ready': print('{"ready":true,"http_status":200}')
elif command == 'run-status': print('completed root')
elif command == 'quiesce':
    assert args == [os.environ['PENGUIN_HOME'], 'project', 'root', '7']
    with Path(os.environ['FIXTURE_EVENTS']).open('a') as handle: handle.write('quiesce\\n')
    code = int(os.environ['FIXTURE_QUIESCE_CODE'])
    print(json.dumps({'native_sessions_idle':code != 6, 'had_active':code != 0}))
    sys.exit(code)
elif command == 'verify-cost':
    try:
        for file in args: assert isinstance(json.loads(Path(file).read_text())['groups'], list)
    except (ValueError, KeyError, AssertionError): sys.exit(1)
    print('{"valid":true}')
elif command == 'redact': print('{"redacted":0}')
elif command == 'verify-stop': print('{"valid":true}')
elif command == 'outcome': Path(args[0]).write_text(args[1])
else: raise SystemExit('unexpected helper command: ' + command)
''')
                penguin = prefix / "bin/penguin"
                penguin.write_text("#!" + sys.executable + "\n" + '''
import json, os, signal, sys, time
from pathlib import Path
pid = Path(os.environ['FIXTURE_SERVER_PID'])
if sys.argv[1:] == ['server']:
    pid.write_text(str(os.getpid()))
    time.sleep(60)
elif sys.argv[1:3] == ['server', 'stop']:
    with Path(os.environ['FIXTURE_EVENTS']).open('a') as handle: handle.write('server-stop\\n')
    if pid.exists():
        try: os.kill(int(pid.read_text()), signal.SIGTERM)
        except ProcessLookupError: pass
    print('{"stopped":true}')
    if os.environ.get('FIXTURE_COST_FAILURE') == 'stop': sys.exit(1)
elif sys.argv[1:3] == ['run', '--help']: print('SYNTHETIC no-source')
elif sys.argv[1] == 'run': print('{"status":"completed","sessionId":"root"}')
elif sys.argv[1] == 'cost':
    evidence = json.loads((Path(os.environ['PB_LOGS']) / 'penguin/quiescence.json').read_text())
    assert evidence['native_sessions_idle'] is True, 'cost before idle verification'
    with Path(os.environ['FIXTURE_EVENTS']).open('a') as handle: handle.write('cost\\n')
    failure = os.environ.get('FIXTURE_COST_FAILURE', '')
    print('not JSON' if failure == 'invalid' else '{"groups":[]}')
    if failure == 'nonzero': sys.exit(2)
else: raise SystemExit('unexpected CLI command: ' + repr(sys.argv))
''')
                for executable in (node, penguin):
                    executable.chmod(0o755)
                env = dict(os.environ, PENGUIN_HOME=str(home), PB_PREFIX=str(prefix), PB_LOGS=str(logs),
                           PB_PROJECT="project", PB_AGENT="fixture", PB_PROVIDER="fixture", PB_MODEL="fixture",
                           PB_THINKING="max", PB_TIMEOUT="900s", PB_ABORT_WAIT="7", PB_MAX_TURNS="",
                           FIXTURE_EVENTS=str(root / "events"), FIXTURE_SERVER_PID=str(root / "pid"),
                           FIXTURE_QUIESCE_CODE=str(code), FIXTURE_COST_FAILURE=cost_failure)
                result = subprocess.run(["bash", str(HELPER.with_name("run.sh"))], cwd=root, env=env,
                                        capture_output=True, text=True, timeout=10)
                expected_code = 7 if cost_failure == 'stop' else 6 if cost_failure else code
                self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
                outcome = json.loads((logs / "penguin-outcome.json").read_text())
                self.assertEqual(outcome["native_sessions_idle"], code != 6)
                self.assertEqual(outcome["status"], "method_failed_cleanup" if cost_failure == 'stop' else "method_failed_unsettled" if cost_failure else {
                    0: "completed", 5: "method_failed_active_work", 6: "method_failed_unsettled"}[code])
                events = (root / "events").read_text().splitlines()
                if code == 6:
                    self.assertEqual(outcome["cost_status"], "unsettled")
                    self.assertEqual(events, ["quiesce", "server-stop"])
                    self.assertFalse((logs / "penguin-cost-by-session.json").exists())
                else:
                    self.assertEqual(outcome["cost_status"], "unsettled" if cost_failure else "observed_after_native_idle")
                    self.assertEqual(events, ["quiesce", "cost", "cost", "server-stop"])
                self.assertTrue((logs / "penguin/redaction.json").is_file())
                self.assertEqual(outcome["process_cleanup"], 'unconfirmed' if cost_failure == 'stop' else 'server_shutdown')


class CostResponseTests(unittest.TestCase):
    def test_malformed_or_failed_native_cost_response_is_never_valid(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-penguin-cost-json-") as tmp:
            root = Path(tmp)
            first, second = root / "session.json", root / "model.json"
            second.write_text('{"groups":[]}')
            for body, valid in [('not JSON', False), ('{"error":"failed"}', False),
                                ('{"groups":[null]}', False), ('{"groups":[]}', True),
                                ('{"groups":[{"cost":null,"hasUncosted":true}]}', True)]:
                with self.subTest(body=body):
                    first.write_text(body)
                    result = subprocess.run(["node", str(HELPER), "verify-cost", str(first), str(second)],
                                            capture_output=True, text=True, timeout=3)
                    self.assertEqual(result.returncode, 0 if valid else 1)
                    self.assertEqual(json.loads(result.stdout), {"valid": valid})

    def test_server_stop_receipt_requires_native_ok_true(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-penguin-stop-json-") as tmp:
            file = Path(tmp) / 'stop.json'
            for body, valid in [('not JSON', False), ('{"stopped":true}', False),
                                ('{"ok":false}', False), ('{"ok":true}', True)]:
                with self.subTest(body=body):
                    file.write_text(body)
                    result = subprocess.run(["node", str(HELPER), "verify-stop", str(file)],
                                            capture_output=True, text=True, timeout=3)
                    self.assertEqual(result.returncode, 0 if valid else 1)
                    self.assertEqual(json.loads(result.stdout), {"valid": valid})


if __name__ == "__main__":
    unittest.main()
