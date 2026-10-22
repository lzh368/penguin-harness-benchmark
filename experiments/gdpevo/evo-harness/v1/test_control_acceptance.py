"""SYNTHETIC controller-to-export/State/hook wiring, without any model or dispatch.

Uses actual control/protocol/acceptance helpers and canonical judge fixtures.
Native traces are fixture data; real native Session execution has separate TS tests.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest

import protocol
import test_protocol
import test_registration
import test_injection
import acceptance


class ControllerAcceptanceTests(unittest.TestCase):
    def setUp(self):
        registration = test_registration.RegistrationTests("test_native_roster_complete_launch_and_full_runtime")
        self.addCleanup(registration.doCleanups)
        registration.setUp()
        self.root = registration.root
        self.tool = Path(os.environ["EVO_TOOL"]).resolve()
        sys.path.insert(0, str(self.tool.parent))
        self.addCleanup(lambda: sys.path.remove(str(self.tool.parent)))
        spec = importlib.util.spec_from_file_location("synthetic_control_acceptance", self.tool.with_name("control.py"))
        self.control = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.control)
        self.runtime = {**registration.config["runtime"], "provider": "synthetic", "thinking_level": "high"}
        self.config_path = self.root / "synthetic-config.json"
        self.config = {**registration.config,
            "synthetic": True, "paid_authorized": True, "budget_usd": 2, "runtime": self.runtime,
            "benchmark_id": "synthetic-training-benchmark", "test_benchmark_id": "synthetic-test-benchmark",
            "trainer_agent_id": "synthetic-trainer", "test_agent_id": "synthetic-target",
            "deployment": registration.config["deployment"]}
        checkout = next(path for path in self.tool.parents if (path / "packages/core/package.json").is_file())
        self.config["deployment"].update(penguin_checkout=str(checkout), node=shutil.which("node"))
        registration.preflight["pins"]["provider"] = self.runtime["provider"]
        registration.save_preflight()
        self.save(self.config_path, self.config)
        self.registration_request = {**registration.req, "config": str(self.config_path), "config_sha256": protocol.sha(self.config_path)}
        self.registered = protocol.execute(self.registration_request)
        self.test_cases = self.registered["test"]["case_ids"]
        fixture = test_protocol.ExportEvidenceTests("test_sealed_five_task_export_survives_relocation_using_original_traces")
        fixture.all_success = getattr(self, "all_success", True)
        fixture.frozen_runtime = self.runtime
        fixture.config_sha256 = protocol.sha(self.config_path)
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.f = fixture
        self.evo = fixture.evo
        self.evo.seal_training(fixture.seal_request)
        agents = self.root / "penguin/synthetic-project/agents"
        self.state = agents / "synthetic-controller/agent_state"
        self.state.mkdir(parents=True)
        self.target = agents / "synthetic-target/agent_state"
        self.target.mkdir(parents=True)
        shutil.copytree(fixture.submission / "skill", self.target / "skills/evo-harness-learned")
        (self.target / "system_config.yaml").write_text("version: 1\nsystem_prompt: SYNTHETIC frozen target\nhooks:\n  enabled: false\n" + test_protocol.SYNTHETIC_OUTPUT_TOOLS)
        (self.target / "AGENTS.md").write_text("SYNTHETIC fixed target instructions\n")
        self.h0 = self.root / "h0.tar.gz"
        self.final = self.root / "final.tar.gz"
        self.final_version = 1 if fixture.all_success else 2
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            h0_state = Path(temporary) / "agent_state"
            shutil.copytree(self.target, h0_state)
            if not fixture.all_success:
                shutil.rmtree(h0_state / "skills/evo-harness-learned")
                self.evo.export_skill({"library": str(fixture.library), "limits": fixture.limits,
                    "max_entries": 5, "max_bytes": 16000, "output": str(h0_state / "skills/evo-harness-learned")})
                (self.target / "system_config.yaml").write_text((self.target / "system_config.yaml").read_text().replace("version: 1", "version: 2"))
            for archive, state in ((self.h0, h0_state), (self.final, self.target)):
                with tarfile.open(archive, "w:gz") as handle:
                    handle.add(state, arcname="agent_state")
        self.scratch = self.state.parent / "scratchpad/synthetic-controller"
        self.scratch.mkdir(parents=True)
        self.trace = self.state.parent / "traces/synthetic-controller.jsonl"
        self.trace.parent.mkdir()
        self.meta = {"session_id": "synthetic-controller", "source": "cli", "agent_state": str(self.state),
                     "workspace": str(self.root), "provider": self.runtime["provider"], "model_id": self.runtime["model_id"]}
        self.trace.write_text(json.dumps({"type": "session_meta", "payload": self.meta}) + "\n")
        source = Path(__file__).with_name("protocol.py").resolve()
        self.mode = {"schema": 2, "mode": "controller", "session_id": self.meta["session_id"],
            "config": str(self.config_path), "config_sha256": protocol.sha(self.config_path),
            "protocol": str(source), "protocol_sha256": protocol.sha(source),
            "acceptance_sha256": protocol.sha(source.with_name("acceptance.py")),
            "support_sha256": {name: protocol.sha(source.with_name(name)) for name in ("registration.py", "injection.py")},
            "freeze_request": str(self.scratch / "evo-guard/freeze-request.json"),
            "completion_request": str(self.scratch / "evo-guard/completion-request.json")}
        self.save(self.scratch / self.control.MARKER, self.mode)
        exported = self.save(self.root / "export-request.json", {
            "evo_tool": str(self.tool), "submission": str(fixture.submission),
            "training_export": str(fixture.record_path), "train_ids": fixture.train,
            "private_learning": str(fixture.private), "trace_root": str(fixture.traces), "runtime": self.runtime})
        self.save(Path(self.mode["freeze_request"]), {"evo_tool": str(self.tool), "export_request": str(exported),
            "h0_snapshot": str(self.h0), "final_snapshot": str(self.final), "h0_version": 1, "final_version": self.final_version,
            "target_state": str(self.target), "test_case_ids": self.test_cases, "runtime": self.runtime})
        # Match the unchanged native evaluation.md output, not the complete
        # frozen runtime used by export/freeze/injection requests. Real workers
        # do not return api_host, effective_reasoning_effort or client_type.
        self.worker_runtime = {key: self.runtime[key] for key in ("provider", "model_id", "thinking_level")}
        self.identity = {"agent_id": "synthetic-target", "expected_version": 1, **self.worker_runtime}
        self.cells = [{"protocol_version": 1, **self.identity, "case_id": task, "run": 1, "status": "ok",
            "score": 50, "cost": 0, "duration_ms": 1, "session_id": f"harbor:synthetic-test-{i}"}
            for i, task in enumerate(self.test_cases)]
        self.benchmark = Path(self.config["deployment"]["penguin_home"]) / self.config["deployment"]["project_id"] / "benchmarks" / self.config["test_benchmark_id"]
        for cell in self.cells:
            test_injection.write_trial(self.benchmark, cell, acceptance.snapshot(self.h0)["files"], self.runtime)
        cells = self.save(self.root / "synthetic-cells.json", self.cells)
        self.matrix = {"evo_tool": str(self.tool), "cells": str(cells), "case_ids": self.test_cases,
            "runs": 1, "identity": self.identity, "time": "2026-10-21T00:00:00Z",
            "summary_title": "SYNTHETIC", "summary": "Constant fixtures, not benchmark scores"}
        self.matrix_path = self.save(self.scratch / "evo-guard/h0-matrix-request.json", self.matrix)
        self.completion = {"h0_matrix_request": str(self.matrix_path), "final_policy": "reused_equivalent_h0",
                           "host_cost_status": "pending_final_request"}
        self.save(Path(self.mode["completion_request"]), self.completion)
        self.save(self.scratch / "GOAL.json", {"status": "complete", "ended": False})
        self.workers = {}
        # Original native worker results are an independent source of each
        # matrix cell. They must exist even when no formatting repair was used.
        self.restore_target(self.h0)
        for cell in self.cells:
            self.record_worker("h0", cell)
        self.restore_target(self.final)

    @staticmethod
    def save(path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())
        return path

    def hook(self, event, **extra):
        return self.control.hook({"hook": event, "trace_path": str(self.trace), **extra}, self.state)

    def call(self, task, version=1):
        prompt = "\n".join(f"{key}: {value}" for key, value in {
            "benchmark_id": self.config["test_benchmark_id"], "test_agent_id": "synthetic-target",
            "case_id": task, "run": 1, "expected_version": version, **self.worker_runtime}.items())
        return {"description": "SYNTHETIC test worker", "prompt": prompt, **self.worker_runtime, "run_in_background": True}

    def restore_target(self, archive):
        shutil.rmtree(self.target)
        for name, content in acceptance.snapshot(archive)["files"].items():
            path = self.target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    @staticmethod
    def append_rows(path, rows):
        with Path(path).open("a") as handle:
            handle.write("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))

    @staticmethod
    def worker_yaml(cell):
        return "".join(key + ": " + json.dumps(value, ensure_ascii=False) + "\n" for key, value in cell.items())

    def record_worker(self, phase, cell):
        index = self.test_cases.index(cell["case_id"])
        call = self.call(cell["case_id"], cell["expected_version"])
        ticket = self.control.worker_request({"phase": phase, "call": call}, self.scratch, self.mode)
        if (phase, cell["case_id"]) in self.workers:
            return self.workers[phase, cell["case_id"]]
        call_id = f"synthetic-call-{index}" if phase == "h0" else f"synthetic-final-call-{index}"
        child_id = f"subagent-synthetic-{phase}-{index}"
        session_id = f"session-synthetic-worker-{phase}-{index}"
        self.save(Path(ticket["ticket"]).parent / (self.evo.digest(ticket["slot"].encode()) + ".claimed.json"),
            {"slot": ticket["slot"], "request_sha256": ticket["request_sha256"], "tool_call_id": call_id})
        self.append_rows(self.trace, [
            {"type": "model_msg", "payload": {"type": "tool_call", "name": "run_subagent", "tool_call_id": call_id,
                "arguments": json.dumps(call, ensure_ascii=False)}},
            {"type": "event_msg", "payload": {"type": "subagent", "session_id": session_id}},
            {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": call_id,
                "stop_reason": "completed", "output": "[subagent running in background with subagent_id " + child_id + "; completion will arrive]"}},
            {"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "harness",
                "text": "[background_task_done]\nkind: subagent\nid: " + child_id + "\nstatus: completed\n[/background_task_done]"}},
        ])
        trace = self.trace.parent / (session_id + "_001.jsonl")
        self.append_rows(trace, [
            {"type": "session_meta", "payload": {**self.meta, "source": "subagent", "session_id": session_id}},
            {"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "parent_agent",
                "text": self.evo.native_prompt(call)}},
            {"type": "event_msg", "payload": {"type": "request_begin", "thinking_level": self.worker_runtime["thinking_level"]}},
            {"type": "model_msg", "payload": {"type": "text", "role": "assistant", "text": self.worker_yaml(cell)}},
            {"type": "event_msg", "payload": {"type": "request_end", "status": "completed"}},
        ])
        worker = {"trace": trace, "subagent_id": child_id, "session_id": session_id, "ticket": ticket}
        self.workers[phase, cell["case_id"]] = worker
        return worker

    def test_native_worker_fields_cannot_be_replaced_with_full_frozen_runtime(self):
        self.assertEqual(set(self.identity), {"agent_id", "expected_version", "provider", "model_id", "thinking_level"})
        self.assertEqual(set(self.cells[0]), {"protocol_version", "status", "case_id", "run", "agent_id",
            "expected_version", "provider", "model_id", "thinking_level", "score", "cost", "duration_ms", "session_id"})
        self.assertTrue(protocol.matrix(self.matrix)["complete"])
        with self.assertRaisesRegex(ValueError, "cell runtime/state mismatch"):
            protocol.matrix({**self.matrix, "identity": {**self.identity, **self.runtime}})
        self.assertEqual(set(self.call(self.test_cases[0])),
                         {"description", "prompt", "provider", "model_id", "thinking_level", "run_in_background"})

    def test_freeze_still_requires_full_runtime_despite_smaller_worker_identity(self):
        request_path = Path(self.mode["freeze_request"])
        original = protocol.read(request_path)
        self.save(request_path, {**original, "runtime": self.worker_runtime})
        with self.assertRaisesRegex(ValueError, "freeze differs from authorized runtime"):
            self.control.freeze(self.mode)
        self.save(request_path, original)
        self.assertTrue(self.control.freeze(self.mode)["complete"])

    def test_five_h0_calls_authorize_only_after_real_noop_freeze_and_final_is_reused(self):
        ledger = str(self.control.journal_path(self.scratch, self.mode))
        common = {"journal": ledger, "config_sha256": self.mode["config_sha256"]}
        self.evo.journal({**common, "action": "init", "budget_usd": 2, "controller_session_id": self.meta["session_id"]})
        reserve = {**common, "action": "reserve", "controller_trace": str(self.trace), "role": "synthetic", "reserve_usd": 0.1}
        self.evo.journal({**reserve, "operation_id": "controller", "slot": "controller", "request_sha256": "synthetic-controller"})
        for i, task in enumerate(self.test_cases):
            call = self.call(task)
            ticket = self.control.worker_request({"phase": "h0", "call": call}, self.scratch, self.mode)
            self.evo.journal({**reserve, **{key: ticket[key] for key in ("operation_id", "slot", "request_sha256")}})
            result = self.hook("pre_tool_use", tool_name="run_subagent", arguments=json.dumps(call), tool_call_id=f"synthetic-call-{i}")
            self.assertEqual(result, {})
        with self.assertRaisesRegex(ValueError, "reuse H0"):
            self.control.worker_request({"phase": "final", "call": self.call(self.test_cases[0])}, self.scratch, self.mode)
        accepted = self.control.controller_final(self.mode)
        self.assertTrue(accepted["complete"])
        self.assertTrue(accepted["equivalent_h0"])
        self.assertEqual(accepted["host_cost_status"], "pending_final_request")
        result = self.hook("stop")
        self.assertTrue(result["output"]["complete"])
        self.assertEqual(protocol.read(self.scratch / "GOAL.json")["status"], "complete")

    def test_missing_or_incomplete_training_never_authorizes_h0_or_goal_completion(self):
        original = self.f.record_path.read_bytes()
        for missing in (False, True):
            if missing:
                self.f.record_path.unlink()
            else:
                self.save(self.f.record_path, {**json.loads(original), "complete": False})
            with self.subTest(missing=missing), self.assertRaises((OSError, ValueError)):
                self.control.worker_request({"phase": "h0", "call": self.call(self.test_cases[0])}, self.scratch, self.mode)
            self.save(self.scratch / "GOAL.json", {"status": "complete", "ended": False})
            self.assertEqual(self.hook("stop")["output"]["status"], "method_failed")
            self.assertEqual(protocol.read(self.scratch / "GOAL.json")["status"], "blocked")

    def test_worker_and_completion_reject_runtime_changes_without_publishing_success(self):
        call = self.call(self.test_cases[0])
        with self.assertRaisesRegex(ValueError, "runtime"):
            self.control.worker_request({"phase": "h0", "call": {**call, "thinking_level": "low"}}, self.scratch, self.mode)
        wrong = {**self.identity, "model_id": "synthetic-wrong-model"}
        self.save(Path(self.matrix["cells"]), [{**cell, **wrong} for cell in self.cells])
        self.save(self.matrix_path, {**self.matrix, "identity": wrong})
        with self.assertRaisesRegex(ValueError, "runtime mismatch|worker result differs"):
            self.control.controller_final(self.mode)
        self.assertEqual(self.hook("stop")["output"]["status"], "method_failed")
        self.assertEqual(protocol.read(self.scratch / "GOAL.json")["status"], "blocked")

    def test_unknown_final_host_cost_cannot_be_claimed_complete(self):
        self.save(Path(self.mode["completion_request"]), {**self.completion, "host_cost_status": "complete"})
        with self.assertRaisesRegex(ValueError, "pending"):
            self.control.controller_final(self.mode)

    def test_original_native_worker_plain_document_must_equal_its_matrix_cell(self):
        worker = self.workers["h0", self.test_cases[0]]
        trace = worker["trace"]
        original = trace.read_bytes()
        cell = self.cells[0]
        variants = ["```yaml\n" + self.worker_yaml(cell) + "```\n",
                    "The evaluation is complete.\n" + self.worker_yaml(cell),
                    self.worker_yaml({**cell, "score": 49}),
                    self.worker_yaml({key: value for key, value in cell.items() if key != "cost"})]
        for text in variants:
            with self.subTest(text_kind=text[:24]):
                rows = [json.loads(line) for line in original.split(b"\n") if line]
                next(row["payload"] for row in rows if row.get("payload", {}).get("role") == "assistant")["text"] = text
                trace.write_bytes(b"")
                self.append_rows(trace, rows)
                with self.assertRaises(ValueError):
                    self.control.controller_final(self.mode)
        trace.write_bytes(original)
        self.assertTrue(self.control.controller_final(self.mode)["complete"])

    def test_same_native_worker_may_repair_only_format_without_tools_or_value_changes(self):
        worker = self.workers["h0", self.test_cases[0]]
        trace = worker["trace"]
        rows = [json.loads(line) for line in trace.read_bytes().split(b"\n") if line]
        answer = next(row["payload"] for row in rows if row.get("payload", {}).get("role") == "assistant")
        answer["text"] = "```yaml\n" + answer["text"] + "```\n"
        trace.write_bytes(b"")
        self.append_rows(trace, rows)
        request = self.control.format_request({"subagent_id": worker["subagent_id"]}, self.meta, self.scratch, self.mode)
        self.assertFalse(request["automatic_dispatch"])
        call_id = "synthetic-format-existing-worker"
        self.assertEqual(self.hook("pre_tool_use", tool_name="input_subagent",
            arguments=json.dumps(request["call"]), tool_call_id=call_id), {})
        self.append_rows(self.trace, [
            {"type": "model_msg", "payload": {"type": "tool_call", "name": "input_subagent", "tool_call_id": call_id,
                "arguments": json.dumps(request["call"])}},
            {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": call_id,
                "stop_reason": "completed", "output": "[subagent continues in background]"}},
            {"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "harness",
                "text": "[background_task_done]\nkind: subagent\nid: " + worker["subagent_id"] + "\nstatus: completed\n[/background_task_done]"}},
        ])
        repair_rows = [
            {"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "parent_agent", "text": self.control.FORMAT_PROMPT}},
            {"type": "event_msg", "payload": {"type": "request_begin", "thinking_level": self.worker_runtime["thinking_level"]}},
            {"type": "model_msg", "payload": {"type": "text", "role": "assistant", "text": self.worker_yaml(self.cells[0])}},
            {"type": "event_msg", "payload": {"type": "request_end", "status": "completed"}},
        ]
        self.append_rows(trace, repair_rows)
        valid = trace.read_bytes()
        self.assertTrue(self.control.controller_final(self.mode)["complete"])
        changed = [json.loads(line) for line in valid.split(b"\n") if line]
        next(row["payload"] for row in reversed(changed) if row.get("payload", {}).get("role") == "assistant")["text"] = self.worker_yaml({**self.cells[0], "score": 51})
        trace.write_bytes(b"")
        self.append_rows(trace, changed)
        with self.assertRaises(ValueError):
            self.control.controller_final(self.mode)
        trace.write_bytes(valid)
        self.append_rows(trace, [{"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command",
            "tool_call_id": "forbidden-repair-work", "arguments": json.dumps({"cmd": "echo SYNTHETIC"})}}])
        with self.assertRaisesRegex(ValueError, "tools"):
            self.control.controller_final(self.mode)

    def test_five_failures_reflect_compile_and_two_frozen_matrices_verify_actual_injection(self):
        fixture = ControllerAcceptanceTests("test_five_h0_calls_authorize_only_after_real_noop_freeze_and_final_is_reused")
        self.addCleanup(fixture.doCleanups)
        fixture.all_success = False
        fixture.setUp()
        self.assertEqual(len(fixture.f.contexts), 5)
        self.assertEqual(len([role for role in fixture.f.roles if role["role"] == "reflector"]), 5)
        self.assertEqual(len(fixture.f.candidates), 5)
        self.assertFalse(fixture.f.package["empty"])
        final_cells = [{**cell, "expected_version": 2, "session_id": cell["session_id"] + "-final"}
                       for cell in fixture.cells]
        for cell in final_cells:
            test_injection.write_trial(fixture.benchmark, cell, acceptance.snapshot(fixture.final)["files"], fixture.runtime)
        final_cells_path = fixture.save(fixture.root / "final-cells.json", final_cells)
        final_matrix = fixture.save(fixture.scratch / "evo-guard/final-matrix-request.json", {
            **fixture.matrix, "cells": str(final_cells_path), "identity": {**fixture.identity, "expected_version": 2}})
        fixture.save(Path(fixture.mode["completion_request"]), {"h0_matrix_request": str(fixture.matrix_path),
            "final_matrix_request": str(final_matrix), "host_cost_status": "pending_final_request"})
        # Restore the actual frozen State before each five-cell registration.
        # No worker/model is dispatched by these deterministic ticket checks.
        for phase, archive, version in (("h0", fixture.h0, 1), ("final", fixture.final, 2)):
            fixture.restore_target(archive)
            for cell in fixture.cells if phase == "h0" else final_cells:
                worker = fixture.record_worker(phase, cell)
                self.assertFalse(worker["ticket"]["dispatched"])
        evaluations = [protocol.matrix(req)["evaluation"]
                       for req in (fixture.matrix, protocol.read(final_matrix))]
        self.assertEqual([entry["version"] for entry in evaluations], [1, 2])
        for entry in evaluations:
            self.assertEqual(set(entry), {"time", "agent_id", "version", "provider", "model_id", "thinking_level",
                "summary_title", "summary", "score", "cost", "duration_ms", "cases"})
            self.assertEqual(len(entry["cases"]), 5)
            self.assertTrue(all(len(case["runs"]) == 1 for case in entry["cases"]))
        # JSON is a valid plain YAML document; retain the exact native matrix
        # records. The native YAML-parser roundtrip has its own offline test.
        scoreboard = fixture.save(fixture.benchmark / "scoreboard.yaml", {"evaluations": evaluations})
        self.assertEqual(protocol.read(scoreboard)["evaluations"], evaluations)
        accepted = fixture.control.controller_final(fixture.mode)
        self.assertTrue(accepted["complete"])
        self.assertFalse(accepted["equivalent_h0"])
        self.assertEqual(len(accepted["injection"]), 2)
        self.assertEqual(sum(len(phase["cells"]) for phase in accepted["injection"]), 10)
        self.assertTrue(all(cell["bytes"] == 0 for cell in accepted["injection"][0]["cells"]))
        self.assertTrue(all(cell["bytes"] > 0 for cell in accepted["injection"][1]["cells"]))


if __name__ == "__main__":
    unittest.main()
