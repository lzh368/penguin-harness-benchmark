"""SYNTHETIC original trial/trace injection evidence, never performance results."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest

import acceptance
import protocol
import test_registration
import test_protocol


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n")
    return path


def write_trial(benchmark, cell, frozen, runtime, selected=None):
    """A real disk fixture of the adapter's retained files; no audit mocks."""
    name = cell["session_id"].removeprefix("harbor:")
    trial = Path(benchmark) / ".jobs/synthetic-job" / name
    state = "/synthetic-container/home/default_project/agents/default_agent/agent_state"
    session = "synthetic-native-" + name
    launch = protocol.read(Path(benchmark) / cell["case_id"] / "statement/harbor-launch.json")
    argv = launch["argv_template"]
    options = dict(argv[i + 1].split("=", 1) for i, value in enumerate(argv) if value == "--ak")
    options["agent_state_tar"] = str(Path(benchmark) / ".jobs" / (trial.parent.name + ".agent-state.tar.gz"))
    save(trial / "agent/penguin-install.json", {"agent_state": "custom", "install": "bundle",
        "node_version": options["node_version"], "penguin_version": options["penguin_version"], "bundle_sha256": launch["bundle_sha256"]})
    install = {"schema": "penguin-agent-state-files-v1", "state": state,
               "files": {key: hashlib.sha256(raw).hexdigest() for key, raw in frozen.items()}}
    for point in ("before", "ready", "after"):
        save(trial / f"agent/penguin-agent-state-{point}.json", install)
    save(trial / "result.json", {"task_name": launch["task_id"], "trial_name": trial.name,
        "task_id": {"path": argv[argv.index("-p") + 1]},
        "config": {"task": {"path": argv[argv.index("-p") + 1]}, "agent": {
            "name": "penguin_agent:PenguinAgent", "model_name": runtime["provider"] + "/" + runtime["model_id"], "kwargs": options,
            "override_timeout_sec": None, "override_setup_timeout_sec": None, "max_timeout_sec": None,
            "resume_trajectory": False, "load_trajectory": None, "extra_allowed_hosts": [runtime["api_host"]], "mcp_servers": [], "skills": []},
            "timeout_multiplier": 1.0, "agent_timeout_multiplier": None, "verifier_timeout_multiplier": None,
            "agent_setup_timeout_multiplier": 2.5, "install_only": False,
            "environment": {"type": "docker", "import_path": None, "delete": True, "extra_docker_compose": [],
                "extra_allowed_hosts": [], "kwargs": {}, "mounts": None, "override_cpus": None, "override_memory_mb": None,
                "override_storage_mb": None, "override_gpus": None, "override_tpu": None},
            "verifier": {"override_timeout_sec": None, "max_timeout_sec": None, "disable": False},
            "extra_instruction_paths": [], "extra_instructions": [], "source_trial": None},
        "agent_info": {"name": "penguin", "version": "0.2.16-synthetic", "model_info": {
            "name": runtime["model_id"], "provider": runtime["provider"]}},
        "exception_info": None, "finished_at": "2026-10-21T00:00:01Z",
        "agent_execution": {"started_at": "2026-10-21T00:00:00Z", "finished_at": "2026-10-21T00:00:00.001Z"},
        "agent_result": {"cost_usd": 0, "model_usage": {runtime["provider"] + "/" + runtime["model_id"]: {}}, "metadata": {
        "provider": runtime["provider"], "model_id": runtime["model_id"], "thinking": runtime["thinking_level"],
        "agent_state": "custom", "requests": 5, "native_sessions_idle": True,
        "process_cleanup": "server_shutdown", "cost_status": "observed_after_native_idle", "session_id": session, "status": "completed"}},
        "verifier_result": {"rewards": {"reward": cell["score"] / 100}}})
    meta = {"session_id": session, "source": "cli", "provider": runtime["provider"],
            "model_id": runtime["model_id"], "workspace": "/app", "agent_state": state}
    rows = [{"type": "session_meta", "payload": meta}, {"type": "event_msg", "payload": {"type": "request_begin"}}]
    def pair(key, tool, args, output):
        rows.extend([{"type": "model_msg", "payload": {"type": "tool_call", "name": tool,
            "arguments": json.dumps(args), "tool_call_id": key}}, {"type": "model_msg", "payload": {
            "type": "tool_call_output", "tool_call_id": key, "output": output}}])
    slot = acceptance.SLOT
    for name in ("SKILL.md", "index.json"):
        raw = frozen[slot + "/" + name]
        pair(name, "read_file", {"file_path": state + "/" + slot + "/" + name},
             "\n".join(f"{i}\t{line}" for i, line in enumerate(raw.decode().removesuffix("\n").split("\n"), 1)))
    index = json.loads(frozen[slot + "/index.json"])
    entries = {entry["id"]: entry for entry in index["entries"]}
    if selected is None:
        selected = list(entries)
    text = "\n\n".join(frozen[slot + "/" + entries[key]["file"]].decode() for key in selected)
    receipt = {"selected": selected, "entry_hashes": {key: entries[key]["sha256"] for key in selected},
               "text": text, "bytes": len(text.encode()), "sha256": hashlib.sha256(text.encode()).hexdigest()}
    pair("selector", "exec_command", {"cmd": "python3 -B select.py --ids '" + ",".join(selected) + "'",
         "workdir": state + "/" + slot}, json.dumps(receipt, ensure_ascii=False) + "\n[exit code 0]")
    trace = trial / "agent/penguin/traces/default_agent/root.jsonl"
    trace.parent.mkdir(parents=True)
    trace.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n")
    return trial, trace


