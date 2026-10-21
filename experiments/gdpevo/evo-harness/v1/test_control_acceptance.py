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


class ControllerAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-controller-acceptance-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tool = Path(os.environ["EVO_TOOL"]).resolve()
        sys.path.insert(0, str(self.tool.parent))
        self.addCleanup(lambda: sys.path.remove(str(self.tool.parent)))
        spec = importlib.util.spec_from_file_location("synthetic_control_acceptance", self.tool.with_name("control.py"))
        self.control = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.control)
        self.runtime = {"provider": "synthetic", "model_id": "no-model", "thinking_level": "high"}
        self.config_path = self.root / "synthetic-config.json"
        self.config = {**protocol.read(Path(__file__).with_name("profile.json")),
            "synthetic": True, "paid_authorized": True, "budget_usd": 2, "runtime": self.runtime,
            "benchmark_id": "synthetic-training-benchmark", "test_benchmark_id": "synthetic-test-benchmark",
            "trainer_agent_id": "synthetic-trainer", "test_agent_id": "synthetic-target",
            "deployment": {"penguin_home": str(self.root / "penguin"), "project_id": "synthetic-project",
                           "benchmark_checkout": str(Path(__file__).resolve().parents[4])}}
        self.save(self.config_path, self.config)
        fixture = test_protocol.ExportEvidenceTests("test_sealed_five_task_export_survives_relocation_using_original_traces")
        fixture.all_success = True
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
        (self.target / "system_config.yaml").write_text("version: 1\nsystem_prompt: SYNTHETIC frozen target\nhooks:\n  enabled: false\n")
        (self.target / "AGENTS.md").write_text("SYNTHETIC fixed target instructions\n")
        self.h0 = self.root / "h0.tar.gz"
        self.final = self.root / "final.tar.gz"
        for archive in (self.h0, self.final):
            with tarfile.open(archive, "w:gz") as handle:
                handle.add(self.target, arcname="agent_state")
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
            "freeze_request": str(self.scratch / "evo-guard/freeze-request.json"),
            "completion_request": str(self.scratch / "evo-guard/completion-request.json")}
        self.save(self.scratch / self.control.MARKER, self.mode)
        exported = self.save(self.root / "export-request.json", {
            "evo_tool": str(self.tool), "submission": str(fixture.submission),
            "training_export": str(fixture.record_path), "train_ids": fixture.train,
            "private_learning": str(fixture.private), "trace_root": str(fixture.traces), "runtime": self.runtime})
        self.save(Path(self.mode["freeze_request"]), {"evo_tool": str(self.tool), "export_request": str(exported),
            "h0_snapshot": str(self.h0), "final_snapshot": str(self.final), "h0_version": 1, "final_version": 1,
            "target_state": str(self.target), "test_case_ids": self.config["test_ids"], "runtime": self.runtime})
        self.identity = {"agent_id": "synthetic-target", "expected_version": 1, **self.runtime}
        self.cells = [{"protocol_version": 1, **self.identity, "case_id": task, "run": 1, "status": "ok",
            "score": 50, "cost": 0, "duration_ms": 1, "session_id": f"synthetic-test-{i}"}
            for i, task in enumerate(self.config["test_ids"])]
        cells = self.save(self.root / "synthetic-cells.json", self.cells)
        self.matrix = {"evo_tool": str(self.tool), "cells": str(cells), "case_ids": self.config["test_ids"],
            "runs": 1, "identity": self.identity, "time": "2026-10-21T00:00:00Z",
            "summary_title": "SYNTHETIC", "summary": "Constant fixtures, not benchmark scores"}
        self.matrix_path = self.save(self.scratch / "evo-guard/h0-matrix-request.json", self.matrix)
        self.completion = {"h0_matrix_request": str(self.matrix_path), "final_policy": "reused_equivalent_h0",
                           "host_cost_status": "pending_final_request"}
        self.save(Path(self.mode["completion_request"]), self.completion)
        self.save(self.scratch / "GOAL.json", {"status": "complete", "ended": False})

    @staticmethod
    def save(path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())
        return path

    def hook(self, event, **extra):
        return self.control.hook({"hook": event, "trace_path": str(self.trace), **extra}, self.state)

    def call(self, task):
        prompt = "\n".join(f"{key}: {value}" for key, value in {
            "benchmark_id": self.config["test_benchmark_id"], "test_agent_id": "synthetic-target",
            "case_id": task, "run": 1, "expected_version": 1, **self.runtime}.items())
        return {"description": "SYNTHETIC test worker", "prompt": prompt, **self.runtime, "run_in_background": True}

    def test_five_h0_calls_authorize_only_after_real_noop_freeze_and_final_is_reused(self):
        ledger = str(self.control.journal_path(self.scratch, self.mode))
        common = {"journal": ledger, "config_sha256": self.mode["config_sha256"]}
        self.evo.journal({**common, "action": "init", "budget_usd": 2, "controller_session_id": self.meta["session_id"]})
        reserve = {**common, "action": "reserve", "controller_trace": str(self.trace), "role": "synthetic", "reserve_usd": 0.1}
        self.evo.journal({**reserve, "operation_id": "controller", "slot": "controller", "request_sha256": "synthetic-controller"})
        for i, task in enumerate(self.config["test_ids"]):
            call = self.call(task)
            ticket = self.control.worker_request({"phase": "h0", "call": call}, self.scratch, self.mode)
            self.evo.journal({**reserve, **{key: ticket[key] for key in ("operation_id", "slot", "request_sha256")}})
            result = self.hook("pre_tool_use", tool_name="run_subagent", arguments=json.dumps(call), tool_call_id=f"synthetic-call-{i}")
            self.assertEqual(result, {})
        with self.assertRaisesRegex(ValueError, "reuse H0"):
            self.control.worker_request({"phase": "final", "call": self.call(self.config["test_ids"][0])}, self.scratch, self.mode)
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
                self.control.worker_request({"phase": "h0", "call": self.call(self.config["test_ids"][0])}, self.scratch, self.mode)
            self.save(self.scratch / "GOAL.json", {"status": "complete", "ended": False})
            self.assertEqual(self.hook("stop")["output"]["status"], "method_failed")
            self.assertEqual(protocol.read(self.scratch / "GOAL.json")["status"], "blocked")

    def test_worker_and_completion_reject_runtime_changes_without_publishing_success(self):
        call = self.call(self.config["test_ids"][0])
        with self.assertRaisesRegex(ValueError, "runtime"):
            self.control.worker_request({"phase": "h0", "call": {**call, "thinking_level": "low"}}, self.scratch, self.mode)
        wrong = {**self.identity, "model_id": "synthetic-wrong-model"}
        self.save(Path(self.matrix["cells"]), [{**cell, **wrong} for cell in self.cells])
        self.save(self.matrix_path, {**self.matrix, "identity": wrong})
        with self.assertRaisesRegex(ValueError, "runtime mismatch"):
            self.control.controller_final(self.mode)
        self.assertEqual(self.hook("stop")["output"]["status"], "method_failed")
        self.assertEqual(protocol.read(self.scratch / "GOAL.json")["status"], "blocked")

    def test_unknown_final_host_cost_cannot_be_claimed_complete(self):
        self.save(Path(self.mode["completion_request"]), {**self.completion, "host_cost_status": "complete"})
        with self.assertRaisesRegex(ValueError, "pending"):
            self.control.controller_final(self.mode)


if __name__ == "__main__":
    unittest.main()
