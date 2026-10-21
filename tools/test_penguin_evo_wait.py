"""Zero-model terminal waiting: actual helper, native API-shaped loopback receipts."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import unittest
import sys

import test_penguin_helper as base
HELPER, TOKEN = base.HELPER, base.TOKEN


class EvoWaitTests(unittest.TestCase):
    setUp = base.HelperReadinessTests.setUp
    stop_server = base.HelperReadinessTests.stop_server
    invoke = base.HelperReadinessTests.invoke
    session = staticmethod(base.HelperReadinessTests.session)
    listing = base.HelperReadinessTests.listing

    def profile(self, milliseconds=4000):
        self.agent = self.root / "project/agents/fixture"
        self.profile_path = self.agent / "agent_state/skills/evo-harness-rsi-toolkit/training.json"
        self.profile_path.parent.mkdir(parents=True)
        self.profile_path.write_text(json.dumps({"guard_version": 2, "attempt_id": "attempt-1",
            "train_ids": ["train_%03d" % i for i in range(1, 6)], "private_learning": str(self.root / "private")}))
        guard = self.agent / "agent_state/hooks/evo-harness-rsi-toolkit/guard.mjs"
        guard.parent.mkdir(parents=True)
        guard.write_text("// synthetic installed guard; no model")
        started = int(time.time()*1000) - (4000-milliseconds)
        result, plan = self.invoke("prepare-evo-wait", "project", "fixture", str(started), "4s")
        self.assertEqual(result.returncode, 0)
        self.plan_path = self.root / "plan.json"
        self.plan_path.write_text(json.dumps(plan))
        self.scratch = self.agent / "scratchpad/root"
        self.scratch.mkdir(parents=True)
        mode = {"schema": 2, "mode": "training", "session_id": "root",
            "training_path": str(self.profile_path), "training_sha256": plan["training_sha256"],
            "private_learning": str(self.root / "private")}
        (self.scratch / "EVO_CONTROL.json").write_text(json.dumps(mode))
        return plan

    def acceptance(self, complete=True):
        record = {"complete": True, "skill_sha256": "a"*64} if complete else {
            "complete": False, "status": "method_failed", "reason": TOKEN}
        (self.scratch / "evo-acceptance.json").write_text(json.dumps(record))

    def wait(self):
        result, body = self.invoke("wait-evo", "project", "fixture", "root", str(self.plan_path))
        self.assertTrue(all(method == "GET" for method, _, _ in self.requests))
        self.assertEqual(body["model_calls"], 0)
        self.assertEqual(body["task_requests"], 0)
        return result, body

    def test_native_optional_zero_background_is_success_only_with_terminal(self):
        self.profile()
        self.acceptance()
        self.responses = self.listing([self.session(), self.session("child")])
        result, body = self.wait()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(body["status"], "completed")
        self.assertTrue(body["native_sessions_idle"])

    def test_idle_without_acceptance_waits_across_notice_gap_and_native_followup(self):
        self.profile()
        self.responses = (self.listing([self.session(backgroundTasks={"processes": 0, "subagents": 1})]) +
            self.listing([self.session()]) + self.listing([self.session(status="running")]) +
            self.listing([self.session()])*8)
        timer = threading.Timer(.78, self.acceptance)
        timer.start()
        self.addCleanup(timer.join)
        result, body = self.wait()
        self.assertEqual(result.returncode, 0)
        self.assertGreaterEqual(body["snapshots"], 4)
        self.assertGreater(body["waited_ms"], 700)

    def test_complete_does_not_bypass_live_or_queued_work(self):
        self.profile()
        self.acceptance()
        self.responses = self.listing([self.session(backgroundTasks={"processes": 0, "subagents": 1})]) + self.listing([
            self.session(pendingFollowUpCount=1)]) + self.listing([self.session()])
        result, body = self.wait()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(body["snapshots"], 3)

    def test_terminal_failure_is_preserved_as_cleanup_boundary(self):
        self.profile()
        self.acceptance(False)
        self.responses = self.listing([self.session(backgroundTasks={"processes": 0, "subagents": 1})])
        result, body = self.wait()
        self.assertEqual(result.returncode, 5)
        self.assertEqual(body["status"], "method_failed")
        self.assertFalse(body["native_sessions_idle"])

    def test_guard_failure_marker_is_terminal_without_fake_acceptance(self):
        self.profile()
        (self.scratch / "evo-hook-blocked.json").write_text(json.dumps({"error": TOKEN}))
        self.responses = self.listing([self.session()])
        result, body = self.wait()
        self.assertEqual(result.returncode, 5)
        self.assertTrue(body["terminal"])

    def test_original_deadline_is_not_restarted_at_cli_return(self):
        plan = self.profile(milliseconds=400)
        self.responses = self.listing([self.session()])*20
        started = time.monotonic()
        result, body = self.wait()
        self.assertEqual(result.returncode, 124)
        self.assertEqual(body["deadline_ms"], plan["deadline_ms"])
        self.assertLess(time.monotonic()-started, .8)
        self.assertFalse(body["terminal"])

    def test_late_response_does_not_turn_deadline_into_success(self):
        self.profile(milliseconds=200)
        self.acceptance()
        self.response_delay = .6
        self.responses = self.listing([self.session()])
        result, body = self.wait()
        self.assertEqual(result.returncode, 124)
        self.assertFalse(body["terminal"])

    def test_response_json_finishing_after_deadline_cannot_certify_success(self):
        plan = self.profile()
        self.acceptance()
        preload = self.root / "late-json.mjs"
        preload.write_text("let clock="+str(plan["started_at_ms"])+"; Date.now=()=>clock;\n" +
            "globalThis.fetch=async(url)=>({ok:true,status:200,json:async()=>{\n" +
            "if(new URL(url).pathname.endsWith('/agents'))return {agents:[{agentId:'fixture'}]};\n" +
            "clock="+str(plan["deadline_ms"])+";return " + json.dumps({"sessions": [self.session()]}) + ";}});\n")
        result = subprocess.run(["node", "--import", str(preload), str(HELPER), "wait-evo", str(self.root),
            "project", "fixture", "root", str(self.plan_path)], capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 124)
        self.assertFalse(json.loads(result.stdout)["terminal"])
        self.assertEqual(self.requests, [])

    def test_subsecond_window_never_uses_zero_second_cli_soft_timeout(self):
        self.profile(milliseconds=600)
        result = subprocess.run(["node", str(HELPER), "evo-wait-window", str(self.plan_path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        hard, soft = result.stdout.split()
        self.assertGreater(float(hard[:-1]), 0)
        self.assertLess(float(hard[:-1]), 1)
        self.assertEqual(soft, "1s")

    def test_profile_mutation_and_foreign_marker_fail_without_dispatch(self):
        for mutate in ("profile", "marker"):
            with self.subTest(mutate=mutate):
                if not hasattr(self, "profile_path"): self.profile()
                self.acceptance()
                path = self.profile_path if mutate == "profile" else self.scratch / "EVO_CONTROL.json"
                before = path.read_bytes()
                value = json.loads(before)
                value["session_id"] = "foreign"
                path.write_text(json.dumps(value))
                result, body = self.wait()
                self.assertEqual(result.returncode, 6)
                self.assertFalse(body["terminal"])
                path.write_bytes(before)
                self.assertEqual(self.requests, [])

    def test_symlink_terminal_and_malformed_native_counts_are_rejected(self):
        self.profile()
        outside = self.root / "foreign.json"
        outside.write_text(json.dumps({"complete": True, "skill_sha256": "a"*64}))
        path = self.scratch / "evo-acceptance.json"
        path.symlink_to(outside)
        result, body = self.wait()
        self.assertEqual(result.returncode, 6)
        self.assertEqual(self.requests, [])
        path.unlink()
        self.acceptance()
        for bg in (None, {"processes": -1, "subagents": 0}, {"processes": True, "subagents": 0}):
            with self.subTest(background=bg):
                self.responses = self.listing([self.session(backgroundTasks=bg)])
                result, body = self.wait()
                self.assertEqual(result.returncode, 6)
                self.assertFalse(body["terminal"])

    def test_unbounded_or_wrong_guard_profile_cannot_arm_wait(self):
        self.profile()
        for duration in ("", "unlimited", "0s", "nan"):
            result, body = self.invoke("prepare-evo-wait", "project", "fixture", str(int(time.time()*1000)), duration)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(body["prepared"])
        p = json.loads(self.profile_path.read_text())
        p["guard_version"] = 1
        self.profile_path.write_text(json.dumps(p))
        result, body = self.invoke("prepare-evo-wait", "project", "fixture", str(int(time.time()*1000)), "4s")
        self.assertEqual(result.returncode, 2)

    def test_native_idle_root_abort_is_failure_without_terminal_receipt(self):
        self.profile()
        trace = self.agent / "traces/2026-10-21/root_001.jsonl"
        trace.parent.mkdir(parents=True)
        rows = [{"type": "session_meta", "payload": {"session_id": "root", "agent_state": str(self.agent / "agent_state")}},
                {"type": "event_msg", "payload": {"type": "request_begin"}},
                {"type": "event_msg", "payload": {"type": "abort", "error_code": "user_abort"}}]
        trace.write_text("".join(json.dumps(row)+"\n" for row in rows))
        self.responses = self.listing([self.session()])
        result, body = self.wait()
        self.assertEqual(result.returncode, 5)
        self.assertTrue(body["native_abort"])

    def test_native_root_abort_exits_while_independent_child_is_still_live(self):
        self.profile()
        trace = self.agent / "traces/2026-10-21/root_001.jsonl"
        trace.parent.mkdir(parents=True)
        rows = [{"type": "session_meta", "payload": {"session_id": "root", "agent_state": str(self.agent / "agent_state")}},
                {"type": "event_msg", "payload": {"type": "request_begin"}},
                {"type": "event_msg", "payload": {"type": "abort", "error_code": "user_abort"}}]
        trace.write_text("".join(json.dumps(row)+"\n" for row in rows))
        self.responses = self.listing([self.session(backgroundTasks={"processes": 0, "subagents": 1}),
                                      self.session("child", status="running")])
        result, body = self.wait()
        self.assertEqual(result.returncode, 5)
        self.assertTrue(body["native_abort"])
        self.assertFalse(body["native_sessions_idle"])
        self.assertEqual(body["snapshots"], 1)

    def test_running_or_queued_root_cannot_be_failed_from_an_old_abort(self):
        self.profile(milliseconds=750)
        trace = self.agent / "traces/2026-10-21/root_001.jsonl"
        trace.parent.mkdir(parents=True)
        rows = [{"type": "session_meta", "payload": {"session_id": "root", "agent_state": str(self.agent / "agent_state")}},
                {"type": "event_msg", "payload": {"type": "request_begin"}},
                {"type": "event_msg", "payload": {"type": "abort", "error_code": "user_abort"}}]
        trace.write_text("".join(json.dumps(row)+"\n" for row in rows))
        self.responses = (self.listing([self.session(status="running")]) +
                          self.listing([self.session(pendingFollowUpCount=1)]))*10
        result, body = self.wait()
        self.assertEqual(result.returncode, 124)
        self.assertNotIn("native_abort", body)

    def test_foreign_origin_or_old_abort_cannot_terminate_a_new_task(self):
        self.profile(milliseconds=1000)
        trace = self.agent / "traces/2026-10-21/root_001.jsonl"
        trace.parent.mkdir(parents=True)
        rows = [{"type": "session_meta", "payload": {"session_id": "root", "agent_state": str(self.agent / "agent_state")}},
                {"type": "event_msg", "payload": {"type": "abort"}},
                {"type": "event_msg", "payload": {"type": "request_begin"}},
                {"type": "event_msg", "origin": ["child"], "payload": {"type": "abort"}}]
        trace.write_text("".join(json.dumps(row)+"\n" for row in rows))
        self.responses = self.listing([self.session()])*20
        result, body = self.wait()
        self.assertEqual(result.returncode, 124)
        self.assertNotIn("native_abort", body)

    def shell(self, *, terminal=True, duration="5s", slow_cli=False, missing_timeout=False):
        self.profile()
        prefix = self.root / "prefix"
        (prefix / "node/bin").mkdir(parents=True)
        (prefix / "bin").mkdir()
        (prefix / "node/bin/node").symlink_to(shutil.which("node"))
        shutil.copyfile(HELPER, prefix / "helper.mjs")
        (prefix / "instruction.md").write_text("offline adapter lifecycle fixture")
        wrapper = prefix / "bin/penguin"
        wrapper.write_text("#!" + sys.executable + "\n" + '''
import json,os,signal,sys,time
from pathlib import Path
root=Path(os.environ['WAIT_FIXTURE_ROOT']); args=sys.argv[1:]
if args==['server']:
    (root/'server-pid').write_text(str(os.getpid())); time.sleep(60)
elif args[:2]==['server','stop']:
    if (root/'server-pid').exists():
        try:os.kill(int((root/'server-pid').read_text()),signal.SIGTERM)
        except ProcessLookupError:pass
    print('{"ok":true}')
elif args==['run','--help']: print('synthetic run command')
elif args[:1]==['run']:
    with (root/'calls.jsonl').open('a') as f:f.write(json.dumps(args)+'\\n')
    if os.environ.get('WAIT_SLOW')=='1':time.sleep(5)
    print('{"status":"completed","sessionId":"root"}')
elif args[:1]==['cost']:print('{"groups":[]}')
else:raise SystemExit(2)
''')
        wrapper.chmod(0o755)
        if missing_timeout:
            timeout = prefix / "bin/timeout"
            timeout.write_text("#!/bin/sh\nprintf 'not GNU timeout\\n'\n")
            timeout.chmod(0o755)
        state_dir = str(self.agent / "agent_state")
        self.responses = [(200, {"projects": [{"projectId": "project"}]}),
                          (200, {"stateDir": state_dir, "config": {"maxTurns": -1}})] + self.listing([self.session()])*30
        timer = None
        if not slow_cli and not missing_timeout:
            timer = threading.Timer(1.2, lambda: self.acceptance(terminal))
            timer.start()
        env = {k:v for k,v in os.environ.items() if k.upper() not in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NODE_OPTIONS")}
        env.update(PATH=str(prefix / "bin") + os.pathsep + os.environ["PATH"],
            PENGUIN_HOME=str(self.root), PB_PREFIX=str(prefix), PB_LOGS=str(self.root / "logs"),
            PB_PROJECT="project", PB_AGENT="fixture", PB_PROVIDER="synthetic", PB_MODEL="no-model", PB_THINKING="high",
            PB_TIMEOUT=duration, PB_ABORT_WAIT="2", PB_MAX_TURNS="", WAIT_FIXTURE_ROOT=str(self.root), WAIT_SLOW=str(int(slow_cli)))
        started = time.monotonic()
        try:
            result = subprocess.run(["bash", str(HELPER.with_name("run.sh"))], cwd=self.root, env=env,
                                    capture_output=True, text=True, timeout=15)
        finally:
            if timer: timer.join()
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        receipt = json.loads((self.root / "logs/penguin-outcome.json").read_text())
        calls_path = self.root / "calls.jsonl"
        calls = [json.loads(line) for line in calls_path.read_text().splitlines()] if calls_path.exists() else []
        self.assertTrue(all(method == "GET" for method, _, _ in self.requests))
        return result, receipt, calls, time.monotonic()-started

    def test_actual_shell_runs_one_cli_and_waits_for_terminal_before_cleanup(self):
        result, receipt, calls, elapsed = self.shell()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)
        wait = json.loads((self.root / "logs/penguin/evo-native-wait.json").read_text())
        self.assertEqual(wait["status"], "completed")
        self.assertGreater(wait["snapshots"], 1)
        self.assertGreater(elapsed, 1)
        self.assertEqual(receipt["status"], "completed")
        self.assertTrue(receipt["native_sessions_idle"])

    def test_actual_shell_preserves_failed_terminal_and_cost_collection(self):
        result, receipt, calls, _ = self.shell(terminal=False)
        self.assertEqual(result.returncode, 5)
        self.assertEqual(receipt["status"], "method_failed")
        self.assertEqual(receipt["cost_status"], "observed_after_native_idle")
        self.assertEqual(len(calls), 1)

    def test_actual_shell_hard_window_bounds_cli_without_restarting_timeout(self):
        result, receipt, calls, elapsed = self.shell(duration="1s", slow_cli=True)
        self.assertEqual(len(calls), 1)
        self.assertTrue(receipt["timed_out"])
        self.assertEqual(receipt["status"], "running")
        self.assertLess(elapsed, 4)
        self.assertFalse((self.root / "logs/penguin/evo-native-wait.json").exists())

    def test_missing_gnu_timeout_blocks_before_any_task(self):
        result, receipt, calls, _ = self.shell(missing_timeout=True)
        self.assertEqual(calls, [])
        self.assertEqual(result.returncode, 4)
        self.assertEqual(receipt["status"], "config_failed")


if __name__ == "__main__": unittest.main()
