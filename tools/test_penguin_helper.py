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
        self.request_bodies = []
        self.responses = []
        self.response_delay = 0
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self):
                test.requests.append((self.command, self.path, self.headers.get("Authorization")))
                if int(self.headers.get("Content-Length", "0")):
                    test.request_bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
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

    def target(self, agent="fixture"):
        return {"stateDir": str(self.root / "project/agents" / agent / "agent_state"), "config": {"maxTurns": -1}}

    def target_ready(self, deadline=None):
        return self.invoke("ready", str(deadline or int(time.time()*1000)+3000), "project", "fixture")

    def test_target_readiness_waits_for_project_then_agent_without_mutation(self):
        self.responses = [(200, {"projects": []})]
        result, body = self.target_ready()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(body["ready"])
        self.assertEqual(body["stage"], "projects")
        self.responses = [(200, {"projects": [{"projectId": "project"}]}), (404, {"error": TOKEN})]
        result, body = self.target_ready()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(body["http_status"], 404)
        self.responses = [(200, {"projects": [{"projectId": "project"}]}), (200, self.target())]
        result, body = self.target_ready()
        self.assertEqual(result.returncode, 0)
        self.assertTrue(body["ready"])
        self.assertTrue(all(method == "GET" for method, _, _ in self.requests))

    def test_target_readiness_rejects_wrong_state_and_malformed_inventory(self):
        for responses in [[(200, {"projects": [{"projectId": "project"}]}), (200, self.target("wrong"))],
                          [(200, {"projects": [None]})]]:
            with self.subTest(responses=responses):
                self.responses = responses
                result, body = self.target_ready()
                self.assertEqual(result.returncode, 2)
                self.assertFalse(body["ready"])
                self.assertFalse(body["retryable"])

    def test_target_auth_is_read_only_and_token_is_refreshed_per_attempt(self):
        self.responses = [(401, TOKEN)]
        result, body = self.target_ready()
        self.assertEqual(result.returncode, 1)
        self.assertTrue(body["retryable"])
        (self.root / "api-token").write_text(TOKEN + "_ROTATED")
        self.responses = [(403, TOKEN)]
        result, body = self.target_ready()
        self.assertEqual(result.returncode, 2)
        self.assertFalse(body["retryable"])
        self.assertEqual([request[2] for request in self.requests], ["Bearer " + TOKEN, "Bearer " + TOKEN + "_ROTATED"])
        self.assertTrue(all(method == "GET" for method, _, _ in self.requests))

    def test_target_second_get_uses_remaining_deadline(self):
        # A synthetic clock checks the exact budget, independent of process
        # startup or concurrent build load on the host.
        preload = self.root/"remaining-budget.mjs"
        record = self.root/"remaining-budget.json"
        preload.write_text("import fs from 'node:fs'; let clock=1000; const budgets=[]; Date.now=()=>clock;\n" +
            "AbortSignal.timeout=(ms)=>{budgets.push(ms); return new AbortController().signal;};\n" +
            "globalThis.fetch=async(url)=>({status:200,json:async()=>{\n" +
            "if(new URL(url).pathname==='/api/projects'){clock=1250;return {projects:[{projectId:'project'}]};}\n" +
            "fs.writeFileSync(" + json.dumps(str(record)) + ",JSON.stringify(budgets)); return " + json.dumps(self.target()) + ";}});\n")
        result = subprocess.run(["node", "--import", str(preload), str(HELPER), "ready", str(self.root),
                                 "1450", "project", "fixture"], capture_output=True,text=True,timeout=3)
        self.assertEqual(result.returncode,0)
        self.assertTrue(json.loads(result.stdout)['ready'])
        self.assertEqual(json.loads(record.read_text()),[450,200])
        self.assertEqual(self.requests,[])
        self.assertNotIn(TOKEN,result.stdout+result.stderr)

    def test_target_config_body_finishing_after_deadline_is_not_ready(self):
        preload = self.root/"slow-body.mjs"
        preload.write_text("let clock=1000; Date.now=()=>clock;\n" +
            "globalThis.fetch=async(url)=>({status:200,json:async()=>{\n" +
            "if(new URL(url).pathname==='/api/projects')return {projects:[{projectId:'project'}]};\n" +
            "clock=2001; return " + json.dumps(self.target()) + ";}});\n")
        result = subprocess.run(["node", "--import", str(preload), str(HELPER), "ready", str(self.root),
                                 "2000", "project", "fixture"], capture_output=True,text=True,timeout=3)
        self.assertEqual(result.returncode,1)
        self.assertFalse(json.loads(result.stdout)['ready'])
        self.assertEqual(self.requests,[])
        self.assertNotIn(TOKEN,result.stdout+result.stderr)

    def fresh_state(self, prepared=False, role="target"):
        view = self.target()
        state = Path(view["stateDir"])
        state.mkdir(parents=True, exist_ok=True)
        (state/"system_config.yaml").write_text("version: 1\nmax_turns: -1\n")
        view["config"] = {"version": 1, "maxTurns": -1 if prepared else 32,
            "memory": {"enabled": not prepared}, "hooks": {"enabled": role == "trainer" if prepared else True},
            "toolsBuiltin": [{"name":"exec_command", "maxOutputLength":104192 if prepared else 30000, "extra":"preserved"},
                {"name":"input_command", "maxOutputLength":120000}, {"name":"read_file", "limit":2000}]}
        return view

    def test_native_preparation_preserves_whole_tool_array_and_reads_back(self):
        before, after = self.fresh_state(), self.fresh_state(True)
        self.responses = [(200,{"sessions":[]}), (200,before), (200,{}), (200,after)]
        result, body = self.invoke("prepare-evo-state", "project", "fixture", "target")
        self.assertEqual(result.returncode,0)
        self.assertTrue(body["complete"])
        self.assertEqual((body["model_calls"],body["task_requests"]),(0,0))
        self.assertEqual([r[0] for r in self.requests],["GET","GET","PUT","GET"])
        self.assertEqual(self.request_bodies,[{"config":{key:after["config"][key]
            for key in ("maxTurns","memory","hooks","toolsBuiltin")}}])
        self.assertEqual(body["native_tool_count"],3)
        self.assertEqual(len(body["config_sha256"]),64)

    def test_prepared_fresh_target_and_trainer_are_noop(self):
        for role in ("target","trainer"):
            with self.subTest(role=role):
                view = self.fresh_state(True,role)
                self.requests=[]; self.request_bodies=[]
                self.responses=[(200,{"sessions":[]}),(200,view),(200,view)]
                result, body=self.invoke("prepare-evo-state","project","fixture",role)
                self.assertEqual(result.returncode,0)
                self.assertFalse(body["changed"])
                self.assertEqual(body["hooks_enabled"],role == "trainer")
                self.assertTrue(all(r[0]=="GET" for r in self.requests))
                self.assertEqual(self.request_bodies,[])

    def test_preparation_refuses_existing_snapshot_before_http(self):
        view=self.fresh_state(); snapshot=Path(view["stateDir"]).parent/"snapshots"/"1.tar.gz"
        snapshot.parent.mkdir(); snapshot.write_bytes(b"SYNTHETIC immutable evidence")
        result,body=self.invoke("prepare-evo-state","project","fixture","target")
        self.assertEqual(result.returncode,1)
        self.assertEqual(body["reason"],"existing_agent_evidence_cannot_be_reconfigured")
        self.assertFalse(body["retryable"])
        self.assertEqual(self.requests,[])
        self.assertEqual(snapshot.read_bytes(),b"SYNTHETIC immutable evidence")

    def test_preparation_refuses_sessions_or_nonfresh_or_incomplete_configuration(self):
        for failure in ("session","version","tools"):
            with self.subTest(failure=failure):
                self.requests=[]; self.request_bodies=[]
                view=self.fresh_state()
                if failure=="version":view["config"]["version"]=2
                if failure=="tools":view["config"]["toolsBuiltin"].pop(0)
                self.responses=[(200,{"sessions":[{"sessionId":"prior"}] if failure=="session" else []}),(200,view)]
                result,body=self.invoke("prepare-evo-state","project","fixture","target")
                self.assertEqual(result.returncode,1)
                self.assertFalse(body["complete"])
                self.assertIn(body["reason"],("fresh_agent_required","native_fresh_state_required","complete_native_tool_configuration_required"))
                self.assertTrue(all(r[0]=="GET" for r in self.requests))
                self.assertEqual(self.request_bodies,[])

    def test_preparation_never_retries_failed_put_or_accepts_unverified_update(self):
        for failure in ("put","readback"):
            with self.subTest(failure=failure):
                self.requests=[];self.request_bodies=[]
                before=self.fresh_state()
                self.responses=[(200,{"sessions":[]}),(200,before)]
                self.responses += [(503,TOKEN)] if failure=="put" else [(200,{}),(200,before)]
                result,body=self.invoke("prepare-evo-state","project","fixture","target")
                self.assertEqual(result.returncode,1)
                self.assertFalse(body["complete"])
                self.assertEqual(sum(r[0]=="PUT" for r in self.requests),1)
                self.assertEqual(body["reason"],"preparation_update_failed" if failure=="put" else "preparation_mismatch")

    def test_config_update_is_one_put_and_successful_readback(self):
        self.responses = [(200, {"config": {"maxTurns": 32}}), (200, {}), (200, {"config": {"maxTurns": -1}})]
        result, body = self.invoke("set-max-turns", "synthetic", "fixture", "-1")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(body["max_turns"], -1)
        self.assertEqual([method for method, _, _ in self.requests], ["GET", "PUT", "GET"])
        self.assertTrue(all(auth == "Bearer " + TOKEN for _, _, auth in self.requests))

    def test_matching_stock_turn_cap_never_puts(self):
        self.responses = [(200, {"config": {"maxTurns": -1}})]
        result, body = self.invoke("set-max-turns", "synthetic", "fixture", "-1")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(body["changed"])
        self.assertEqual(body["action"], "unchanged")
        self.assertEqual([method for method, _, _ in self.requests], ["GET"])

    def test_frozen_evo_state_verifies_without_writing_or_refuses_mismatch(self):
        state = self.root / "project/agents/fixture/agent_state"
        marker = state / "skills/evo-harness-learned/SKILL.md"
        marker.parent.mkdir(parents=True)
        marker.write_text("SYNTHETIC frozen Skill marker")
        yaml = state / "system_config.yaml"
        original = b"# SYNTHETIC exact frozen formatting\nmax_turns: -1\n"
        yaml.write_bytes(original)
        for current, exit_code in ((-1, 0), (32, 1)):
            with self.subTest(current=current):
                self.requests.clear()
                self.responses = [(200, {"stateDir": str(state), "config": {"maxTurns": current}})]
                result, body = self.invoke("set-max-turns", "project", "fixture", "-1")
                self.assertEqual(result.returncode, exit_code)
                self.assertTrue(body["frozen_state"])
                self.assertFalse(body["changed"])
                self.assertEqual(body["config_sha256_before"], body["config_sha256_after"])
                self.assertEqual(yaml.read_bytes(), original)
                self.assertEqual([method for method, _, _ in self.requests], ["GET"])
                if exit_code:
                    self.assertEqual(body["error"], "frozen_state_max_turns_mismatch_prepare_before_snapshot")

    def test_unreadable_or_invalid_native_config_never_puts(self):
        for response in ((503, "starting"), (200, {"config": {}}), (200, {"config": {"maxTurns": True}})):
            with self.subTest(response=response):
                self.requests.clear()
                self.responses = [response]
                result, body = self.invoke("set-max-turns", "project", "fixture", "-1")
                self.assertEqual(result.returncode, 1)
                self.assertFalse(body["changed"])
                self.assertEqual([method for method, _, _ in self.requests], ["GET"])

    def test_failed_config_put_is_never_retried_or_accepted_by_readback(self):
        for status in (401, 403, 404, 503):
            with self.subTest(status=status):
                self.requests.clear()
                self.responses = [(200, {"config": {"maxTurns": 32}}), (status, "penguin-server is starting")]
                result, body = self.invoke("set-max-turns", "synthetic", "fixture", "-1")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(body["put_status"], status)
                self.assertEqual(body["max_turns"], 32)
                self.assertEqual([method for method, _, _ in self.requests], ["GET", "PUT"])

    def test_real_adapter_shell_waits_read_only_then_skips_matching_put_and_submits_once(self):
        # The real shell and real helper talk to a synthetic local API. The CLI
        # Task is a fixture only, so no Session or model request can be created.
        self.responses = [(200, {"projects": []}),
            (200, {"projects": [{"projectId": "project"}]}), (404, {}),
            (200, {"projects": [{"projectId": "project"}]}), (200, self.target()),
            (200, self.target()),
            (200, {"agents": [{"agentId": "fixture"}]}), (200, {"sessions": [self.session()]})]
        result, events = self.adapter_shell()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, ["task", "stop"])
        self.assertEqual([m for m, _, _ in self.requests], ["GET"]*8)
        outcome = json.loads((self.root/"logs/penguin-outcome.json").read_text())
        self.assertTrue(outcome["server_ready"])
        self.assertEqual(outcome["status"], "completed")

    def test_real_adapter_shell_forbidden_target_never_updates_or_submits(self):
        self.responses = [(403, TOKEN)]
        result, events = self.adapter_shell()
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(events, ["stop"])
        self.assertEqual([m for m, _, _ in self.requests], ["GET"])
        private_log = self.root/"logs/penguin/server.log"
        self.assertIn(TOKEN, private_log.read_text())
        self.assertEqual(private_log.stat().st_mode & 0o777, 0o600)

    def adapter_shell(self):
        prefix = self.root/"prefix"
        (prefix/"node/bin").mkdir(parents=True)
        (prefix/"bin").mkdir()
        (prefix/"node/bin/node").symlink_to(shutil.which("node"))
        shutil.copyfile(HELPER, prefix/"helper.mjs")
        (prefix/"instruction.md").write_text("SYNTHETIC no model or real task")
        state = self.root/"project/agents/fixture/agent_state"
        state.mkdir(parents=True)
        (state/"system_config.yaml").write_text("max_turns: -1\n")
        penguin = prefix/"bin/penguin"
        penguin.write_text("#!" + sys.executable + "\n" + '''
import json, os, signal, sys, time
from pathlib import Path
home = Path(os.environ['PENGUIN_HOME'])
pid = home/'fixture-server.pid'
def event(name):
    with (home/'fixture-events').open('a') as file: file.write(name+'\\n')
if sys.argv[1:] == ['server']:
    pid.write_text(str(os.getpid()))
    print(os.environ['FIXTURE_PRIVATE_LOG'], flush=True)
    time.sleep(60)
elif sys.argv[1:3] == ['server', 'stop']:
    event('stop')
    if pid.exists():
        try: os.kill(int(pid.read_text()), signal.SIGTERM)
        except ProcessLookupError: pass
    print('{"ok":true}')
elif sys.argv[1:3] == ['run', '--help']: print('SYNTHETIC no-source')
elif sys.argv[1] == 'run':
    event('task')
    print('{"status":"completed","sessionId":"root"}')
elif sys.argv[1] == 'cost': print('{"groups":[]}')
else: raise SystemExit('unexpected fixture CLI command')
''')
        penguin.chmod(0o755)
        env = {k:v for k,v in os.environ.items()
               if k.upper() not in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NODE_OPTIONS")}
        env.update(PENGUIN_HOME=str(self.root), PB_PREFIX=str(prefix), PB_LOGS=str(self.root/"logs"),
                   PB_PROJECT="project", PB_AGENT="fixture", PB_PROVIDER="synthetic", PB_MODEL="no-model",
                   PB_THINKING="high", PB_TIMEOUT="5s", PB_ABORT_WAIT="2", PB_MAX_TURNS="-1",
                   FIXTURE_PRIVATE_LOG=TOKEN)
        result = subprocess.run(["bash", str(HELPER.with_name("run.sh"))], cwd=self.root, env=env,
                                capture_output=True, text=True, timeout=15)
        self.assertNotIn(TOKEN, result.stdout+result.stderr)
        return result, (self.root/"fixture-events").read_text().splitlines()

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
    assert args[2:] == ['project', 'fixture'], args
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
                expected_code = 7 if cost_failure == 'stop' else code
                self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
                outcome = json.loads((logs / "penguin-outcome.json").read_text())
                self.assertEqual(outcome["native_sessions_idle"], code != 6)
                self.assertEqual(outcome["status"], "method_failed_cleanup" if cost_failure == 'stop' else {
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


@unittest.skipUnless(shutil.which("node"), "Node runtime required")
class StateFileEvidenceTests(unittest.TestCase):
    def test_real_node_state_inventory_hashes_files_without_opening_secrets(self):
        import hashlib
        with tempfile.TemporaryDirectory(prefix="synthetic-state-inventory-") as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("SYNTHETIC fixed reader\n")
            (root / ".vault.toml").write_text(TOKEN)
            (root / "memory").mkdir()
            (root / "memory/hidden.txt").write_text(TOKEN)
            result = subprocess.run(["node", str(HELPER), "state-files", str(root)], capture_output=True, text=True, timeout=3)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(TOKEN, result.stdout)
            self.assertEqual(json.loads(result.stdout)["files"], {
                "AGENTS.md": hashlib.sha256((root / "AGENTS.md").read_bytes()).hexdigest()})

    def test_state_inventory_fails_closed_on_links_without_leaking_contents(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-state-inventory-link-") as tmp:
            root = Path(tmp)
            (root / "linked.txt").symlink_to("/etc/hosts")
            result = subprocess.run(["node", str(HELPER), "state-files", str(root)], capture_output=True, text=True, timeout=3)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads(result.stdout)["complete"])


if __name__ == "__main__":
    unittest.main()