class InjectionTests(unittest.TestCase):
    def setUp(self):
        registration = test_registration.RegistrationTests("test_native_roster_complete_launch_and_full_runtime")
        self.addCleanup(registration.doCleanups)
        registration.setUp()
        self.root = registration.root
        self.tool = os.environ["EVO_TOOL"]
        self.evo = protocol.helper(self.tool)
        self.runtime = registration.config["runtime"]
        self.config = registration.config
        self.registered = registration.run_prepare()
        self.library = save(self.root / "library.json", [{"id": "fixture-guide", "level": "general",
            "trigger": "synthetic", "scope": "synthetic", "body": "SYNTHETIC validate documented join keys."}])
        self.state = self.root / "state"
        self.evo.export_skill({"library": str(self.library), "limits": {"general": 5, "topic": 5},
            "max_entries": 5, "max_bytes": 16000, "output": str(self.state / acceptance.SLOT)})
        (self.state / "system_config.yaml").write_text("version: 1\n" + test_protocol.SYNTHETIC_OUTPUT_TOOLS)
        (self.state / "AGENTS.md").write_text("SYNTHETIC frozen reader instruction\n")
        self.snapshot = self.root / "snapshot.tar.gz"
        with tarfile.open(self.snapshot, "w:gz") as archive:
            archive.add(self.state, arcname="agent_state")
        self.frozen = acceptance.snapshot(self.snapshot)["files"]
        self.benchmark = registration.output / registration.config["test_benchmark_id"]
        self.cells = [{"protocol_version": 1, "agent_id": "synthetic-target", "expected_version": 1, **self.runtime,
                       "case_id": case, "run": 1, "status": "ok", "score": 50,
                       "cost": 0, "duration_ms": 1, "session_id": f"harbor:synthetic-test-{i}"}
                      for i, case in enumerate(self.registered["test"]["case_ids"], 1)]
        self.trials = [write_trial(self.benchmark, cell, self.frozen, self.runtime) for cell in self.cells]
        cells = save(self.root / "cells.json", self.cells)
        matrix = save(self.root / "matrix.json", {"evo_tool": self.tool, "cells": str(cells),
            "case_ids": [cell["case_id"] for cell in self.cells], "runs": 1,
            "identity": {"agent_id": "synthetic-target", "expected_version": 1, **self.runtime},
            "time": "2026-10-21T00:00:00Z", "summary_title": "SYNTHETIC", "summary": "fixture, not scores"})
        self.req = {"op": "verify-injection-matrix", "evo_tool": self.tool, "matrix_request": str(matrix),
            "snapshot": str(self.snapshot), "runtime": self.runtime, "expected_version": 1, "benchmark": str(self.benchmark),
            "config": str(registration.config_path), "config_sha256": protocol.sha(registration.config_path)}

    def test_complete_matrix_has_observed_guidance_and_no_test_content_receipt(self):
        result = protocol.execute(self.req)
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["cells"]), 5)
        self.assertTrue(all(cell["bytes"] > 0 and cell["selected"] == ["fixture-guide"] for cell in result["cells"]))
        self.assertNotIn("SYNTHETIC validate", json.dumps(result))

    def test_nonempty_library_may_legally_select_nothing(self):
        for cell, (trial, _) in zip(self.cells, self.trials):
            shutil.rmtree(trial)
            write_trial(self.benchmark, cell, self.frozen, self.runtime, selected=[])
        self.assertTrue(all(cell["bytes"] == 0 for cell in protocol.execute(self.req)["cells"]))

    def test_disk_install_substitution_and_in_test_state_edit_fail(self):
        for point in ("before", "after"):
            path = self.trials[0][0] / f"agent/penguin-agent-state-{point}.json"
            original = path.read_text()
            changed = json.loads(original)
            changed["files"][acceptance.SLOT + "/index.json"] = "0" * 64
            save(path, changed)
            with self.assertRaisesRegex(ValueError, "State (differs|changed)"):
                protocol.execute(self.req)
            path.write_text(original)

    def test_truncated_or_fabricated_selector_observation_fails_without_score_change(self):
        path = self.trials[0][1]
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        original = path.read_text()
        for mutation in ("truncated", "echo"):
            changed = copy.deepcopy(rows)
            if mutation == "truncated":
                changed[-1]["payload"]["output"] = '{"selected": ["fixture-guide"], ...truncated'
            else:
                changed[-2]["payload"]["arguments"] = json.dumps({"cmd": "echo fabricated-receipt"})
            path.write_text("\n".join(json.dumps(row) for row in changed))
            with self.assertRaises(ValueError):
                protocol.execute(self.req)
        path.write_text(original)
        self.assertEqual(protocol.matrix(protocol.read(self.req["matrix_request"]))["evaluation"]["score"], 50)

    def test_missing_reader_read_and_wrong_root_session_refused(self):
        path = self.trials[0][1]
        original = path.read_text()
        rows = [json.loads(line) for line in original.splitlines()]
        path.write_text("\n".join(json.dumps(row) for row in [*rows[:2], *rows[4:]]))
        with self.assertRaisesRegex(ValueError, "reader/index"):
            protocol.execute(self.req)
        rows[0]["payload"]["session_id"] = "other-session"
        path.write_text("\n".join(json.dumps(row) for row in rows))
        with self.assertRaisesRegex(ValueError, "Session trace"):
            protocol.execute(self.req)

    def test_incomplete_cleanup_cannot_be_called_verified_injection(self):
        path = self.trials[0][0] / "result.json"
        record = protocol.read(path)
        record["agent_result"]["metadata"]["process_cleanup"] = "unconfirmed"
        save(path, record)
        with self.assertRaisesRegex(ValueError, "cleanup"):
            protocol.execute(self.req)

    def test_unknown_cost_retains_valid_verifier_score_and_pending_liability(self):
        cells_path = protocol.read(self.req["matrix_request"])["cells"]
        cells = protocol.read(cells_path)
        cells[0]["cost"] = None
        save(cells_path, cells)
        path = self.trials[0][0] / "result.json"
        record = protocol.read(path)
        record["agent_result"]["cost_usd"] = None
        record["agent_result"]["metadata"]["cost_status"] = "unsettled"
        record["agent_result"]["metadata"].pop("requests")
        record["agent_result"].pop("model_usage")
        save(path, record)
        result = protocol.execute(self.req)
        self.assertTrue(result["complete"])
        self.assertEqual(result["cells"][0]["score"], 50)
        self.assertIsNone(result["cells"][0]["cost"])
        self.assertEqual(result["cells"][0]["cost_status"], "unsettled")

    def test_interleaved_selector_before_index_observation_is_not_a_full_read(self):
        path = self.trials[0][1]
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[5], rows[6] = rows[6], rows[5]
        path.write_text("\n".join(json.dumps(row) for row in rows))
        with self.assertRaisesRegex(ValueError, "observed before selection"):
            protocol.execute(self.req)

    def test_business_action_must_follow_complete_selector_observation(self):
        path = self.trials[0][1]
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        work = copy.deepcopy(rows[6:8])
        work[0]["payload"].update(name="write_file", tool_call_id="premature-answer",
            arguments=json.dumps({"file_path": "/app/submission/answer.json", "text": "{}"}))
        work[1]["payload"].update(tool_call_id="premature-answer", output="SYNTHETIC written")
        path.write_text("\n".join(json.dumps(row) for row in [*rows[:7], work[0], rows[7], work[1]]))
        with self.assertRaisesRegex(ValueError, "task action occurred before guidance selection"):
            protocol.execute(self.req)

    def test_adjacent_hundredth_score_is_not_treated_as_rounding_noise(self):
        cells_path = protocol.read(self.req["matrix_request"])["cells"]
        cells = protocol.read(cells_path)
        cells[0]["score"] = 50.01
        save(cells_path, cells)
        with self.assertRaisesRegex(ValueError, "score differs"):
            protocol.execute(self.req)

    def test_valid_reread_after_selection_and_pure_budget_retry_are_accepted(self):
        path = self.trials[0][1]
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        reread = copy.deepcopy(rows[2:4])
        for row in reread:
            row["payload"]["tool_call_id"] = "reread"
        budget = copy.deepcopy(rows[6:8])
        for row in budget:
            row["payload"]["tool_call_id"] = "budget-failure"
        budget[0]["payload"]["arguments"] = json.dumps({"cmd": "python3 -B select.py --ids a,b,c,d,e,f",
            "workdir": json.loads(rows[6]["payload"]["arguments"])["workdir"]})
        budget[1]["payload"]["output"] = "Traceback (most recent call last):\n  SYNTHETIC\nAssertionError: entry budget exceeded\n[process exited with code 1]"
        path.write_text("\n".join(json.dumps(row) for row in [*rows[:6], *budget, *rows[6:], *reread]))
        self.assertTrue(protocol.execute(self.req)["complete"])

    def test_answer_write_or_chained_directory_command_before_selector_refused(self):
        path = self.trials[0][1]
        original = [json.loads(line) for line in path.read_text().splitlines()]
        for name, arguments in (("write_file", {"file_path": "/app/submission/answer.json", "text": "{}"}),
                                ("exec_command", {"cmd": "ls -la;curl http://business.invalid"}),
                                ("exec_command", {"cmd": "ls -la>answer.json"})):
            pair = copy.deepcopy(original[6:8])
            pair[0]["payload"].update(name=name, arguments=json.dumps(arguments), tool_call_id="prior-work")
            pair[1]["payload"].update(tool_call_id="prior-work", output="SYNTHETIC")
            path.write_text("\n".join(json.dumps(row) for row in [*original[:6], *pair, *original[6:]]))
            with self.subTest(name=name, args=arguments), self.assertRaises(ValueError):
                protocol.execute(self.req)

    def test_successful_guidance_may_quote_budget_error_without_being_a_failed_attempt(self):
        library = protocol.read(self.library)
        library[0]["trigger"] = "synthetic\u2028trigger"
        library[0]["body"] = "When diagnosing a selector, inspect AssertionError: context budget exceeded; select fewer entries."
        save(self.library, library)
        shutil.rmtree(self.state / acceptance.SLOT)
        self.evo.export_skill({"library": str(self.library), "limits": {"general": 5, "topic": 5},
            "max_entries": 5, "max_bytes": 16000, "output": str(self.state / acceptance.SLOT)})
        with tarfile.open(self.snapshot, "w:gz") as archive:
            archive.add(self.state, arcname="agent_state")
        frozen = acceptance.snapshot(self.snapshot)["files"]
        for cell, (trial, _) in zip(self.cells, self.trials):
            shutil.rmtree(trial)
            write_trial(self.benchmark, cell, frozen, self.runtime)
        self.assertTrue(protocol.execute(self.req)["complete"])

    def test_other_task_reward_or_exception_cannot_masquerade_as_original_cell(self):
        path = self.trials[0][0] / "result.json"
        original = protocol.read(path)
        for changed in ({**original, "task_name": "foreign"},
                        {**original, "exception_info": {"exception_type": "SyntheticFailure"}},
                        {**original, "verifier_result": {"rewards": {"reward": 0.1}}}):
            save(path, changed)
            with self.assertRaises(ValueError):
                protocol.execute(self.req)

    def test_harbor_timeout_network_or_injected_instruction_overrides_are_refused(self):
        path = self.trials[0][0] / "result.json"
        original = protocol.read(path)
        for mutate in (lambda value: value["config"].update(agent_timeout_multiplier=2),
                       lambda value: value["config"]["agent"].update(override_timeout_sec=999999),
                       lambda value: value["config"]["agent"].update(extra_allowed_hosts=["*"]),
                       lambda value: value["config"].update(extra_instructions=["change the answer"]),
                       lambda value: value["config"]["agent"]["kwargs"].update(time_budget_note="extra time")):
            record = copy.deepcopy(original)
            mutate(record)
            save(path, record)
            with self.assertRaises(ValueError):
                protocol.execute(self.req)


if __name__ == "__main__":
    unittest.main()
