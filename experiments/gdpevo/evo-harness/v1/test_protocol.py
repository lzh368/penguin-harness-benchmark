"""SYNTHETIC offline tests, never benchmark outcomes."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import shlex
import tempfile
import unittest
from unittest.mock import patch
import protocol


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-evo-protocol-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tool = os.environ["EVO_TOOL"]
        self.identity = {"agent_id": "synthetic-target", "expected_version": 1, "provider": "fixture",
                         "model_id": "no-model", "thinking_level": "high"}
        self.case_ids = [f"case-{n}" for n in range(1, 6)]
        self.cells = [{"protocol_version": 1, **self.identity, "case_id": c, "run": 1,
                       "status": "ok", "score": 20 if n == 0 else 100, "cost": 0,
                       "duration_ms": 10, "session_id": f"synthetic-{n}"} for n, c in enumerate(self.case_ids)]
        self.file = self.root / "cells.json"
        self.req = {"evo_tool": self.tool, "cells": str(self.file), "case_ids": self.case_ids, "runs": 1,
                    "identity": self.identity, "time": "2026-10-21T00:00:00Z",
                    "summary_title": "SYNTHETIC", "summary": "Not real model scores"}

    def calculate(self):
        self.file.write_text(json.dumps(self.cells))
        return protocol.matrix(self.req)

    def test_full_five_member_mean_uses_all_scores(self):
        result = self.calculate()
        self.assertEqual(result["evaluation"]["score"], 84)
        self.assertEqual(len(result["evaluation"]["cases"]), 5)

    def test_missing_failed_or_foreign_cells_not_zero_filled(self):
        self.cells.pop(1)
        result = self.calculate()
        self.assertFalse(result["complete"])
        self.assertIsNone(result["evaluation"])
        self.assertEqual(result["missing"], [{"case_id": "case-2", "run": 1}])
        self.cells[0]["case_id"] = "foreign-test"
        with self.assertRaises(ValueError):
            self.calculate()

    def test_valid_zero_and_unknown_cost_preserved(self):
        self.cells[0]["score"] = 0
        self.cells[0]["cost"] = None
        result = self.calculate()
        self.assertEqual(result["evaluation"]["score"], 80)
        self.assertFalse(result["cost_complete"])
        self.assertIsNone(result["evaluation"]["cases"][0]["cost"])

    def test_runtime_version_and_replica_identity(self):
        for field in self.identity:
            original = self.cells[0][field]
            self.cells[0][field] = "wrong"
            with self.assertRaisesRegex(ValueError, "mismatch"):
                self.calculate()
            self.cells[0][field] = original
        self.cells.append(self.cells[0].copy())
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.calculate()

    def test_missing_replica_slot_is_not_shifted(self):
        self.req["runs"] = 2
        self.cells[0]["run"] = 2
        result = self.calculate()
        self.assertIn({"case_id": "case-1", "run": 1}, result["missing"])

    def test_independent_test_cells_cannot_reuse_one_session(self):
        self.cells[1]["session_id"] = self.cells[0]["session_id"]
        with self.assertRaisesRegex(ValueError, "fresh sessions"):
            self.calculate()

    def test_profile_checks_real_public_task_metadata_without_private_inputs(self):
        here = Path(__file__).resolve().parent
        config = protocol.read(here / "profile.json")
        repo = here.parents[3]
        result = protocol.validate_config(config, repo)
        self.assertEqual([t["solving_timeout_sec"] for t in result["tasks"]], [21600] + [14400] * 5)
        self.assertFalse(result["paid_authorized"])
        config["train_ids"].pop()
        with self.assertRaises(ValueError):
            protocol.validate_config(config, repo)

    def test_frozen_concurrency_is_five_tests_and_one_training(self):
        here = Path(__file__).resolve().parent
        config = protocol.read(here / "profile.json")
        self.assertEqual(config["train_concurrency"], 1)
        self.assertEqual(config["test_concurrency"], 5)
        protocol.validate_config(config, here.parents[3])
        for field, values in (("test_concurrency", [1, 4, 6, 8, True]),
                              ("train_concurrency", [0, 2, 5, True])):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "concurrent tests"):
                    protocol.validate_config({**config, field: value}, here.parents[3])

    def test_frozen_test_order_requires_training_and_freezing_first(self):
        here = Path(__file__).resolve().parent
        config = protocol.read(here / "profile.json")
        self.assertEqual(config["test_phase_order"], ["train_compile_freeze", "h0_test", "final_test"])
        for order in (["h0_test", "train_compile_freeze", "final_test"],
                      ["train_compile_freeze", "final_test", "h0_test"], []):
            with self.subTest(order=order), self.assertRaisesRegex(ValueError, "freezing before"):
                protocol.validate_config({**config, "test_phase_order": order}, here.parents[3])

    def test_native_state_pack_preserves_whole_learned_skill_without_scratch(self):
        evo = protocol.helper(self.tool)
        library = self.root / "library.json"
        library.write_text("[]")
        base = self.root / "base"
        evo.export_skill({"library": str(library), "limits": {"general": 5, "topic": 5},
                          "max_entries": 5, "max_bytes": 16000, "output": str(base / "skills/evo-harness-learned")})
        (base / "system_config.yaml").write_text("version: 1\nmemory:\n  enabled: false\nhooks:\n  enabled: false\n")
        (base / "AGENTS.md").write_text("SYNTHETIC fixed reader\n")
        here = Path(__file__).resolve().parent
        trainer = self.root / "trainer"
        # Reproduce the native installer's on-disk package from the real plugin
        # manifest. Real installPlugin execution is covered by the Penguin TS
        # integration suite, not claimed by this Python preparation fixture.
        plugin = Path(self.tool).resolve().parents[3]
        manifest = protocol.read(plugin / "package.json")
        hooks = self.root / "installed-method-hooks"
        hooks.mkdir()
        shutil.copyfile(plugin / "hooks/guard.mjs", hooks / "guard.mjs")
        shutil.copyfile(plugin / "icon.svg", hooks / "icon.svg")
        hook_config = {"name": manifest["name"].split("/")[-1], "description": manifest["description"],
                       **manifest["penguin"]["hooks"]}
        (hooks / "hooks.json").write_text(json.dumps(hook_config) + "\n")
        request = {"config": str(here / "profile.json"), "repo": str(here.parents[3]),
                   "evo_tool": self.tool, "base_state": str(base), "output": str(trainer),
                   "method_skill": str(Path(self.tool).parents[1]), "method_hooks": str(hooks), "training_budget_usd": 2,
                   "host_operation_id": "synthetic-training-op",
                   "config_sha256": hashlib.sha256((here / "profile.json").read_bytes()).hexdigest()}
        prepared = protocol.prepare_trainer(request)
        self.assertEqual(prepared["training"]["training_budget_usd"], 2)
        self.assertEqual(prepared["training"]["host_operation_id"], "synthetic-training-op")
        self.assertEqual(prepared["training"]["config_sha256"], request["config_sha256"])
        self.assertEqual(prepared["training"]["private_learning"], "/logs/agent/evo-harness")
        self.assertEqual(prepared["training"]["answer_dir"], "/app/submission/answers")
        self.assertEqual(prepared["training"]["feedback_dir"], "/logs/agent/evo-harness/feedback")
        self.assertIn("enabled: true", (trainer / "system_config.yaml").read_text())
        archive = self.root / "state.tar.gz"
        protocol.pack_state({"state": str(trainer), "output": str(archive), "evo_tool": self.tool})
        import tarfile
        with tarfile.open(archive) as f:
            names = f.getnames()
        self.assertIn("skills/evo-harness-learned/select.py", names)
        self.assertIn("skills/evo-harness-rsi-toolkit/training.json", names)
        self.assertIn("hooks/evo-harness-rsi-toolkit/hooks.json", names)
        self.assertIn("hooks/evo-harness-rsi-toolkit/guard.mjs", names)
        self.assertNotIn(".vault.toml", names)
        missing_budget = {**request, "output": str(self.root / "missing-budget")}
        missing_budget.pop("training_budget_usd")
        with self.assertRaises((KeyError, ValueError)):
            protocol.prepare_trainer(missing_budget)
        with self.assertRaises(ValueError):
            protocol.prepare_trainer({**request, "output": str(self.root / "wrong-config"), "config_sha256": "SYNTHETIC-WRONG"})
        with self.assertRaises(ValueError):
            protocol.prepare_trainer({**request, "output": str(self.root / "zero-budget"), "training_budget_usd": 0})
        paid = {**protocol.read(here / "profile.json"), "paid_authorized": True, "budget_usd": 15,
                "budget_allocations_usd": {"continuous_training_including_all_children": 5}}
        paid_path = self.root / "synthetic-paid-config.json"
        paid_path.write_text(json.dumps(paid))
        paid_request = {**request, "config": str(paid_path), "config_sha256": protocol.sha(paid_path),
                        "output": str(self.root / "invalid-paid-trainer")}
        with self.assertRaisesRegex(ValueError, "exceeds authorized"):
            protocol.prepare_trainer({**paid_request, "training_budget_usd": 16})
        with self.assertRaisesRegex(ValueError, "differs from frozen"):
            protocol.prepare_trainer({**paid_request, "training_budget_usd": 4})
        self.assertFalse(Path(paid_request["output"]).exists())
        (base / ".vault.toml").write_text("SYNTHETIC forbidden")
        with self.assertRaises(ValueError):
            protocol.prepare_trainer({**request, "output": str(self.root / "another")})


class ExportEvidenceTests(unittest.TestCase):
    """SYNTHETIC original native traces; no claim that a model followed them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-evo-export-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / "container"
        self.private = self.run / "private"
        self.traces = self.run / "traces"
        self.submission = self.run / "submission"
        self.private.mkdir(parents=True)
        self.traces.mkdir()
        self.tool = os.environ["EVO_TOOL"]
        self.installed_state = self.run / "agent_state"
        self.installed_helper = self.installed_state / "skills/evo-harness-rsi-toolkit/scripts/evo.py"
        self.installed_helper.parent.mkdir(parents=True)
        shutil.copyfile(self.tool, self.installed_helper)
        self.evo = protocol.helper(self.installed_helper)
        self.runtime = {"provider": "synthetic", "model_id": "no-model", "thinking_level": "high"}
        self.train = [f"train_{i:03d}" for i in range(1, 6)]
        self.training = {"guard_version": 2, "train_ids": self.train, "attempt_id": "attempt-1",
                         "config_sha256": getattr(self, "config_sha256", hashlib.sha256(b"SYNTHETIC frozen protocol").hexdigest()),
                         "private_learning": str(self.private), "answer_dir": str(self.submission / "answers"),
                         "feedback_dir": str(self.private / "feedback"), "runtime": self.runtime,
                         "workspace": str(self.run), "training_budget_usd": 2}
        self.save(self.installed_helper.parent.parent / "training.json", self.training)
        self.parent = self.traces / "synthetic-parent.jsonl"
        self.root_binding = {"session_id": "synthetic-training-root", "provider": "synthetic", "model_id": "no-model",
                             "source": "cli", "agent_state": str(self.installed_state), "workspace": str(self.run)}
        sessions = [f"synthetic-solver-{i}" for i in range(1, 6)]
        if not getattr(self, "all_success", False):
            sessions += [f"synthetic-reflector-{i}" for i in range(1, 6)]
        sessions += ["synthetic-evolver"]
        self.write_trace(self.parent, [{"type": "session_meta", "payload": self.root_binding}] + [{"type": "event_msg", "payload": {
            "type": "subagent", "session_id": s}} for s in sessions])
        self.contexts, self.capture_requests, self.candidates, self.roles = [], [], [], []
        self.role_text = self.save_text(self.private / "roles.txt", "SYNTHETIC role instructions.\n" + "Read all bounded material.\n" * 180)
        for i, task in enumerate(self.train, 1):
            inp = self.save_text(self.private / f"{task}-input.txt", "SYNTHETIC public task\n" + "fictional record_id value\n" * 200)
            answer = self.save(self.submission / "answers" / f"{task}.json", {"synthetic": True, "answer": i})
            paths = self.evo.judge_paths(self.training, task, "attempt-1")
            judge_request = {"op": "judge", "split": "train", "task_id": task, "attempt_id": "attempt-1",
                             "train_ids": self.train, "url": "http://127.0.0.1:9005/api/judge",
                             "answer": str(answer), "output": paths["output"]}
            judge_path = self.save(paths["judge_request"], judge_request)
            with patch.object(self.evo.urllib.request, "build_opener") as opener:
                response = opener.return_value.open.return_value.__enter__.return_value
                response.status = 200
                correct = getattr(self, "all_success", False)
                response.read.return_value = json.dumps({"score": 1 if correct else 0.5,
                                                         "correct": correct, "synthetic": True}).encode()
                feedback = self.evo.judge(judge_request, str(judge_path))
            fb = Path(paths["output"])
            binding = self.binding(f"synthetic-solver-{i}")
            grant = {"evo_task": {"split": "train", "task_id": task, "attempt_id": "attempt-1", "inputs": [str(inp)]}}
            trace = self.traces / f"synthetic-solver-{i}.jsonl"
            rows = self.trace_rows(binding, [inp], [], json.dumps(grant))
            rows += self.pair("synthetic-judge", json.dumps(self.evo.judge_stdout(feedback)), arguments={
                "cmd": shlex.join(["python3", "-B", str(self.installed_helper), str(judge_path)])})
            for offset in range(0, max(len(fb.read_text()), 1), 4000):
                read_request = self.save(self.private / "requests" / f"read-feedback-{task}-{offset}.json",
                                         {"op": "read-material", "path": str(fb), "offset": offset})
                rows += self.pair(f"synthetic-feedback-{offset}", json.dumps(self.evo.material({"path": str(fb), "offset": offset})),
                                 arguments={"cmd": shlex.join(["python3", "-B", str(self.installed_helper), str(read_request)])})
            self.write_trace(trace, rows)
            ctx = self.private / f"{task}-context.json"
            request = {"op": "capture", "split": "train", "task_id": task, "attempt_id": "attempt-1", "train_ids": self.train,
                       "answer": str(answer), "feedback": str(fb), "judge_request": str(judge_path),
                       "inputs": [str(inp)], "traces": [str(trace)],
                       "binding": binding, "output": str(ctx)}
            self.evo.capture(request)
            self.capture_requests.append(request)
            self.contexts.append(str(ctx))
            candidate = {"id": f"synthetic-candidate-{i}", "task_id": task, "attempt_id": "attempt-1",
                         "lesson": "SYNTHETIC inspect documented join keys", "trigger": "fictional join task", "scope_hint": "synthetic only",
                         "evidence": [{"kind": "input", "sha256": self.sha(inp), "claim": "SYNTHETIC documented key", "epistemic": "observed"}]}
            if not correct:
                self.candidates.append(candidate)
                output = self.save(self.private / f"reflector-{i}.json", [candidate])
                self.roles.append(self.role("reflector", f"synthetic-reflector-{i}", [self.role_text, ctx], output, task))
        self.library = self.save(self.private / "prebatch-library.json", [])
        self.candidate_path = self.save(self.private / "candidates.json", self.candidates)
        entry = {"id": "synthetic-join", "level": "topic", "topic": "synthetic", "trigger": "fictional join task",
                 "scope": "synthetic only", "body": "SYNTHETIC inspect documented keys; join rows; report unmatched rows."}
        self.decisions = self.save(self.private / "decisions.json", [{"op": "ADD", "candidate_ids": [c["id"] for c in self.candidates],
             "reason": "SYNTHETIC shared procedure", "entry": entry}] if self.candidates else [])
        self.roles.append(self.role("evolver", "synthetic-evolver",
                                   [self.role_text, self.candidate_path, self.library] + [Path(p) for p in self.contexts], self.decisions))
        self.limits = {"general": 5, "topic": 5}
        self.compiled = self.private / "compiled.json"
        self.evo.compile_batch({"train_ids": self.train, "contexts": self.contexts, "candidates": str(self.candidate_path),
             "decisions": str(self.decisions), "library": str(self.library), "limits": self.limits, "output": str(self.compiled)})
        self.package = self.evo.export_skill({"library": str(self.compiled), "limits": self.limits,
             "max_entries": 5, "max_bytes": 16000, "output": str(self.submission / "skill")})
        self.record_path = self.private / "training-export.json"
        self.seal_request = {"private_learning": str(self.private), "train_ids": self.train, "contexts": self.contexts,
             "candidates": str(self.candidate_path), "decisions": str(self.decisions), "library": str(self.library),
             "roles": self.roles, "skill": str(self.submission / "skill"), "limits": self.limits,
             "max_entries": 5, "max_bytes": 16000, "runtime": self.runtime, "output": str(self.record_path)}

    @staticmethod
    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    @staticmethod
    def save(path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        return path

    @staticmethod
    def save_text(path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    @staticmethod
    def write_trace(path, rows):
        Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows))

    def binding(self, session):
        return {"session_id": session, "provider": "synthetic", "model_id": "no-model", "source": "subagent",
                "workspace": str(self.run), "agent_state": str(self.installed_state), "parent_trace": str(self.parent)}

    @staticmethod
    def pair(call_id, output, name="exec_command", arguments=None):
        return [{"type": "model_msg", "payload": {"type": "tool_call", "tool_call_id": call_id, "name": name,
                    "arguments": json.dumps(arguments or {"cmd": "SYNTHETIC fixture; no model execution"})}},
                {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": call_id, "output": output}}]

    def trace_rows(self, binding, materials, outputs, user_text="SYNTHETIC learning role"):
        rows = [{"type": "session_meta", "payload": binding},
                {"type": "model_msg", "payload": {"type": "text", "role": "user", "text": user_text}}]
        for n, material in enumerate(materials):
            text = Path(material).read_text()
            for offset in range(0, max(len(text), 1), 4000):
                end = min(offset + 4000, len(text))
                receipt = {"material_path": str(material), "sha256": self.sha(material), "offset": offset,
                           "end": end, "total": len(text), "text": text[offset:end]}
                rows += self.pair(f"synthetic-material-{n}-{offset}", json.dumps(receipt))
        for n, output in enumerate(outputs):
            rows += self.pair(f"synthetic-output-{n}", json.dumps({"file_path": str(output), "sha256": self.sha(output)}))
        return rows

    def role(self, name, session, materials, output, task=None):
        binding = self.binding(session)
        trace = self.traces / f"{session}.jsonl"
        self.write_trace(trace, self.trace_rows(binding, materials, [output]))
        result = {"role": name, "traces": [str(trace)], "binding": binding, "materials": [str(p) for p in materials],
                  "outputs": [str(output)], "role_text_path": str(self.role_text)}
        if task is not None:
            result["task_id"] = task
        return result

    def verify(self, run=None):
        run = run or self.run
        return protocol.verify_export({"evo_tool": self.tool, "submission": str(run / "submission"),
            "training_export": str(run / "private/training-export.json"), "train_ids": self.train,
            "private_learning": str(run / "private"), "trace_root": str(run / "traces"), "runtime": self.runtime})

    def test_sealed_five_task_export_survives_relocation_using_original_traces(self):
        self.evo.seal_training(self.seal_request)
        moved = self.root / "exported-harbor-artifacts"
        shutil.move(str(self.run), moved)
        self.assertFalse(self.run.exists())
        self.assertEqual(self.verify(moved), self.package)

    def test_export_summary_without_context_or_role_evidence_is_rejected(self):
        minimal = {"complete": True, "members": {task: {"answer_sha256": self.sha(self.submission / "answers" / f"{task}.json")}
                   for task in self.train}, "skill_sha256": self.package["sha256"]}
        self.save(self.record_path, minimal)
        with self.assertRaises((KeyError, ValueError)):
            self.verify()

    def test_partial_material_chunk_and_unsealed_output_are_rejected(self):
        role = self.roles[0]
        trace = Path(role["traces"][0])
        original = trace.read_text()
        rows = [json.loads(r) for r in original.splitlines()]
        for dropped in ("synthetic-material-0-4000", "synthetic-output-0"):
            self.write_trace(trace, [r for r in rows if r.get("payload", {}).get("tool_call_id") != dropped])
            with self.assertRaisesRegex(ValueError, "fully read|not sealed"):
                self.evo.audit_role(role)
        trace.write_text(original)

    def test_wrong_parent_reference_and_changed_role_output_are_rejected(self):
        self.parent.write_text("")
        with self.assertRaisesRegex(ValueError, "referenced"):
            self.evo.audit_role(self.roles[0])
        self.write_trace(self.parent, [{"type": "event_msg", "payload": {"type": "subagent", "session_id": "synthetic-reflector-1"}}])
        self.save(Path(self.roles[0]["outputs"][0]), [])
        with self.assertRaisesRegex(ValueError, "not sealed"):
            self.evo.audit_role(self.roles[0])

    def test_missing_exported_trace_and_changed_answer_are_rejected(self):
        self.evo.seal_training(self.seal_request)
        trace = Path(self.roles[0]["traces"][0])
        original = trace.read_bytes()
        trace.unlink()
        with self.assertRaisesRegex(ValueError, "trace missing"):
            self.verify()
        trace.write_bytes(original)
        self.save(self.submission / "answers" / "train_001.json", {"synthetic": "changed answer"})
        with self.assertRaisesRegex(ValueError, "answer changed"):
            self.verify()

    def test_capture_requires_exact_task_grant_and_complete_material_reads(self):
        request = self.capture_requests[0]
        changed = {**request, "output": str(self.private / "unbound-context.json")}
        extra = self.save_text(self.private / "synthetic-heldout.txt", "SYNTHETIC held-out evidence\n")
        with self.assertRaisesRegex(ValueError, "not bound"):
            self.evo.capture({**changed, "inputs": [str(extra)]})
        trace = Path(request["traces"][0])
        rows = [json.loads(r) for r in trace.read_text().splitlines()]
        self.write_trace(trace, [r for r in rows if r.get("payload", {}).get("tool_call_id") != "synthetic-material-0-4000"])
        with self.assertRaisesRegex(ValueError, "fully read"):
            self.evo.capture(changed)

    def test_context_answer_and_input_body_tampering_are_rejected(self):
        context = json.loads(Path(self.contexts[0]).read_text())
        for field in ("answer", "input"):
            changed = copy.deepcopy(context)
            if field == "answer":
                changed["answer"] = {"synthetic": "different answer"}
            else:
                changed["inputs"][0]["text"] = "SYNTHETIC changed facts"
            with self.assertRaisesRegex(ValueError, "captured answer|captured input"):
                self.evo.validate_contexts([changed] + [json.loads(Path(p).read_text()) for p in self.contexts[1:]], self.train)

    def test_workspace_capture_accepts_full_native_read_file_and_rejects_truncation(self):
        workspace = self.root / "workspace"
        inp = self.save_text(workspace / "README.md", "SYNTHETIC public task\nSecond required line\n")
        answer = self.save(workspace / "answer.json", {"synthetic": True})
        binding = {**self.binding("synthetic-workspace"), "source": "cli", "workspace": str(workspace)}
        trace = self.traces / "synthetic-workspace.jsonl"
        rows = [{"type": "session_meta", "payload": binding}]
        rows += self.pair("synthetic-read-file", "1\tSYNTHETIC public task\n2\tSecond required line\n", "read_file", {"file_path": "README.md"})
        self.write_trace(trace, rows)
        identity = {"agent_id": "synthetic-workspace-target", "provider": "synthetic", "model_id": "no-model",
                    "thinking_level": "high", "expected_version": 1, "run": 1}
        evaluation = self.save(self.private / "workspace-evaluation.json", {"protocol_version": 1, "status": "ok", "case_id": "workspace-task",
             **identity, "score": 50, "session_id": binding["session_id"], "cost": None, "duration_ms": 1})
        request = {"op": "capture-evaluation", "split": "train", "task_id": "workspace-task", "attempt_id": "attempt-1", "train_ids": ["workspace-task"],
             "answer": str(answer), "evaluation": str(evaluation), "evaluation_identity": identity, "inputs": [str(inp)],
             "traces": [str(trace)], "binding": binding, "output": str(self.private / "workspace-context.json")}
        self.assertTrue(self.evo.capture(request)["negative"])
        rows[-1]["payload"]["output"] = "1\tSYNTHETIC public task\n"
        self.write_trace(trace, rows)
        with self.assertRaisesRegex(ValueError, "not fully read"):
            self.evo.capture({**request, "output": str(self.private / "truncated-workspace-context.json")})

    def test_rehashed_context_input_must_still_match_solver_read_evidence(self):
        changed = json.loads(Path(self.contexts[0]).read_text())
        changed["inputs"][0]["text"] = "SYNTHETIC held-out replacement"
        changed["inputs"][0]["sha256"] = hashlib.sha256(changed["inputs"][0]["text"].encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, "read|input|material|grant"):
            self.evo.validate_contexts([changed] + [json.loads(Path(p).read_text()) for p in self.contexts[1:]], self.train)

    def test_self_reported_reading_without_tool_observation_is_rejected(self):
        role = self.roles[0]
        trace = Path(role["traces"][0])
        rows = [{"type": "session_meta", "payload": role["binding"]}]
        for material in role["materials"]:
            rows.append({"type": "model_msg", "payload": {"type": "text", "role": "assistant",
                         "text": "SYNTHETIC: I read this entire file: " + material}})
        rows += self.pair("synthetic-output", json.dumps({"file_path": role["outputs"][0], "sha256": self.sha(role["outputs"][0])}))
        self.write_trace(trace, rows)
        with self.assertRaisesRegex(ValueError, "not fully read"):
            self.evo.audit_role(role)

    def test_complete_hashes_cannot_substitute_uncompiled_learned_package(self):
        record = self.evo.seal_training(self.seal_request)
        changed = json.loads(self.compiled.read_text())
        changed[0]["body"] = "SYNTHETIC unrelated procedure that Evolver never authored."
        replacement = self.save(self.private / "unauthored-library.json", changed)
        shutil.rmtree(self.submission / "skill")
        package = self.evo.export_skill({"library": str(replacement), "limits": self.limits,
            "max_entries": 5, "max_bytes": 16000, "output": str(self.submission / "skill")})
        record["skill_sha256"] = package["sha256"]
        self.save(self.record_path, record)
        with self.assertRaisesRegex(ValueError, "compil|package"):
            self.verify()

    def test_rehashed_decisions_still_need_original_evolver_output(self):
        record = self.evo.seal_training(self.seal_request)
        changed = json.loads(self.decisions.read_text())
        changed[0]["reason"] = "SYNTHETIC changed after native Evolver completed"
        self.save(self.decisions, changed)
        record["decisions_sha256"] = self.sha(self.decisions)
        self.save(self.record_path, record)
        with self.assertRaisesRegex(ValueError, "not sealed"):
            self.verify()

    def test_reusing_a_learning_session_is_rejected(self):
        roles = copy.deepcopy(self.roles)
        roles.insert(1, copy.deepcopy(roles[0]))
        with self.assertRaisesRegex(ValueError, "fresh sessions"):
            self.evo.seal_training({**self.seal_request, "roles": roles})

    def test_one_solver_session_cannot_supply_five_independent_training_roles(self):
        binding = self.capture_requests[0]["binding"]
        contexts = []
        for i, request in enumerate(self.capture_requests):
            rows = [json.loads(r) for r in Path(request["traces"][0]).read_text().splitlines()]
            rows[0]["payload"] = binding
            trace = self.traces / f"synthetic-reused-solver-{i}.jsonl"
            self.write_trace(trace, rows)
            output = self.private / f"reused-solver-context-{i}.json"
            self.evo.capture({**request, "binding": binding, "traces": [str(trace)], "output": str(output)})
            contexts.append(json.loads(output.read_text()))
        with self.assertRaisesRegex(ValueError, "fresh|unique|session|Session"):
            self.evo.validate_contexts(contexts, self.train)

    def test_frozen_runtime_must_match_every_native_role(self):
        roles = copy.deepcopy(self.roles)
        role = roles[-1]
        role["binding"]["model_id"] = "synthetic-different-model"
        self.write_trace(Path(role["traces"][0]), self.trace_rows(role["binding"], role["materials"], role["outputs"]))
        with self.assertRaisesRegex(ValueError, "runtime"):
            self.evo.seal_training({**self.seal_request, "roles": roles})

    def test_role_material_mapping_cannot_replace_validated_task_context(self):
        record = self.evo.seal_training(self.seal_request)
        role = record["roles"][0]
        original = record["members"]["train_001"]["container_context_path"]
        changed = json.loads(Path(original).read_text())
        changed["answer"] = {"synthetic": "different context actually read by this Reflector"}
        shadow = self.save(self.private / "synthetic-shadow-context.json", changed)
        role["material_files"][original] = shadow.name
        # This synthetic native trace records the wrong material actually read,
        # under the same container pathname. The host must retain its earlier
        # authoritative context binding instead of accepting the role's remap.
        rows = self.trace_rows(role["binding"], [self.role_text, shadow], role["outputs"])
        for row in rows:
            payload = row.get("payload", {})
            if payload.get("type") == "tool_call_output":
                receipt = json.loads(payload["output"])
                if receipt.get("material_path") == str(shadow):
                    receipt["material_path"] = original
                    payload["output"] = json.dumps(receipt)
        self.write_trace(Path(role["traces"][0]), rows)
        self.save(self.record_path, record)
        with self.assertRaisesRegex(ValueError, "map|binding|context|material|read"):
            self.verify()

    def test_canonical_judge_export_mapping_is_complete_and_hash_bound(self):
        original = self.evo.seal_training(self.seal_request)
        task = self.train[0]
        changed = copy.deepcopy(original)
        context = protocol.read(self.contexts[0])
        del changed["members"][task]["judge_evidence_files"][context["judge_request"]["path"]]
        self.save(self.record_path, changed)
        with self.assertRaisesRegex(ValueError, "judge evidence mapping"):
            self.verify()
        self.save(self.record_path, original)
        request = Path(context["judge_request"]["path"])
        self.save(request, {**protocol.read(request), "output": str(self.private / "another-feedback.json")})
        with self.assertRaisesRegex(ValueError, "request evidence changed"):
            self.verify()

    def test_continuous_roles_cannot_be_independent_cli_roots(self):
        record = self.evo.seal_training(self.seal_request)
        role = self.roles[-1]
        role["binding"]["source"] = "cli"
        role["binding"].pop("parent_trace")
        self.write_trace(Path(role["traces"][0]), self.trace_rows(role["binding"], role["materials"], role["outputs"]))
        with self.assertRaisesRegex(ValueError, "native child roles"):
            self.evo.seal_training(self.seal_request)
        record["roles"][-1]["binding"] = role["binding"]
        self.save(self.record_path, record)
        with self.assertRaisesRegex(ValueError, "native child roles"):
            self.verify()

    def test_continuous_solver_cannot_be_an_independent_cli_root(self):
        record = self.evo.seal_training(self.seal_request)
        request = copy.deepcopy(self.capture_requests[0])
        request["binding"]["source"] = "cli"
        request["binding"].pop("parent_trace")
        trace = Path(request["traces"][0])
        rows = [json.loads(line) for line in trace.read_text().splitlines()]
        rows[0]["payload"] = request["binding"]
        self.write_trace(trace, rows)
        output = self.private / "synthetic-cli-solver-context.json"
        self.evo.capture({**request, "output": str(output)})
        with self.assertRaisesRegex(ValueError, "native child roles"):
            self.evo.seal_training({**self.seal_request, "contexts": [str(output)] + self.contexts[1:]})
        member = record["members"][self.train[0]]
        member.update(context_file=output.name, container_context_path=str(output), context_sha256=self.sha(output))
        self.save(self.record_path, record)
        with self.assertRaisesRegex(ValueError, "native child roles"):
            self.verify()

    def test_different_native_parent_is_rejected_by_seal_and_host(self):
        record = self.evo.seal_training(self.seal_request)
        role = self.roles[-1]
        parent = self.traces / "synthetic-other-parent.jsonl"
        self.write_trace(parent, [{"type": "session_meta", "payload": {
            **self.root_binding, "session_id": "synthetic-other-training-root"}},
            {"type": "event_msg", "payload": {"type": "subagent", "session_id": role["binding"]["session_id"]}}])
        role["binding"]["parent_trace"] = str(parent)
        self.write_trace(Path(role["traces"][0]), self.trace_rows(role["binding"], role["materials"], role["outputs"]))
        with self.assertRaisesRegex(ValueError, "different training roots"):
            self.evo.seal_training(self.seal_request)
        record["roles"][-1]["binding"] = role["binding"]
        record["trace_paths"].append(str(parent))
        self.save(self.record_path, record)
        with self.assertRaisesRegex(ValueError, "different training roots"):
            self.verify()

    def test_parent_state_runtime_and_record_identity_are_bound(self):
        record = self.evo.seal_training(self.seal_request)
        rows = [json.loads(line) for line in self.parent.read_text().splitlines()]
        for field in ("provider", "model_id", "workspace", "agent_state"):
            changed = copy.deepcopy(rows)
            changed[0]["payload"][field] = "SYNTHETIC changed identity"
            self.write_trace(self.parent, changed)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "State/runtime differs"):
                self.verify()
        self.write_trace(self.parent, rows)
        record["training_root"]["session_id"] = "synthetic-invented-root"
        self.save(self.record_path, record)
        with self.assertRaisesRegex(ValueError, "native training root"):
            self.verify()

    def test_parent_compaction_segments_share_session_identity_after_relocation(self):
        role = self.roles[-1]
        parent_segment = self.traces / "synthetic-parent-segment-2.jsonl"
        self.write_trace(parent_segment, [{"type": "session_meta", "payload": self.root_binding},
            {"type": "event_msg", "payload": {"type": "subagent", "session_id": role["binding"]["session_id"]}}])
        role["binding"]["parent_trace"] = str(parent_segment)
        self.write_trace(Path(role["traces"][0]), self.trace_rows(role["binding"], role["materials"], role["outputs"]))
        record = self.evo.seal_training(self.seal_request)
        self.assertEqual(record["training_root"], self.root_binding)
        moved = self.root / "exported-with-compaction"
        shutil.move(str(self.run), moved)
        self.assertEqual(self.verify(moved), self.package)

    def test_five_successes_zero_candidates_still_require_a_real_evolver_record(self):
        fixture = ExportEvidenceTests("test_sealed_five_task_export_survives_relocation_using_original_traces")
        fixture.all_success = True
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.assertEqual(fixture.candidates, [])
        self.assertEqual([role["role"] for role in fixture.roles], ["evolver"])
        self.assertEqual(protocol.read(fixture.decisions), [])
        self.assertTrue(all(not protocol.read(path)["negative"] for path in fixture.contexts))
        fixture.evo.seal_training(fixture.seal_request)
        self.assertTrue(fixture.verify()["empty"])
        with self.assertRaisesRegex(ValueError, "one Evolver"):
            fixture.evo.seal_training({**fixture.seal_request, "roles": []})


if __name__ == "__main__":
    unittest.main()
