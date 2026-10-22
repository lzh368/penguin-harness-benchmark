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

SYNTHETIC_OUTPUT_TOOLS = "max_turns: -1\ntools:\n  builtin:\n    - name: exec_command\n      maxOutputLength: 104192\n    - name: input_command\n      maxOutputLength: 104192\n"


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
                          "max_bytes": 16000, "output": str(base / "skills/evo-harness-learned")})
        (base / "system_config.yaml").write_text("version: 1\nmemory:\n  enabled: false\nhooks:\n  enabled: false\n" + SYNTHETIC_OUTPUT_TOOLS)
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
        # A self-consistent larger/smaller package and output capacity must not
        # redefine this experiment's whole-file budget before publication.
        skill_file = base / "skills/evo-harness-learned/SKILL.md"
        config_file = base / "system_config.yaml"
        skill_before, config_before = skill_file.read_bytes(), config_file.read_bytes()
        for budget in (8000, 32000):
            with self.subTest(budget=budget):
                skill_file.write_bytes(evo.render_skill([], budget))
                config_file.write_bytes(config_before.replace(b"104192", b"200192"))
                with self.assertRaisesRegex(ValueError, "exactly 16000"):
                    protocol.prepare_trainer(request)
                self.assertFalse(trainer.exists())
        skill_file.write_bytes(skill_before)
        config_file.write_bytes(config_before)
        prepared = protocol.prepare_trainer(request)
        self.assertEqual(prepared["training"]["max_bytes"], 16000)
        self.assertEqual(prepared["training"]["injection_mode"], "full_skill")
        self.assertEqual(prepared["training"]["reflection_mode"], "solver-session")
        self.assertIn("original Solver keeps its native conversation", (trainer / "AGENTS.md").read_text())
        self.assertIn("never create a separate Reflector", (trainer / "AGENTS.md").read_text())
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
        self.assertIn("skills/evo-harness-learned/SKILL.md", names)
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
        (hooks / "hooks.json").write_text(json.dumps({**hook_config, "version": "2026.10.21.11"}))
        with self.assertRaisesRegex(ValueError, "wrong native method hook package"):
            protocol.prepare_trainer({**request, "output": str(self.root / "old-method")})
        self.assertFalse((self.root / "old-method").exists())
        (hooks / "hooks.json").write_text(json.dumps(hook_config) + "\n")
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
        shutil.copyfile(Path(self.tool).with_name("inspection.py"), self.installed_helper.with_name("inspection.py"))
        self.evo = protocol.helper(self.installed_helper)
        self.runtime = getattr(self, "frozen_runtime", {"provider": "synthetic", "model_id": "no-model", "thinking_level": "high"})
        self.train = [f"train_{i:03d}" for i in range(1, 6)]
        self.training = {"guard_version": 2, "train_ids": self.train, "attempt_id": "attempt-1",
                         "config_sha256": getattr(self, "config_sha256", hashlib.sha256(b"SYNTHETIC frozen protocol").hexdigest()),
                         "private_learning": str(self.private), "answer_dir": str(self.submission / "answers"),
                         "feedback_dir": str(self.private / "feedback"), "runtime": self.runtime,
                         "workspace": str(self.run), "training_budget_usd": 2,
                         "limits": {"general": 5, "topic": 5}, "max_bytes": 16000,
                         "injection_mode": "full_skill", "reflection_mode": "solver-session"}
        self.save(self.installed_helper.parent.parent / "training.json", self.training)
        self.h0_skill = self.installed_state / "skills/evo-harness-learned/SKILL.md"
        self.h0_skill.parent.mkdir(parents=True)
        self.h0_skill.write_bytes(self.evo.render_skill([], 16000))
        self.parent = self.traces / "synthetic-training-root_001.jsonl"
        self.root_binding = {"session_id": "synthetic-training-root", "provider": "synthetic", "model_id": "no-model",
                             "source": "cli", "agent_state": str(self.installed_state), "workspace": str(self.run)}
        self.write_trace(self.parent, [{"type": "session_meta", "payload": self.root_binding}])
        self.contexts, self.capture_requests, self.candidates, self.roles = [], [], [], []
        self.role_text = self.save_text(self.private / "roles.txt", "SYNTHETIC role instructions.\n" + "Read all bounded material.\n" * 180)
        for i, task in enumerate(self.train, 1):
            inp = self.save_text(self.run / "train_tasks" / task / "input/prompt.txt", "SYNTHETIC public task\n" + "fictional record_id value\n" * 200)
            answer = self.save(self.submission / "answers" / f"{task}.json", {"synthetic": True, "answer": i})
            paths = self.evo.judge_paths(self.training, task, "attempt-1")
            judge_request = {"op": "judge", "split": "train", "task_id": task, "attempt_id": "attempt-1",
                             "train_ids": self.train, "url": "http://127.0.0.1:9005/api/judge",
                             "answer": str(answer), "output": paths["output"]}
            judge_path = self.save(paths["judge_request"], judge_request)
            correct = getattr(self, "all_success", False)
            fb = Path(paths["output"])
            session = f"synthetic-solver-{i}"
            binding = self.binding(session)
            grant = {"evo_task": {"split": "train", "task_id": task, "attempt_id": "attempt-1", "inputs": [str(inp)]}}
            contract = self.evo.role_contract(self.training, "reflector", task)
            role_file = self.save_text(Path(contract["materials"][0]), self.role_text.read_text())
            prompt = json.dumps(grant) + "\n" + json.dumps({"evo_role_seal": {
                **contract, "parent_session_id": self.root_binding["session_id"]}})
            self.register_dispatch("solver", session, task, prompt)
            trace = self.traces / f"{session}_001.jsonl"
            rows = self.trace_rows(binding, [inp, role_file], [], prompt)
            rows[1]["payload"]["sender"] = "parent_agent"
            rows.insert(2, {"type": "event_msg", "payload": {"type": "request_begin", "thinking_level": self.runtime["thinking_level"]}})
            skill_read = self.pair("synthetic-h0-read", "\n".join(
                f"{n:6}\t{line}" for n, line in enumerate(
                    self.h0_skill.read_text().removesuffix("\n").split("\n"), 1)),
                name="read_file", arguments={"file_path": str(self.h0_skill)})
            rows[3:3] = skill_read
            # Supply the same original native history before invoking the real
            # readiness gate; only the HTTP response is synthetic.
            judge_call = self.pair("synthetic-judge", "", arguments={
                "cmd": shlex.join(["python3", "-B", str(self.installed_helper), str(judge_path)])})[0]
            rows.append(judge_call)
            self.write_trace(trace, rows)
            with patch.dict(os.environ, {"PENGUIN_SESSION_ID": session}), patch.object(self.evo.urllib.request, "build_opener") as opener:
                response = opener.return_value.open.return_value.__enter__.return_value
                response.status = 200
                response.read.return_value = json.dumps({"score": 1 if correct else 0.5,
                                                         "correct": correct, "synthetic": True}).encode()
                feedback = self.evo.judge(judge_request, str(judge_path))
            self.assertEqual(feedback["status"], "ok")
            rows += self.pair("synthetic-judge", json.dumps(self.evo.judge_stdout(feedback)))[1:]
            for offset in range(0, max(len(fb.read_text()), 1), 4000):
                read_request = self.save(Path(contract["request_dir"]) / f"feedback-{offset}.json",
                                         {"op": "read-material", "path": str(fb), "offset": offset})
                rows += self.write_pair(f"write-feedback-{offset}", read_request)
                rows += self.pair(f"synthetic-feedback-{offset}", json.dumps(self.evo.material({"path": str(fb), "offset": offset})),
                                 arguments={"cmd": shlex.join(["python3", "-B", str(self.installed_helper), str(read_request)])})
            if not correct:
                candidate = {"id": task + "-attempt-1-c01", "task_id": task, "attempt_id": "attempt-1",
                             "lesson": "SYNTHETIC inspect documented join keys", "trigger": "fictional join task", "scope_hint": "synthetic only",
                             "evidence": [{"kind": "input", "sha256": self.sha(inp), "claim": "SYNTHETIC documented key", "epistemic": "observed"}]}
                proposals = [] if getattr(self, "negative_empty", False) else [candidate]
                self.candidates.extend(proposals)
                output = self.save(Path(contract["output"]), proposals)
                rows += [{"type": "model_msg", "payload": {"type": "text", "role": "assistant",
                    "text": "SYNTHETIC same-session reflection uses the observed task, execution and authorized feedback."}}]
                rows += self.write_pair("synthetic-candidates", output)
                seal_req = {"op": "seal-role-output", "role": "reflector", "task_id": task}
                seal_path = self.save(Path(contract["request_dir"]) / "seal.json", seal_req)
                rows += self.write_pair("write-seal", seal_path)
                pending = self.pair("seal-" + session, "", arguments={
                    "cmd": shlex.join(["python3", "-B", str(self.installed_helper), str(seal_path)])})
                self.write_trace(trace, rows + [pending[0]])
                with patch.dict(os.environ, PENGUIN_SESSION_ID=session):
                    receipt = self.evo.execute(seal_req, str(seal_path))
                self.assertTrue(receipt["complete"])
                pending[1]["payload"]["output"] = json.dumps(receipt)
                rows += pending
            rows += [{"type": "model_msg", "payload": {"type": "text", "role": "assistant", "text": "SYNTHETIC Solver complete"}},
                     {"type": "event_msg", "payload": {"type": "request_end", "status": "completed"}}]
            self.write_trace(trace, rows)
            self.complete_dispatch(session)
            ctx = self.private / "contexts" / (task + ".json")
            request = {"op": "capture", "split": "train", "task_id": task, "attempt_id": "attempt-1", "train_ids": self.train,
                       "answer": str(answer), "feedback": str(fb), "judge_request": str(judge_path),
                       "inputs": [str(inp)], "traces": [str(trace)], "binding": binding, "output": str(ctx)}
            captured = self.evo.capture(request)
            self.capture_requests.append(request)
            self.contexts.append(str(ctx))
            if not correct:
                self.roles.append(protocol.read(ctx)["reflection"]["audit_request"])
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
             "max_bytes": 16000, "output": str(self.submission / "skill")})
        self.record_path = self.private / "training-export.json"
        self.seal_request = {"private_learning": str(self.private), "train_ids": self.train, "contexts": self.contexts,
             "candidates": str(self.candidate_path), "decisions": str(self.decisions), "library": str(self.library),
             "roles": self.roles, "skill": str(self.submission / "skill"), "limits": self.limits,
             "max_bytes": 16000, "runtime": self.runtime, "output": str(self.record_path)}

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
                {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": call_id, "output": output, "stop_reason": "completed"}}]

    def write_pair(self, call_id, path):
        return self.pair(call_id, "SYNTHETIC wrote file", name="write_file", arguments={
            "file_path": str(path), "content": Path(path).read_text()})

    def register_dispatch(self, name, session, task, prompt):
        prompt_path = self.save_text(self.private / "dispatch" / (session + ".prompt.txt"), prompt)
        call = {"prompt_file": str(prompt_path), "prompt_sha256": self.sha(prompt_path),
                "description": "SYNTHETIC " + name, **self.runtime, "run_in_background": True}
        slot = f"solver/{task}/attempt-1" if name == "solver" else "evolver/batch-1"
        ticket = {"schema": 2, "session_id": self.root_binding["session_id"], "phase": name, "task_id": task, "slot": slot, "call": call}
        self.save(self.private / "dispatch" / (session + ".json"), ticket)
        call_id = "dispatch-" + session
        canonical = json.dumps(call, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.save(self.private / "dispatch" / (self.evo.digest(slot.encode()) + ".claimed.json"),
                  {"tool_call_id": call_id, "request_sha256": self.evo.digest(canonical.encode())})
        rows = [self.pair(call_id, "[subagent running in background with subagent_id handle-" + session + "; completion will arrive]",
                         name="run_subagent", arguments=call)[0],
                {"type": "event_msg", "payload": {"type": "subagent", "session_id": session}},
                self.pair(call_id, "[subagent running in background with subagent_id handle-" + session + "; completion will arrive]",
                          name="run_subagent", arguments=call)[1]]
        with self.parent.open("a") as handle:
            handle.write("".join(json.dumps(row) + "\n" for row in rows))

    def complete_dispatch(self, session):
        with self.parent.open("a") as handle:
            handle.write(json.dumps({"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "harness",
                "text": "[background_task_done]\nkind: subagent\nid: handle-" + session + "\nstatus: completed\n[/background_task_done]"}}) + "\n")

    def trace_rows(self, binding, materials, outputs, user_text="SYNTHETIC learning role"):
        rows = [{"type": "session_meta", "payload": binding},
                {"type": "model_msg", "payload": {"type": "text", "role": "user", "text": user_text}}]
        for n, material in enumerate(materials):
            text = Path(material).read_text()
            for offset in range(0, max(len(text), 1), 4000):
                end = min(offset + 4000, len(text))
                receipt = {"material_path": str(material), "sha256": self.sha(material), "offset": offset,
                           "end": end, "total": len(text), "text": text[offset:end]}
                request = self.save(self.private / "requests" / f"{binding['session_id']}-material-{n}-{offset}.json",
                                    {"op": "read-material", "path": str(material), "offset": offset})
                rows += self.pair(f"synthetic-material-{n}-{offset}", json.dumps(receipt),
                    arguments={"cmd": shlex.join(["python3", "-B", str(self.installed_helper), str(request)])})
        for n, output in enumerate(outputs):
            request = self.save(self.private / "requests" / f"{binding['session_id']}-output-{n}.json",
                                {"op": "file-digest", "path": str(output)})
            rows += self.pair(f"synthetic-output-{n}", json.dumps({"file_path": str(output), "sha256": self.sha(output)}),
                arguments={"cmd": shlex.join(["python3", "-B", str(self.installed_helper), str(request)])})
        return rows

    def role(self, name, session, materials, output, task=None):
        # Real deterministic self-seal execution against synthetic original
        # native traces; no provider, model or service is invoked here.
        contract = self.evo.role_contract(self.training, name, task)
        role_text = Path(contract["materials"][0])
        if not role_text.exists():
            self.save_text(role_text, self.role_text.read_text())
        self.assertEqual(str(output), contract["output"])
        materials = contract["materials"]
        binding = self.binding(session)
        trace = self.traces / f"{session}_001.jsonl"
        prompt = json.dumps({"evo_role_seal": {**contract, "parent_session_id": self.root_binding["session_id"]}})
        self.register_dispatch(name, session, task, prompt)
        rows = self.trace_rows(binding, materials, [], prompt)
        rows[1]["payload"]["sender"] = "parent_agent"
        request = {"op": "seal-role-output", "role": name, **({"task_id": task} if task else {})}
        request_path = self.save(self.private / "requests" / (session + "-seal.json"), request)
        pending = self.pair("seal-" + session, "", arguments={"cmd": shlex.join(["python3", "-B", str(self.installed_helper), str(request_path)])})
        self.write_trace(trace, rows + [pending[0]])
        with patch.dict(os.environ, PENGUIN_SESSION_ID=session):
            receipt = self.evo.execute(request, str(request_path))
        self.assertTrue(receipt["complete"])
        pending[1]["payload"]["output"] = json.dumps(receipt)
        self.write_trace(trace, rows + pending + [
            {"type": "model_msg", "payload": {"type": "text", "role": "assistant", "text": "SYNTHETIC Evolver complete"}},
            {"type": "event_msg", "payload": {"type": "request_end", "status": "completed"}}])
        self.complete_dispatch(session)
        result = {"role": name, "traces": [str(trace)], "binding": binding, "materials": materials,
                  "outputs": [str(output)], "role_text_path": str(role_text)}
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
        # Host evidence comes from immutable child USER messages and sealed
        # original traces. The container's dispatch inputs are not a new host
        # dependency, nor may an audit rewrite paths inside those traces.
        shutil.rmtree(moved / "private/dispatch")
        self.assertEqual(self.verify(moved), self.package)

    def test_original_solver_reflection_exports_without_extra_agent_dispatch(self):
        record = self.evo.seal_training(self.seal_request)
        self.assertEqual(record["reflection_mode"], "solver-session")
        dispatches = [protocol.read(path) for path in (self.private / "dispatch").glob("*.json")
                      if not path.name.endswith(".claimed.json")]
        self.assertEqual(sorted(ticket["phase"] for ticket in dispatches), ["evolver"] + ["solver"] * 5)
        for context, audit in zip(map(protocol.read, self.contexts), self.roles[:-1]):
            self.assertEqual(context["trace"]["binding"], audit["binding"])
            self.assertEqual(context["reflection"]["audit_request"], audit)
            self.assertEqual(audit["traces"], [source["path"] for source in context["trace"]["sources"]])
        self.assertEqual(self.verify(), self.package)

    def test_negative_empty_candidates_require_a_completed_same_session_reflection(self):
        fixture = ExportEvidenceTests("test_sealed_five_task_export_survives_relocation_using_original_traces")
        fixture.negative_empty = True
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.assertEqual(fixture.candidates, [])
        self.assertEqual(len(fixture.roles), 6)
        self.assertTrue(all(protocol.read(path)["negative"] for path in fixture.contexts))
        fixture.evo.seal_training(fixture.seal_request)
        self.assertTrue(fixture.verify()["empty"])
        with self.assertRaisesRegex(ValueError, "coverage"):
            fixture.evo.seal_training({**fixture.seal_request, "roles": fixture.roles[1:]})

    def test_five_successes_remain_verifiable_after_container_disappears(self):
        fixture = ExportEvidenceTests("test_sealed_five_task_export_survives_relocation_using_original_traces")
        fixture.all_success = True
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.evo.seal_training(fixture.seal_request)
        moved = fixture.root / "exported-positive-harbor-artifacts"
        shutil.move(str(fixture.run), moved)
        shutil.rmtree(moved / "private/dispatch")
        self.assertTrue(fixture.verify(moved)["empty"])

    def test_host_rejects_legacy_reflector_mode_and_another_session(self):
        record = self.evo.seal_training(self.seal_request)
        changed = copy.deepcopy(record)
        changed.pop("reflection_mode")
        self.save(self.record_path, changed)
        with self.assertRaisesRegex(ValueError, "original Solver-session"):
            self.verify()
        changed = copy.deepcopy(record)
        changed["roles"][0]["binding"]["session_id"] = "synthetic-new-reflector"
        self.save(self.record_path, changed)
        with self.assertRaisesRegex(ValueError, "original Solver binding"):
            self.verify()

    def test_capture_requires_full_feedback_before_candidate_generation(self):
        request = self.capture_requests[0]
        trace = Path(request["traces"][0])
        original = trace.read_bytes()
        rows = [json.loads(line) for line in original.splitlines()]
        feedback = [row for row in rows if row.get("payload", {}).get("tool_call_id") == "synthetic-feedback-0"]
        self.assertEqual(len(feedback), 2)
        for name, changed in (("missing", [row for row in rows if row not in feedback]),
                              ("late", [row for row in rows if row not in feedback] + feedback)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.write_trace(trace, changed)
                self.evo.capture({**request, "output": str(self.private / (name + "-feedback-context.json"))})
        trace.write_bytes(original)

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
        for dropped in ("synthetic-material-0-4000", "seal-" + role["binding"]["session_id"]):
            self.write_trace(trace, [r for r in rows if r.get("payload", {}).get("tool_call_id") != dropped])
            with self.assertRaisesRegex(ValueError, "fully read|sealed in native trace|missing original Solver reflection sealing"):
                self.evo.audit_role(role)
        trace.write_text(original)

    def test_wrong_parent_reference_and_changed_role_output_are_rejected(self):
        original = self.parent.read_bytes()
        self.parent.write_text("")
        with self.assertRaisesRegex(ValueError, "referenced"):
            self.evo.audit_role(self.roles[0])
        self.parent.write_bytes(original)
        self.save(Path(self.roles[0]["outputs"][0]), [])
        with self.assertRaisesRegex(ValueError, "sealed|candidate|reflection output"):
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

    def test_solver_h0_full_read_is_required_again_at_capture_and_compilation(self):
        request = self.capture_requests[0]
        trace = Path(request["traces"][0])
        original = trace.read_bytes()
        rows = [json.loads(line) for line in original.splitlines()]
        context = protocol.read(self.contexts[0])
        h0 = [row for row in rows if row.get("payload", {}).get("tool_call_id") == "synthetic-h0-read"]
        without = [row for row in rows if row not in h0]
        shortened = copy.deepcopy(rows)
        next(row["payload"] for row in shortened if row.get("payload", {}).get("type") == "tool_call_output"
             and row["payload"].get("tool_call_id") == "synthetic-h0-read")["output"] = "SYNTHETIC truncated H0"
        try:
            for name, changed in (("missing", without), ("truncated", shortened), ("late", without + h0)):
                with self.subTest(name=name):
                    self.write_trace(trace, changed)
                    output = self.private / ("bad-h0-" + name + ".json")
                    with self.assertRaises(ValueError):
                        self.evo.capture({**request, "output": str(output)})
                    self.assertFalse(output.exists())
                    # Rehashing the context's raw trace is not enough: compile
                    # acceptance must independently enforce the original read.
                    altered = copy.deepcopy(context)
                    altered["trace"] = self.evo.trace_view(request["traces"], request["binding"])
                    with self.assertRaises(ValueError):
                        self.evo.validate_contexts([altered] + [protocol.read(path) for path in self.contexts[1:]], self.train)
        finally:
            trace.write_bytes(original)

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
        role = self.roles[-1]
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
            "max_bytes": 16000, "output": str(self.submission / "skill")})
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
        with self.assertRaisesRegex(ValueError, "sealed in native trace"):
            self.verify()

    def test_duplicate_same_solver_reflection_audit_is_rejected(self):
        roles = copy.deepcopy(self.roles)
        roles.insert(1, copy.deepcopy(roles[0]))
        with self.assertRaisesRegex(ValueError, "duplicate|coverage|concatenation"):
            self.evo.seal_training({**self.seal_request, "roles": roles})

    def test_one_solver_session_cannot_supply_another_training_task(self):
        request = self.capture_requests[1]
        binding = self.capture_requests[0]["binding"]
        rows = [json.loads(r) for r in Path(request["traces"][0]).read_text().splitlines()]
        rows[0]["payload"] = binding
        trace = self.traces / "synthetic-reused-solver.jsonl"
        self.write_trace(trace, rows)
        with self.assertRaisesRegex(ValueError, "session|Session|Solver|reflection|seal|dispatch"):
            self.evo.capture({**request, "binding": binding, "traces": [str(trace)],
                              "output": str(self.private / "reused-solver-context.json")})

    def test_frozen_runtime_must_match_every_native_role(self):
        roles = copy.deepcopy(self.roles)
        role = roles[-1]
        role["binding"]["model_id"] = "synthetic-different-model"
        self.write_trace(Path(role["traces"][0]), self.trace_rows(role["binding"], role["materials"], role["outputs"]))
        with self.assertRaisesRegex(ValueError, "runtime"):
            self.evo.seal_training({**self.seal_request, "roles": roles})

    def test_role_material_mapping_cannot_replace_validated_task_context(self):
        record = self.evo.seal_training(self.seal_request)
        role = record["roles"][-1]
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
        self.evo.seal_training(self.seal_request)
        request = copy.deepcopy(self.capture_requests[0])
        request["binding"]["source"] = "cli"
        request["binding"].pop("parent_trace")
        trace = Path(request["traces"][0])
        rows = [json.loads(line) for line in trace.read_text().splitlines()]
        rows[0]["payload"] = request["binding"]
        self.write_trace(trace, rows)
        with self.assertRaisesRegex(ValueError, "native|subagent|parent|Solver"):
            self.evo.capture({**request, "output": str(self.private / "synthetic-cli-solver-context.json")})
        with self.assertRaisesRegex(ValueError, "binding|trace|Session|Solver"):
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
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "State/runtime differs|parent runtime changed"):
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
        rows = [json.loads(line) for line in Path(role["traces"][0]).read_bytes().splitlines()]
        rows[0]["payload"] = role["binding"]
        self.write_trace(Path(role["traces"][0]), rows)
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
