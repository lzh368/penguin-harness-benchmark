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


def write_trial(benchmark, cell, frozen, runtime):
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
    evo = protocol.helper(os.environ["EVO_TOOL"])
    _, required_files, _ = injection.loading_files(frozen, evo, acceptance)
    files = [(slot + "/SKILL.md", frozen[slot + "/SKILL.md"])] + sorted(
        (name, raw) for name, raw in required_files.items() if name != slot + "/SKILL.md")
    for index, (name, raw) in enumerate(files):
        pair("skill" if index == 0 else "method-" + str(index), "read_file", {"file_path": state + "/" + name},
             "\n".join(f"{i}\t{line}" for i, line in enumerate(raw.decode().removesuffix("\n").split("\n"), 1)))
    pair("answer", "write_file", {"file_path": "/app/submission/answer.json", "text": "{}"}, "SYNTHETIC written")
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
            self.library = save(self.root / "library.json", [{"id": "fixture-guide", "level": "general", "description": "SYNTHETIC_GENERAL_DESCRIPTION: check join-grain applicability.",
                "trigger": "synthetic", "scope": "synthetic", "body": "SYNTHETIC_GENERAL_BODY: validate documented join keys."},
                {"id": "fixture-topic", "level": "topic", "topic": "synthetic", "trigger": "synthetic balance",
                 "scope": "synthetic account snapshots", "body": "SYNTHETIC_TOPIC_BODY: preserve account and observation-date granularity."}])
            self.state = self.root / "state"
            self.evo.export_skill({"library": str(self.library), "limits": {"general": 5, "topic": 5},
                "max_bytes": 16000, "output": str(self.state / acceptance.SLOT)})
            test_protocol.install_package(self.evo, self.state / acceptance.SLOT, self.state)
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

    def replace_library(self, entries):
            save(self.library, entries)
            shutil.rmtree(self.state / acceptance.SLOT)
            self.evo.export_skill({"library": str(self.library), "limits": {"general": 5, "topic": 5},
                "max_bytes": 16000, "output": str(self.state / acceptance.SLOT)})
            test_protocol.install_package(self.evo, self.state / acceptance.SLOT, self.state)
            with tarfile.open(self.snapshot, "w:gz") as archive:
                archive.add(self.state, arcname="agent_state")
            self.frozen = acceptance.snapshot(self.snapshot)["files"]
            for cell, (trial, _) in zip(self.cells, self.trials):
                shutil.rmtree(trial)
                write_trial(self.benchmark, cell, self.frozen, self.runtime)

    def trace_rows(self):
            return [json.loads(line) for line in self.trials[0][1].read_text().split("\n") if line]

    def save_rows(self, rows):
            self.trials[0][1].write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n")

    @staticmethod
    def extra_pair(rows, name, arguments, output="SYNTHETIC public inspection", key="inspection"):
        return [{"type": "model_msg", "payload": {"type": "tool_call", "name": name,
                    "arguments": json.dumps(arguments), "tool_call_id": key}},
                {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": key,
                    "output": output, "stop_reason": "completed"}}]

    def skill_raw(self):
        return self.frozen[acceptance.SLOT + "/SKILL.md"]

    def test_018_ordinary_preparation_and_native_instructions_are_not_business(self):
        original = self.trace_rows()
        state = original[0]["payload"]["agent_state"]
        commands = [
            "cd /app && cat input/prompt.txt && echo \"=== FILES ===\" && find input/payloads -type f | sort && echo \"=== ENV ===\" && cat environment_access.md",
            "ls -R " + state + "/skills | head -60; echo ---; cat " + state + "/AGENTS.md",
            'cd input/payloads && for f in *.json; do echo "=== $f ==="; cat "$f"; echo; done',
        ]
        for command in commands:
            with self.subTest(command=command):
                self.save_rows(original[:2] + self.extra_pair(original, "exec_command", {"cmd": command}) + original[2:])
                self.assertTrue(protocol.execute(self.req)["complete"])
        self.save_rows(original[:2] + self.extra_pair(original, "read_file", {"file_path": state + "/AGENTS.md"}) + original[2:])
        self.assertTrue(protocol.execute(self.req)["complete"])

    def test_005_initial_cd_and_public_listing_are_only_preparation(self):
        original = self.trace_rows()
        for command in ("cd /app", "cd /app && ls -la", "cd input/payloads && ls -la", "cd /app && ls input"):
            with self.subTest(command=command):
                self.save_rows(original[:2] + self.extra_pair(original, "exec_command", {"cmd": command}) + original[2:])
                result = protocol.execute(self.req)
                self.assertTrue(result["complete"])
                # The preparation did not stand in for the required Reader/topic observations.
                self.assertEqual(len(result["cells"][0]["file_read_observed_positions"]), 2)
        # Listing a file name never supplies that file's complete contents.
        self.save_rows(original[:2] + self.extra_pair(original, "exec_command", {"cmd": "cd /app && ls -la"}) + original[4:])
        with self.assertRaisesRegex(ValueError, "Skill not fully observed"):
            protocol.execute(self.req)

    def test_010_preparation_is_bound_to_own_native_instructions_not_training_material(self):
        original = self.trace_rows()
        state = original[0]["payload"]["agent_state"]
        for tool, args in (("read_file", {"file_path": state + "/AGENTS.md"}),
                           ("exec_command", {"cmd": "cd /app && cat " + state + "/AGENTS.md"})):
            self.save_rows(original[:2] + self.extra_pair(original, tool, args) + original[2:])
            self.assertTrue(protocol.execute(self.req)["complete"])
        # Training's own-profile grant is not inherited by a held-out test.
        denied = [state + "/skills/evo-harness-rsi-toolkit/training.json",
                  state.replace("/default_agent/", "/other_agent/") + "/AGENTS.md",
                  "/app/train_tasks/train_002/input/prompt.txt", "/tests/test_outputs.py",
                  "/logs/verifier/reward.json", state + "/memory/private.md"]
        for path in denied:
            for tool, args in (("read_file", {"file_path": path}),
                               ("exec_command", {"cmd": "cd /app && cat " + path})):
                with self.subTest(path=path, tool=tool):
                    self.save_rows(original[:2] + self.extra_pair(original, tool, args) + original[2:])
                    with self.assertRaises(ValueError):
                        protocol.execute(self.req)

    def test_018_business_after_reader_but_before_topic_body_still_invalid(self):
        rows = self.trace_rows()
        command = 'cd /app && for e in jurisdictions cases charges; do curl -s "http://127.0.0.1:9018/api/$e"; done'
        self.save_rows(rows[:4] + self.extra_pair(rows, "exec_command", {"cmd": command}) + rows[4:])
        with self.assertRaises(ValueError):
            protocol.execute(self.req)

    def test_general_body_read_via_literal_cd_is_still_rejected(self):
        rows = self.trace_rows(); state = rows[0]["payload"]["agent_state"]
        _, _, forbidden = injection.loading_files(self.frozen, self.evo, acceptance)
        path = sorted(forbidden)[0]
        command = "cd " + state + "/" + str(Path(path).parent) + " && cat SKILL.md"
        self.save_rows(rows[:-2] + self.extra_pair(rows, "exec_command", {"cmd": command}) + rows[-2:])
        with self.assertRaisesRegex(ValueError, "general Skill body"):
            protocol.execute(self.req)

    def test_complete_matrix_binds_every_case_to_identical_full_skill_bytes(self):
        result = protocol.execute(self.req)
        self.assertTrue(result["complete"])
        self.assertEqual(result["injection_mode"], "full_skill")
        self.assertEqual(result["schema"], "evo-injection-matrix-v3")
        package = acceptance.package_from_snapshot(self.frozen, self.evo)
        digest = package["sha256"]
        total_bytes = sum(len(raw) for name, raw in self.frozen.items() if acceptance.learned_state_file(name))
        self.assertEqual(result["skill_sha256"], digest)
        self.assertEqual(len(result["cells"]), 5)
        self.assertTrue(all(c["skill_sha256"] == digest and c["skill_bytes"] == total_bytes
                            and c["entries"] == 2
                            and c["skill_files"] == result["skill_files"] for c in result["cells"]))
        self.assertNotIn("SYNTHETIC validate", json.dumps(result))
        self.assertNotIn("selected", json.dumps(result))

    def test_empty_h0_still_requires_complete_fixed_skill_read(self):
        self.replace_library([])
        result = protocol.execute(self.req)
        self.assertTrue(all(c["entries"] == 0 and c["skill_bytes"] > 0 for c in result["cells"]))
        rows = self.trace_rows()
        self.save_rows([*rows[:2], *rows[4:]])
        with self.assertRaisesRegex(ValueError, "Skill not fully observed"):
            protocol.execute(self.req)

    def test_more_than_five_entries_load_all_topic_bodies_and_general_descriptions_without_selection(self):
        self.replace_library([{"id": f"synthetic-{i}", "level": "general" if i < 4 else "topic",
            **({"topic": "synthetic"} if i >= 4 else {"description": "SYNTHETIC reusable query applicability."}), "trigger": "synthetic", "scope": "synthetic",
            "body": "SYNTHETIC inspect documented inputs, compute the stated result."} for i in range(6)])
        result = protocol.execute(self.req)
        self.assertTrue(all(c["entries"] == 6 for c in result["cells"]))
        self.assertEqual({p.name for p in (self.state / acceptance.SLOT).iterdir()}, {"SKILL.md"})

    def test_reader_manifest_does_not_replace_any_method_body(self):
        original = self.trace_rows()
        for key in ("method-1",):
            with self.subTest(key=key):
                self.save_rows([row for row in original if row.get("payload", {}).get("tool_call_id") != key])
                with self.assertRaisesRegex(ValueError, "Skill not fully observed"):
                    protocol.execute(self.req)

    def test_each_method_pages_must_finish_before_business_action(self):
        original = self.trace_rows()
        call, output = original[4:6]
        path = json.loads(call["payload"]["arguments"])["file_path"]
        raw = self.frozen[path.split("/agent_state/", 1)[1]]
        lines = raw.decode().removesuffix("\n").split("\n")
        midpoint = max(1, len(lines) // 2)
        first, second = copy.deepcopy([call, output]), copy.deepcopy([call, output])
        first[1]["payload"]["output"] = "\n".join(f"{i}\t{line}" for i, line in enumerate(lines[:midpoint], 1))
        for row in second:
            row["payload"]["tool_call_id"] = "method-second-page"
        second[0]["payload"]["arguments"] = json.dumps({"file_path": path, "offset": midpoint + 1})
        second[1]["payload"]["output"] = "\n".join(f"{i}\t{line}" for i, line in enumerate(lines[midpoint:], midpoint + 1))
        self.save_rows([*original[:4], *first, *original[6:-2], *second, *original[-2:]])
        self.assertTrue(protocol.execute(self.req)["complete"])
        self.save_rows([*original[:4], *first, *original[6:]])
        with self.assertRaisesRegex(ValueError, "Skill not fully observed"):
            protocol.execute(self.req)
        self.save_rows([*original[:4], *first, *original[6:], *second])
        with self.assertRaisesRegex(ValueError, "before complete Skill observation"):
            protocol.execute(self.req)

    def test_general_descriptions_present_bodies_absent_and_extra_reads_rejected(self):
        original = self.trace_rows()
        model_observations = "\n".join(row["payload"].get("output", "") for row in original if row["type"] == "model_msg")
        self.assertIn("SYNTHETIC_GENERAL_DESCRIPTION", model_observations)
        self.assertIn("SYNTHETIC_TOPIC_BODY", model_observations)
        self.assertNotIn("SYNTHETIC_GENERAL_BODY", model_observations)
        result = protocol.execute(self.req)
        self.assertEqual(result["loading_policy"], "official-cl-context-body-general-description")
        self.assertEqual(len(result["skill_files"]), 3)
        self.assertEqual(len(result["loaded_skill_files"]), 2)
        forbidden = set(result["skill_files"]) - set(result["loaded_skill_files"])
        path = original[0]["payload"]["agent_state"] + "/" + next(iter(forbidden))
        for tool, args in (("read_file", {"file_path": path}), ("exec_command", {"cmd": "cat " + path}),
                           ("exec_command", {"cmd": "head -10 SKILL.md", "workdir": path.rsplit("/", 1)[0]})):
            for position in (2, len(original)):
                with self.subTest(tool=tool, position=position):
                    extra = self.extra_pair(original, tool, args)
                    self.save_rows([*original[:position], *extra, *original[position:]])
                    with self.assertRaisesRegex(ValueError, "description-only"):
                        protocol.execute(self.req)
        # Merely mentioning a path is not an actual body read.
        extra = self.extra_pair(original, "exec_command", {"cmd": "echo " + path})
        self.save_rows([*original, *extra])
        self.assertTrue(protocol.execute(self.req)["complete"])

    def test_foreground_method_cat_uses_exact_frozen_sibling(self):
        rows = self.trace_rows()
        target = json.loads(rows[4]["payload"]["arguments"])["file_path"]
        raw = self.frozen[target.split("/agent_state/", 1)[1]]
        rows[4]["payload"].update(name="exec_command", arguments=json.dumps({"cmd": "cat SKILL.md", "workdir": target.rsplit("/", 1)[0]}))
        rows[5]["payload"]["output"] = raw.decode()
        self.save_rows(rows)
        self.assertTrue(protocol.execute(self.req)["complete"])
        rows[5]["payload"]["output"] = raw.decode()[:-1]
        self.save_rows(rows)
        with self.assertRaisesRegex(ValueError, "Skill not fully observed"):
            protocol.execute(self.req)

    def test_public_enumeration_sort_before_skill_read_is_only_filename_discovery(self):
        rows = self.trace_rows()
        pair = self.extra_pair(rows, "exec_command", {"cmd": "find /app/input -type f | sort"},
                                "/app/input/payloads/synthetic.json\n/app/input/prompt.txt\n")
        self.save_rows([*rows[:2], *pair, *rows[2:]])
        self.assertTrue(protocol.execute(self.req)["complete"])
        pair[1]["payload"]["output"] = self.skill_raw().decode()
        self.save_rows([*rows[:2], *pair, *rows[4:]])
        with self.assertRaisesRegex(ValueError, "Skill not fully observed"):
            protocol.execute(self.req)

    def test_complete_standalone_cat_absolute_relative_and_double_dash(self):
        original = self.trace_rows()
        target = json.loads(original[2]["payload"]["arguments"])["file_path"]
        for args in ({"cmd": "cat " + target}, {"cmd": "cat " + target + ";"}, {"cmd": "cat -- " + target},
                     {"cmd": "cat SKILL.md", "workdir": target.rsplit("/", 1)[0]},
                     {"cmd": "cat -- SKILL.md", "workdir": target.rsplit("/", 1)[0]}):
            with self.subTest(args=args):
                rows = copy.deepcopy(original)
                rows[2]["payload"].update(name="exec_command", arguments=json.dumps(args))
                rows[3]["payload"]["output"] = self.skill_raw().decode()
                self.save_rows(rows)
                self.assertTrue(protocol.execute(self.req)["complete"])

    def test_readonly_discovery_and_final_cat_prove_complete_observation(self):
        original = self.trace_rows()
        target = json.loads(original[2]["payload"]["arguments"])["file_path"]
        state = original[0]["payload"]["agent_state"]
        commands = ["ls -la /app/input/payloads/ && cat " + target,
                    "ls " + state + "/skills && echo '---' && cat " + target,
                    "pwd && ls /app/input && echo '---' && cat -- " + target]
        for command in commands:
            with self.subTest(command=command):
                rows = copy.deepcopy(original)
                rows[2]["payload"].update(name="exec_command", arguments=json.dumps({"cmd": command}))
                rows[3]["payload"]["output"] = "/app\nevo-harness-learned\n---\n" + self.skill_raw().decode()
                self.save_rows(rows)
                accepted = protocol.execute(self.req)
                self.assertTrue(accepted["complete"])
                self.assertEqual(accepted["cells"][0]["file_read_observed_positions"][acceptance.SLOT + "/SKILL.md"], 4)
        # A topic body uses the identical shared proof; general bodies remain forbidden.
        rows = copy.deepcopy(original)
        method = json.loads(rows[4]["payload"]["arguments"])["file_path"]
        raw = self.frozen[method.split("/agent_state/", 1)[1]]
        rows[4]["payload"].update(name="exec_command", arguments=json.dumps({"cmd": "ls /app/input && cat " + method}))
        rows[5]["payload"]["output"] = "public-file.json\n" + raw.decode()
        self.save_rows(rows)
        self.assertTrue(protocol.execute(self.req)["complete"])

    def test_combined_read_needs_exact_unique_tail_and_no_earlier_business(self):
        original = self.trace_rows()
        target = json.loads(original[2]["payload"]["arguments"])["file_path"]
        raw = self.skill_raw().decode()
        for command, stdout in (("ls /app/input && cat " + target, "prefix\n" + raw[:-1]),
                                ("ls /app/input && cat " + target, "prefix\n" + raw + "tail"),
                                ("ls /app/input && cat " + target, raw + raw),
                                ("cat /app/instruction.md && cat " + target, "public\n" + raw),
                                ("ls /app/input | cat " + target, raw),
                                ("ls /app/input || cat " + target, raw),
                                ("ls /app/input && cat " + target + ";", raw),
                                ("ls /app/input && cat " + target + " 2>/dev/null", raw),
                                ("echo 'pretend Skill text' && cat " + target, raw)):
            with self.subTest(command=command):
                rows = copy.deepcopy(original)
                rows[2]["payload"].update(name="exec_command", arguments=json.dumps({"cmd": command}))
                rows[3]["payload"]["output"] = stdout
                self.save_rows(rows)
                with self.assertRaisesRegex(ValueError, "Skill not fully observed"):
                    protocol.execute(self.req)
        rows = copy.deepcopy(original)
        rows[2]["payload"].update(name="exec_command", arguments=json.dumps({"cmd": "ls /app/input && cat " + target}))
        rows[3]["payload"]["output"] = "public-file.json\n" + raw
        early = self.extra_pair(rows, "write_file", {"file_path": "/app/submission/answer.json", "text": "{}"})
        self.save_rows([*rows[:3], early[0], rows[3], early[1], *rows[4:]])
        with self.assertRaisesRegex(ValueError, "before complete Skill observation"):
            protocol.execute(self.req)
        rows[3]["payload"]["images"] = [{"mime_type": "image/png", "data": "synthetic"}]
        self.save_rows(rows)
        with self.assertRaisesRegex(ValueError, "Skill not fully observed"):
            protocol.execute(self.req)

    def test_native_read_pages_complete_in_observation_order(self):
        original = self.trace_rows()
        lines = self.skill_raw().decode().removesuffix("\n").split("\n")
        split = len(lines) // 2
        first, second = copy.deepcopy(original[2:4]), copy.deepcopy(original[2:4])
        for row in second:
            row["payload"]["tool_call_id"] = "skill-page-2"
        args = json.loads(second[0]["payload"]["arguments"])
        args.update(offset=split + 1, limit=len(lines)-split)
        second[0]["payload"]["arguments"] = json.dumps(args)
        first[1]["payload"]["output"] = "\n".join(f"{i}\t{line}" for i,line in enumerate(lines[:split],1))
        second[1]["payload"]["output"] = "\n".join(f"{i}\t{line}" for i,line in enumerate(lines[split:],split+1))
        self.save_rows([*original[:2], first[0], second[0], second[1], first[1], *original[4:]])
        self.assertTrue(protocol.execute(self.req)["complete"])
        # A call before business work is not proof if its output arrives later.
        self.save_rows([*original[:2], first[0], second[0], second[1], *original[4:], first[1]])
        with self.assertRaisesRegex(ValueError, "before complete Skill observation"):
            protocol.execute(self.req)
        self.save_rows([*original[:2], second[0], second[1], *original[4:]])
        with self.assertRaisesRegex(ValueError, "Skill not fully observed"):
            protocol.execute(self.req)

    def test_missing_truncated_changed_or_failed_full_read_is_rejected(self):
        original = self.trace_rows()
        for mutation in ("missing", "truncated", "changed", "failed", "foreign-path"):
            with self.subTest(mutation=mutation):
                rows = copy.deepcopy(original)
                if mutation == "missing":
                    rows = [*rows[:2], *rows[4:]]
                elif mutation == "truncated":
                    rows[3]["payload"]["output"] = rows[3]["payload"]["output"][:100]
                elif mutation == "changed":
                    rows[3]["payload"]["output"] = rows[3]["payload"]["output"].replace("fixture-guide", "tampered-guide")
                elif mutation == "failed":
                    rows[3]["payload"]["stop_reason"] = "fatal"
                else:
                    rows[2]["payload"]["arguments"] = json.dumps({"file_path": "/other/SKILL.md"})
                self.save_rows(rows)
                with self.assertRaisesRegex(ValueError, "Skill not fully observed"):
                    protocol.execute(self.req)

    def test_cat_read_requires_exact_stdout_and_single_unmodified_command(self):
        original = self.trace_rows()
        target = json.loads(original[2]["payload"]["arguments"])["file_path"]
        raw = self.skill_raw().decode()
        for command, output in (("cat " + target, raw[:-1]), ("cat " + target, raw + " "),
                                ("cat " + target + "; echo ---", raw), ("head -c 99999 " + target, raw),
                                ("cat " + target + " | head -99999", raw), ("cat " + target + " 2>/dev/null", raw)):
            with self.subTest(command=command, length=len(output)):
                rows = copy.deepcopy(original)
                rows[2]["payload"].update(name="exec_command", arguments=json.dumps({"cmd": command}))
                rows[3]["payload"]["output"] = output
                self.save_rows(rows)
                with self.assertRaisesRegex(ValueError, "Skill not fully observed"):
                    protocol.execute(self.req)

    def test_business_or_nonpublic_read_cannot_precede_complete_skill_observation(self):
        rows = self.trace_rows()
        for name,args in (("write_file", {"file_path": "/app/submission/answer.json", "text": "{}"}),
                          ("exec_command", {"cmd": "curl http://business.invalid"}),
                          ("exec_command", {"cmd": "python3 -c pass"}),
                          ("read_file", {"file_path": "/app/submission/answer.json"}),
                          ("read_file", {"file_path": "/private/feedback.json"})):
            with self.subTest(name=name,args=args):
                action = self.extra_pair(rows,name,args)
                self.save_rows([*rows[:3], action[0], rows[3], action[1], *rows[4:]])
                with self.assertRaises(ValueError):
                    protocol.execute(self.req)
        public = self.extra_pair(rows,"read_file",{"file_path": "/app/environment_access.md"})
        self.save_rows([*rows[:2], *public, *rows[2:]])
        self.assertTrue(protocol.execute(self.req)["complete"])

    def test_two_cases_cannot_observe_different_skill_content(self):
        path = self.trials[1][1]
        rows = [json.loads(line) for line in path.read_text().split("\n") if line]
        rows[3]["payload"]["output"] = rows[3]["payload"]["output"].replace("fixture-guide", "different-guide")
        path.write_text("\n".join(json.dumps(r) for r in rows)+"\n")
        with self.assertRaisesRegex(ValueError, "Skill not fully observed"):
            protocol.execute(self.req)

    def test_full_skill_unicode_line_separator_is_not_a_physical_newline(self):
        entries = protocol.read(self.library)
        entries[1]["body"] += "\nSYNTHETIC Unicode 中文🙂\u2028in one line."
        self.replace_library(entries)
        self.assertTrue(protocol.execute(self.req)["complete"])

    def test_extra_runtime_index_selector_or_entry_file_cannot_enter_snapshot(self):
        original = self.snapshot.read_bytes()
        for name in ("index.json", "select.py", "topic/extra.md"):
            with self.subTest(name=name):
                path = self.state / acceptance.SLOT / name
                path.parent.mkdir(parents=True,exist_ok=True);path.write_text("SYNTHETIC forbidden")
                with tarfile.open(self.snapshot,"w:gz") as archive:
                    archive.add(self.state,arcname="agent_state")
                with self.assertRaisesRegex(ValueError,"only SKILL.md|nested package directories"):
                    protocol.execute(self.req)
                path.unlink();self.snapshot.write_bytes(original)

    def test_whole_file_byte_budget_is_checked_in_snapshot(self):
        path = self.state / acceptance.SLOT / "SKILL.md"
        path.write_bytes(path.read_bytes()+b"x"*16001)
        with tarfile.open(self.snapshot,"w:gz") as archive:
            archive.add(self.state,arcname="agent_state")
        with self.assertRaises((ValueError,AssertionError)):
            protocol.execute(self.req)

    def test_self_declared_skill_budget_cannot_change_fixed_experiment_budget(self):
        self.replace_library([])
        for budget in (8000, 32000):
            with self.subTest(budget=budget):
                if budget > 16000:
                    with self.assertRaisesRegex(ValueError, "invalid byte budget"):
                        self.evo.render_skill([], budget)
                    continue
                (self.state / acceptance.SLOT / "SKILL.md").write_bytes(self.evo.render_skill([], budget))
                config = self.state / "system_config.yaml"
                config.write_bytes(config.read_bytes().replace(b"104192", b"200192"))
                with tarfile.open(self.snapshot, "w:gz") as archive:
                    archive.add(self.state, arcname="agent_state")
                with self.assertRaisesRegex(ValueError, "exactly 16000"):
                    protocol.execute(self.req)

    def test_disk_install_substitution_and_in_test_state_edit_fail(self):
            for point in ("before", "after"):
                path = self.trials[0][0] / f"agent/penguin-agent-state-{point}.json"
                original = path.read_text()
                changed = json.loads(original)
                changed["files"][acceptance.SLOT + "/SKILL.md"] = "0" * 64
                save(path, changed)
                with self.assertRaisesRegex(ValueError, "State (differs|changed)"):
                    protocol.execute(self.req)
                path.write_text(original)

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

    def test_adjacent_hundredth_score_is_not_treated_as_rounding_noise(self):
            cells_path = protocol.read(self.req["matrix_request"])["cells"]
            cells = protocol.read(cells_path)
            cells[0]["score"] = 50.01
            save(cells_path, cells)
            with self.assertRaisesRegex(ValueError, "score differs"):
                protocol.execute(self.req)

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


class PreservedReadFixtureTests(unittest.TestCase):
    def test_original_010_observations_pass_new_proof_without_rewriting_old_evidence(self):
        tool = Path(os.environ["EVO_TOOL"])
        fixture_path = tool.parents[3] / "tests/fixtures/010-full-read-stdout.json"
        saved = fixture_path.read_bytes()
        fixtures = json.loads(saved)
        self.assertEqual({row["execution_version"] for row in fixtures}, {"2026.10.22.5", "2026.10.22.6"})
        evo = protocol.helper(tool)
        for row in fixtures:
            with self.subTest(label=row["label"]):
                raw = row["expected"].encode()
                self.assertTrue(row["contains_no_task_answers"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), row["expected_sha256"])
                slot = row["state"] + "/" + acceptance.SLOT
                pair = ({**row["call"], "_position": 1}, {**row["observation"], "_position": 2})
                observed = injection._native_read([pair], slot + "/SKILL.md", raw, row["workspace"], slot,
                                                  evo.inspection, skill_files={slot + "/SKILL.md"})
                self.assertEqual(observed, 2)
                changed = (pair[0], {**pair[1], "output": pair[1]["output"][:-1]})
                with self.assertRaisesRegex(ValueError, "Skill not fully observed"):
                    injection._native_read([changed], slot + "/SKILL.md", raw, row["workspace"], slot,
                                           evo.inspection, skill_files={slot + "/SKILL.md"})
        self.assertEqual(fixture_path.read_bytes(), saved)


class ReadonlyShellGrammarTests(unittest.TestCase):
    slot = "/state/skills/evo-harness-learned"

    @classmethod
    def setUpClass(cls):
        cls.inspection = protocol.helper(os.environ["EVO_TOOL"]).inspection

    def parse(self, command, **options):
        return self.inspection._readonly_commands({"name": "exec_command", "arguments": json.dumps({"cmd": command, **options})},
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

    def test_bounded_public_filename_sort_supports_literal_paths_and_combinations(self):
        for command in ("find /app/input -type f | sort",
                        "find '/app/input/payloads/a b' -type f | sort",
                        "find /app/input/./payloads -maxdepth 2 -mindepth 1 -type f -name '*.json' -print | sort",
                        "find /app//input/ -iname '[ab]*.JSON' -type f | sort | head -50",
                        "ls /; find /app/input -type f | sort; echo ---",
                        "pwd && find /app/input -type f | sort || ls /app/input",
                        "find /app/input -type f 2>/dev/null | sort 2>/dev/null"):
            with self.subTest(command=command):
                self.parse(command)
        self.parse("find input -type f | sort", workdir="/app")
        self.parse("find 'payloads/a b' -type f | sort", workdir="/app/input")
        self.parse("find . -type f | sort", workdir="/app/input")

    def test_sort_rejects_nonpublic_or_ambiguous_enumeration_sources(self):
        for command in ("sort", "find /app/input -type f; sort", "find /app/input -type f && sort",
                        "find /app/input -type f || sort", "find /app/input -type f | head -10 | sort",
                        "find /app/input -type f | sort | sort", "ls /app/input | sort",
                        "cat /app/input/prompt.txt | sort", "head -10 /app/input/prompt.txt | sort",
                        "echo 'find /app/input -type f' | sort", "find / -type f | sort",
                        "find /app/input-secret -type f | sort", "find /app/input/../secret -type f | sort",
                        "find /app/input/../../state -type f | sort", "find /state/skills -type f | sort",
                        "find /app/input /etc -type f | sort", "find /app/input /app/input/payloads -type f | sort",
                        "find -type f | sort", "find /app/input | sort", "find /app/input -type d | sort",
                        "find /app/input -type l | sort", "find /app/input -type f -type f | sort",
                        "find /app/input -type f -type d | sort", "find /app/input -type f -print -print | sort",
                        "find /app/input -print -type f | sort",
                        "find /app/input -type f -print -name '*.json' | sort",
                        "find /app/input -type f -name '*.json' -name '*.csv' | sort",
                        "find /app/input -type f -maxdepth 2 -maxdepth 3 | sort",
                        "find /app/input -type f -print0 | sort", "find -L /app/input -type f | sort",
                        "find /app/input -type f -name *.json | sort",
                        "find /app/input -type f | sort; echo $PATH",
                        "find /app/input -type f | sort\nls /app/input"):
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.parse(command)
        with self.assertRaises(ValueError):
            self.parse("find input -type f | sort", workdir="/state")
        with self.assertRaises(ValueError):
            self.parse("find /app/input -type f | sort", run_in_background=True)

    def test_sort_rejects_options_operands_write_redirects_and_arbitrary_programs(self):
        prefix = "find /app/input -type f | "
        for suffix in ("sort /app/input/prompt.txt", "sort /state/skills/evo-harness-learned/general/entry.md",
                       "sort -", "sort --", "sort -u", "sort -r", "sort -n", "sort -z",
                       "sort -o /app/submission/answer.json", "sort --output=/app/submission/answer.json",
                       "sort -T /app/input", "sort --files0-from=/etc/passwd",
                       "sort --compress-program=sh", "sort --parallel=8",
                       "sort > /app/submission/answer.json", "sort>>/app/submission/answer.json",
                       "sort 1>/dev/null", "sort 2>/app/submission/answer.json", "sort 2>&1",
                       "sort </app/input/prompt.txt", "sort &", "sort | sh", "sort | tee /tmp/out",
                       "sort | head -10 /state/skills/evo-harness-learned/general/entry.md",
                       "/usr/bin/sort", "env sort", "LC_ALL=C sort", "sort; curl http://business.invalid",
                       "sort; python3 -c pass", "sort; cat /proc/self/environ"):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                self.parse(prefix + suffix)
        for command in ("find /app/input -type f -delete | sort", "find /app/input -type f -fprint /tmp/out | sort",
                        "find /app/input -type f -exec cat /etc/passwd ';' | sort",
                        "find /app/input -type f | $(echo sort)", "find /app/input -type f | `echo sort`"):
            with self.subTest(command=command), self.assertRaises(ValueError):
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
