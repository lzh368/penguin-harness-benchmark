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
import injection
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
            "type": "tool_call_output", "tool_call_id": key, "output": output, "stop_reason": "completed"}}])
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

    def replace_library(self, entries, selected=None):
        save(self.library, entries)
        shutil.rmtree(self.state / acceptance.SLOT)
        self.evo.export_skill({"library": str(self.library), "limits": {"general": 5, "topic": 5},
            "max_entries": 5, "max_bytes": 16000, "output": str(self.state / acceptance.SLOT)})
        with tarfile.open(self.snapshot, "w:gz") as archive:
            archive.add(self.state, arcname="agent_state")
        self.frozen = acceptance.snapshot(self.snapshot)["files"]
        for cell, (trial, _) in zip(self.cells, self.trials):
            shutil.rmtree(trial)
            write_trial(self.benchmark, cell, self.frozen, self.runtime, selected=selected)

    def trace_rows(self):
        return [json.loads(line) for line in self.trials[0][1].read_text().split("\n") if line]

    def save_rows(self, rows):
        self.trials[0][1].write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n")

    @staticmethod
    def extra_pair(rows, name, arguments, output="SYNTHETIC public inspection", key="inspection"):
        pair = copy.deepcopy(rows[6:8])
        pair[0]["payload"].update(name=name, arguments=json.dumps(arguments), tool_call_id=key)
        pair[1]["payload"].update(tool_call_id=key, output=output, stop_reason="completed")
        return pair

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

    def test_real_h0_readonly_directory_and_public_document_combinations(self):
        self.replace_library([])
        rows = self.trace_rows()
        for command in ("ls -la /app/input/payloads/ && ls -la /app",
                        "ls -R /app/input /app; cat /app/environment_access.md 2>/dev/null || cat /app/input/environment_access.md"):
            with self.subTest(command=command):
                pair = self.extra_pair(rows, "exec_command", {"cmd": command})
                self.save_rows([*rows[:6], *pair, *rows[6:]])
                result = protocol.execute(self.req)
                self.assertTrue(result["complete"])
                self.assertTrue(all(cell["bytes"] == 0 for cell in result["cells"]))

    def test_real_final_discovery_pipeline_and_literal_echo_separator(self):
        rows = self.trace_rows()
        pair = self.extra_pair(rows, "exec_command", {
            "cmd": "find /app/input -type f | head -50; echo ---; ls -la /app"})
        self.save_rows([*rows[:6], *pair, *rows[6:]])
        self.assertTrue(protocol.execute(self.req)["complete"])

    def test_real_final_complete_standalone_cat_index_proves_full_read(self):
        entries = protocol.read(self.library)
        delta = 2830 - len(self.frozen[acceptance.SLOT + "/index.json"])
        entries[0]["trigger"] += "a" * (delta // 2)
        entries[0]["scope"] += "b" * (delta - delta // 2)
        self.replace_library(entries)
        raw = self.frozen[acceptance.SLOT + "/index.json"]
        self.assertEqual(len(raw), 2830)
        rows = self.trace_rows()
        target = json.loads(rows[4]["payload"]["arguments"])["file_path"]
        rows[4]["payload"].update(name="exec_command", arguments=json.dumps({"cmd": "cat " + target}))
        rows[5]["payload"]["output"] = raw.decode()
        public = self.extra_pair(rows, "exec_command", {
            "cmd": "cat /app/environment_access.md; head -n 20 /app/input/prompt.txt"})
        self.save_rows([*rows[:6], *public, *rows[6:]])
        self.assertTrue(protocol.execute(self.req)["complete"])

    def test_standalone_cat_reader_and_index_preserve_unicode_and_newlines(self):
        entries = protocol.read(self.library)
        entries[0]["trigger"] = "SYNTHETIC Unicode 中文🙂\u2028trigger"
        self.replace_library(entries)
        rows = self.trace_rows()
        for offset, name in ((2, "SKILL.md"), (4, "index.json")):
            target = json.loads(rows[offset]["payload"]["arguments"])["file_path"]
            rows[offset]["payload"].update(name="exec_command", arguments=json.dumps({"cmd": "cat " + target}))
            rows[offset + 1]["payload"]["output"] = self.frozen[acceptance.SLOT + "/" + name].decode()
        self.save_rows(rows)
        self.assertTrue(protocol.execute(self.req)["complete"])

    def test_cat_read_proof_rejects_wrong_truncated_failed_combined_or_late_output(self):
        original = self.trace_rows()
        target = json.loads(original[4]["payload"]["arguments"])["file_path"]
        raw = self.frozen[acceptance.SLOT + "/index.json"].decode()
        variants = [
            ("cat " + target, raw[:-1], "completed", False),
            ("cat " + target, raw + " ", "completed", False),
            ("cat " + target, raw[:40] + "[output truncated]", "completed", False),
            ("cat " + target, raw, "fatal", False),
            ("cat " + target + "; echo ---", raw, "completed", False),
            ("head -c 99999 " + target, raw, "completed", False),
            ("cat " + target + " | head -99999", raw, "completed", False),
            ("cat " + target + " 2>/dev/null", raw, "completed", False),
            ("cat " + target, raw, "completed", True),
        ]
        for command, output, status, late in variants:
            with self.subTest(command=command, status=status, late=late, bytes=len(output)):
                rows = copy.deepcopy(original)
                rows[4]["payload"].update(name="exec_command", arguments=json.dumps({"cmd": command}))
                rows[5]["payload"].update(output=output, stop_reason=status)
                if late:
                    rows[5], rows[6] = rows[6], rows[5]
                self.save_rows(rows)
                with self.assertRaisesRegex(ValueError, "reader/index"):
                    protocol.execute(self.req)

    def test_single_cat_relative_operand_or_double_dash_proves_exact_file(self):
        original = self.trace_rows()
        target = json.loads(original[4]["payload"]["arguments"])["file_path"]
        raw = self.frozen[acceptance.SLOT + "/index.json"].decode()
        for arguments in ({"cmd": "cat index.json", "workdir": target.rsplit("/", 1)[0]},
                          {"cmd": "cat -- " + target},
                          {"cmd": "cat -- index.json", "workdir": target.rsplit("/", 1)[0]}):
            with self.subTest(arguments=arguments):
                rows = copy.deepcopy(original)
                rows[4]["payload"].update(name="exec_command", arguments=json.dumps(arguments))
                rows[5]["payload"].update(output=raw, stop_reason="completed")
                self.save_rows(rows)
                self.assertTrue(protocol.execute(self.req)["complete"])

    def test_entry_body_reads_cannot_precede_the_complete_selector(self):
        rows = self.trace_rows()
        state = rows[0]["payload"]["agent_state"]
        target = state + "/" + acceptance.SLOT + "/general/fixture-guide.md"
        for name, arguments in (("read_file", {"file_path": target}),
                                ("exec_command", {"cmd": "cat " + target}),
                                ("exec_command", {"cmd": "head -n 1 " + target})):
            with self.subTest(tool=name, arguments=arguments):
                pair = self.extra_pair(rows, name, arguments)
                self.save_rows([*rows[:6], *pair, *rows[6:]])
                with self.assertRaisesRegex(ValueError, "entry"):
                    protocol.execute(self.req)

    def test_unselected_cat_head_and_mixed_commands_cannot_bypass_budget_after_selection(self):
        self.replace_library(protocol.read(self.library), selected=[])
        rows = self.trace_rows()
        slot = rows[0]["payload"]["agent_state"] + "/" + acceptance.SLOT
        for arguments in ({"cmd": "cat " + slot + "/general/fixture-guide.md"},
                          {"cmd": "head -1 " + slot + "/general/fixture-guide.md"},
                          {"cmd": "cat general/fixture-guide.md", "workdir": slot},
                          {"cmd": "cat " + slot + "/general/fixture-guide.md; curl http://business.invalid"},
                          {"cmd": "cat -n " + slot + "/general/fixture-guide.md"},
                          {"cmd": "head --verbose " + slot + "/general/fixture-guide.md"},
                          {"cmd": "cat " + slot + "/general/fixture-guide.md; echo \"$PATH\""},
                          {"cmd": "cat " + slot + "/general/fixture-guide.md; python3 -c 'print(1)'"},
                          {"cmd": "head -1 " + slot + "/general/fixture-guide.md; echo '{}'"},
                          {"cmd": "cat " + slot + "/general/fixture-guide.md > /tmp/copied.txt"},
                          {"cmd": "cat " + slot + "/general/fixture-guide.md>/tmp/copied.txt"},
                          {"cmd": "# read the existing file\ncat " + slot + "/general/fixture-guide.md"},
                          {"cmd": "/bin/cat " + slot + "/general/fixture-guide.md"},
                          {"cmd": "cd " + slot + " && cat general/fixture-guide.md"}):
            with self.subTest(arguments=arguments):
                pair = self.extra_pair(rows, "exec_command", arguments)
                self.save_rows([*rows, *pair])
                with self.assertRaisesRegex(ValueError, "entry"):
                    protocol.execute(self.req)

    def test_post_selection_path_text_is_not_misclassified_as_an_entry_read(self):
        self.replace_library(protocol.read(self.library), selected=[])
        rows = self.trace_rows()
        slot = rows[0]["payload"]["agent_state"] + "/" + acceptance.SLOT
        for arguments in ({"cmd": "python3 -c 'print({\"source\": \"" + slot + "/general/fixture-guide.md\"})'"},
                          {"cmd": "echo 'Documentation mentions " + slot + "/general/fixture-guide.md'"},
                          {"cmd": "python3 -c 'print(\"text; cat " + slot + "/general/fixture-guide.md\")'"},
                          {"cmd": "echo 'text; cat " + slot + "/general/fixture-guide.md'"},
                          {"cmd": "echo text # ; cat " + slot + "/general/fixture-guide.md"}):
            with self.subTest(arguments=arguments):
                pair = self.extra_pair(rows, "exec_command", arguments)
                self.save_rows([*rows, *pair])
                self.assertTrue(protocol.execute(self.req)["complete"])

    def test_selected_entry_reread_after_selection_does_not_expand_budget(self):
        rows = self.trace_rows()
        target = rows[0]["payload"]["agent_state"] + "/" + acceptance.SLOT + "/general/fixture-guide.md"
        pair = self.extra_pair(rows, "exec_command", {"cmd": "cat " + target})
        self.save_rows([*rows, *pair])
        self.assertTrue(protocol.execute(self.req)["complete"])

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

    def test_parallel_native_pages_require_the_latest_needed_observation_before_selection(self):
        original = self.trace_rows()
        raw = self.frozen[acceptance.SLOT + "/SKILL.md"].decode().split("\n")
        if raw[-1] == "":
            raw.pop()
        split = max(1, len(raw) // 2)
        first = copy.deepcopy(original[2:4])
        second = copy.deepcopy(first)
        for row in second:
            row["payload"]["tool_call_id"] = "reader-page-2"
        arguments = json.loads(second[0]["payload"]["arguments"])
        arguments.update(offset=split + 1, limit=len(raw) - split)
        second[0]["payload"]["arguments"] = json.dumps(arguments)
        first[1]["payload"]["output"] = "\n".join(f"{i}\t{line}" for i, line in enumerate(raw[:split], 1))
        second[1]["payload"]["output"] = "\n".join(f"{i}\t{line}" for i, line in enumerate(raw[split:], split + 1))
        # Calls are A then B; B's output arrives first. Page A is observed only
        # after selection, so neither its earlier call nor B's output proves it.
        self.save_rows([*original[:2], first[0], second[0], second[1], *original[4:], first[1]])
        with self.assertRaisesRegex(ValueError, "observed before selection"):
            protocol.execute(self.req)
        self.save_rows([*original[:2], first[0], second[0], second[1], first[1], *original[4:]])
        self.assertTrue(protocol.execute(self.req)["complete"])

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


class ReadonlyShellGrammarTests(unittest.TestCase):
    slot = "/state/skills/evo-harness-learned"

    def parse(self, command, **options):
        return injection._readonly_commands({"name": "exec_command", "arguments": json.dumps({"cmd": command, **options})},
                                            "/app", self.slot)

    def test_limited_literal_public_read_combinations(self):
        for command in ("pwd; ls -la /app", "cat '/app/input/payloads/a b.txt'",
                        "cat /app/environment_access.md 2> /dev/null || cat /app/input/environment_access.md",
                        "cat /app/environment_access.md 2>'/dev/null'",
                        "head -n 20 /app/input/prompt.txt", "cat /app/input/prompt.txt | head -20",
                        "find /app/input -maxdepth 2 -type f | head --lines=50; echo ---; ls -R /app",
                        "ls /app;", "cat -- /app/instruction.md", "ls /",
                        "ls /state/skills; find /opt -maxdepth 1 -type d",
                        "find /app/input -name '*.json'", 'find /app/input -iname "*.JSON"',
                        "find /app/input -name '[ab]*.json' -type f | head -50"):
            with self.subTest(command=command):
                self.parse(command)

    def test_side_effects_expansion_nonpublic_reads_and_ambiguous_redirection_fail_closed(self):
        commands = (
            "ls -la;curl http://business.invalid", "ls -la&&python3 -c pass", "ls -la>answer.json",
            "cat /app/input/prompt.txt > /app/submission/answer.json", "cat /app/input/prompt.txt 2>/tmp/error",
            "cat /app/input/prompt.txt 2>&1", "cat /app/input/prompt.txt 2 > /dev/null",
            "cat /app/input/prompt.txt '2'>/dev/null", "cat /app/input/prompt.txt &> /dev/null",
            "cat </app/input/prompt.txt", "cat /app/input/prompt.txt | sh", "echo text | bash",
            "find /app -delete", "find /app -exec cat /state/entry.md ';'", "find /app -fprint /tmp/x",
            "ls $(cat /app/input/prompt.txt)", "ls `pwd`", "cat $FILE", "cat /app/input/*",
            "find /app/input -name *.json", "find /app/input -name foo'*.json'",
            "find /app/input -name '[ab]*.json'; cat /app/input/*.json", "cat '/app/input/*.json'",
            "cat /app/input/prompt.txt &", "pwd\nls", "(pwd)", "echo hi;> /tmp/out", "pwd;;ls",
            "cat /proc/self/environ", "cat /app/submission/answer.json", "cat /app/input/../../state/entry.md",
            "cat " + self.slot + "/general/entry.md", "head -1 " + self.slot + "/general/entry.md",
            "find /app | head -1 /state/entry.md", "X=x cat /app/input/prompt.txt")
        for command in commands:
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.parse(command)
        with self.assertRaises(ValueError):
            self.parse("cat /app/input/prompt.txt", run_in_background=True)


if __name__ == "__main__":
    unittest.main()
