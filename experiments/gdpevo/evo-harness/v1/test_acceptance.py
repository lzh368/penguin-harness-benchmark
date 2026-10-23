"""Synthetic full-provenance freeze tests; no real benchmark answers or models."""
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import Mock

import acceptance
import protocol
import test_protocol


class SnapshotTransportScopeTests(unittest.TestCase):
    def test_documented_snapshot_transport_and_native_inventory_have_identical_scope(self):
        evo = protocol.helper(os.environ["EVO_TOOL"])
        method = Path(os.environ["EVO_TOOL"]).resolve().parent.parent
        docs = (method / "references/state.md").read_text()
        command = re.search(r"(?m)^tar .*snapshots/v<N>\.tar\.gz\.tmp agent_state$", docs)[0]
        with tempfile.TemporaryDirectory(prefix="synthetic-snapshot-scope-") as temporary:
            root = Path(temporary)
            state = root / "agent_state"
            expected = {"system_config.yaml": b"version: 1\n", "AGENTS.md": b"SYNTHETIC instructions\n",
                        "skills/example/SKILL.md": b"SYNTHETIC retained method\n",
                        "skills/example/memory/conditions.md": b"SYNTHETIC retained applicability\n",
                        "skills/example/schedule": b"SYNTHETIC retained nested filename\n"}
            excluded = {".vault.toml": b"SYNTHETIC NO SECRET\n", "memory/user/MEMORY.md": b"runtime scaffold\n",
                        "schedule/entries.json": b"[]\n"}
            for name, body in {**expected, **excluded}.items():
                path = state / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
            (root / "snapshots").mkdir()
            subprocess.run(command.replace("<N>", "1"), shell=True, cwd=root, check=True)
            snapshot = acceptance.snapshot(root / "snapshots/v1.tar.gz.tmp")["files"]
            self.assertEqual(snapshot, expected)
            transport = root / "transport.tar.gz"
            subprocess.run(["tar", "--exclude=./.vault.toml", "--exclude=./memory", "--exclude=./schedule",
                            "-czf", str(transport), "-C", str(state), "."], check=True)
            with tarfile.open(transport) as archive:
                self.assertEqual({m.name.removeprefix("./"): archive.extractfile(m).read()
                                  for m in archive if m.isfile()}, expected)
            hashes = {name: acceptance.sha(body) for name, body in expected.items()}
            self.assertEqual(acceptance.transported_filemap(state, evo), hashes)
            packed = root / "packed.tar.gz"
            protocol.pack_state({"evo_tool": os.environ["EVO_TOOL"], "state": str(state), "output": str(packed)})
            with tarfile.open(packed) as archive:
                self.assertEqual({m.name: archive.extractfile(m).read() for m in archive if m.isfile()}, expected)
            helper = Path(__file__).resolve().parents[4] / "agents/penguin_agent/helper.mjs"
            native = json.loads(subprocess.check_output(["node", str(helper), "state-files", str(state)], text=True))
            self.assertEqual(native["files"], hashes)
            # No retrospective normalization of old vault-only snapshots.
            legacy = root / "old-unfiltered.tar.gz"
            with tarfile.open(legacy, "w:gz") as archive:
                archive.add(state, arcname="agent_state")
            before = legacy.read_bytes()
            with self.assertRaisesRegex(ValueError, "excluded transport material"):
                acceptance.snapshot(legacy)
            self.assertEqual(legacy.read_bytes(), before)


class FreezeTests(unittest.TestCase):
    def setUp(self):
        self.initialize_fixture()

    def initialize_fixture(self, all_success=False):
        fixture = test_protocol.ExportEvidenceTests("test_sealed_five_task_export_survives_relocation_using_original_traces")
        fixture.all_success = all_success
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.f = fixture
        self.evo = fixture.evo
        self.evo.seal_training(fixture.seal_request)
        self.h0 = fixture.root / "h0-state"
        self.final = fixture.root / "final-state"
        self.h0.mkdir()
        self.evo.export_skill({"library": str(fixture.library), "limits": fixture.limits,
            "max_bytes": 16000, "output": str(self.h0 / acceptance.SLOT)})
        (self.h0 / "system_config.yaml").write_text("version: 1\nsystem_prompt: |\n  SYNTHETIC frozen solver\nmemory:\n  enabled: false\n" + test_protocol.SYNTHETIC_OUTPUT_TOOLS)
        (self.h0 / "AGENTS.md").write_text("SYNTHETIC frozen agent guidance\n")
        shutil.copytree(self.h0, self.final)
        shutil.rmtree(self.final / acceptance.SLOT)
        test_protocol.install_package(self.evo, fixture.submission / "skill", self.final)
        final_version = 1 if all_success else 2
        (self.final / "system_config.yaml").write_bytes((self.h0 / "system_config.yaml").read_bytes().replace(
            b"version: 1\n", f"version: {final_version}\n".encode()))
        self.h0_archive = fixture.root / "h0.tar.gz"
        self.final_archive = fixture.root / "final.tar.gz"
        self.pack(self.h0, self.h0_archive)
        self.pack(self.final, self.final_archive)
        self.export_path = fixture.save(fixture.root / "export-request.json", {
            "evo_tool": fixture.tool, "submission": str(fixture.submission),
            "training_export": str(fixture.record_path), "train_ids": fixture.train,
            "private_learning": str(fixture.private), "trace_root": str(fixture.traces), "runtime": fixture.runtime})
        self.req = {"export_request": str(self.export_path), "h0_snapshot": str(self.h0_archive),
            "final_snapshot": str(self.final_archive), "h0_version": 1, "final_version": final_version,
            "target_state": str(self.final), "test_case_ids": [f"synthetic-test-{i}" for i in range(1, 6)],
            "runtime": fixture.runtime}

    @staticmethod
    def pack(state, archive):
        with tarfile.open(archive, "w:gz") as handle:
            handle.add(state, arcname="agent_state")

    def verify(self, **overrides):
        return acceptance.verify_freeze({**self.req, **overrides}, protocol.verify_export, self.evo)

    def test_native_default_command_output_cap_cannot_truncate_frozen_injection(self):
        for state, archive in ((self.h0, self.h0_archive), (self.final, self.final_archive)):
            path = state / "system_config.yaml"
            path.write_text(path.read_text().replace("maxOutputLength: 104192", "maxOutputLength: 16000"))
            self.pack(state, archive)
        with self.assertRaisesRegex(ValueError, "maxOutputLength must be at least"):
            self.verify()

    def empty_export(self):
        f = self.f
        f.save(f.decisions, [{"op": "SKIP", "candidate_ids": [c["id"] for c in f.candidates],
                              "reason": "SYNTHETIC no reusable procedure"}])
        f.roles[-1] = f.role("evolver", "synthetic-evolver", [f.role_text, f.candidate_path, f.library]
                            + [Path(p) for p in f.contexts], f.decisions)
        f.compiled.unlink()
        self.evo.compile_batch({"train_ids": f.train, "contexts": f.contexts, "candidates": str(f.candidate_path),
            "decisions": str(f.decisions), "library": str(f.library), "limits": f.limits, "output": str(f.compiled)})
        shutil.rmtree(f.submission / "skill")
        self.evo.export_skill({"library": str(f.compiled), "limits": f.limits,             "max_bytes": 16000, "output": str(f.submission / "skill")})
        f.record_path.unlink()
        self.evo.seal_training(f.seal_request)
        shutil.rmtree(self.final)
        shutil.copytree(self.h0, self.final)
        self.pack(self.final, self.final_archive)
        self.req["final_version"] = 1

    def test_complete_export_binds_snapshots_runtime_roster_and_active_target(self):
        result = self.verify()
        self.assertTrue(result["complete"])
        self.assertFalse(result["equivalent_h0"])
        self.assertEqual(result["h0"]["version"], 1)
        self.assertEqual(result["final"]["version"], 2)
        self.assertEqual(result["final"]["filemap"], self.evo.filemap(self.final))
        self.assertEqual(result["final"]["snapshot_sha256"], protocol.sha(self.final_archive))
        self.assertEqual(result["export_request_sha256"], protocol.sha(self.export_path))
        self.assertEqual(result["training_export_sha256"], protocol.sha(self.f.record_path))
        self.assertEqual(result["runtime"], self.f.runtime)
        self.assertEqual(result["test_case_ids"], self.req["test_case_ids"])
        self.assertEqual(result["model_calls"], 0)
        with self.assertRaisesRegex(ValueError, "live Target State"):
            self.verify(active_state="h0")
        checked = self.verify(active_state="h0", target_state=str(self.h0))
        self.assertEqual(checked["active_state"], "h0")
        self.assertEqual(checked["h0"]["filemap_sha256"], checked["target_state_filemap_sha256"])

    def test_verified_no_op_requires_complete_evidence_identical_content_and_version(self):
        self.empty_export()
        verifier = Mock(wraps=protocol.verify_export)
        first = acceptance.verify_freeze(self.req, verifier, self.evo)
        second = acceptance.verify_freeze(self.req, verifier, self.evo)
        self.assertEqual(verifier.call_count, 2)
        self.assertTrue(first["equivalent_h0"])
        self.assertEqual(first["h0"]["filemap_sha256"], second["final"]["filemap_sha256"])
        (self.final / "system_config.yaml").write_bytes((self.h0 / "system_config.yaml").read_bytes().replace(b"version: 1\n", b"version: 2\n"))
        self.pack(self.final, self.final_archive)
        with self.assertRaisesRegex(ValueError, "equivalent H0 requires identical"):
            self.verify(final_version=2)

    def test_five_successes_zero_candidates_and_real_evolver_allow_h0_reuse(self):
        self.initialize_fixture(all_success=True)
        self.assertEqual(self.f.candidates, [])
        self.assertEqual([role["role"] for role in self.f.roles], ["evolver"])
        result = self.verify()
        self.assertTrue(result["equivalent_h0"])
        self.assertEqual(result["h0"]["filemap"], result["final"]["filemap"])
        record = json.loads(self.f.record_path.read_bytes())
        record["roles"] = []
        self.f.save(self.f.record_path, record)
        with self.assertRaisesRegex(ValueError, "Evolver|evolver"):
            self.verify()

    def test_empty_output_never_turns_missing_or_incomplete_seal_into_h0_reuse(self):
        self.empty_export()
        saved = self.f.record_path.read_bytes()
        record = json.loads(saved)
        record["complete"] = False
        self.f.save(self.f.record_path, record)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.verify()
        self.f.record_path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.verify()
        self.f.record_path.write_bytes(saved)
        self.export_path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.verify()

    def test_recheck_rejects_tampered_evidence_after_successful_freeze(self):
        self.verify()
        self.f.save(self.f.submission / "answers/train_001.json", {"synthetic": "changed"})
        with self.assertRaisesRegex(ValueError, "answer changed"):
            self.verify()

    def test_final_slot_must_equal_verified_export_even_when_both_packages_are_valid(self):
        shutil.rmtree(self.final / acceptance.SLOT)
        test_protocol.install_package(self.evo, self.h0 / acceptance.SLOT, self.final)
        self.pack(self.final, self.final_archive)
        with self.assertRaisesRegex(ValueError, "slot differs from verified export"):
            self.verify()

    def test_h0_must_be_valid_empty_fixed_reader(self):
        shutil.rmtree(self.h0 / acceptance.SLOT)
        test_protocol.install_package(self.evo, self.f.submission / "skill", self.h0)
        self.pack(self.h0, self.h0_archive)
        with self.assertRaisesRegex(ValueError, "H0 must start"):
            self.verify()
        (self.h0 / acceptance.SLOT / "SKILL.md").write_text("SYNTHETIC changed reader")
        self.pack(self.h0, self.h0_archive)
        with self.assertRaisesRegex(ValueError, "reader|Skill.*format"):
            self.verify()

    def test_complete_skill_byte_budget_cannot_change_with_learned_entries(self):
        for budget in (8000, 32000):
            with self.subTest(budget=budget):
                if budget > 16000:
                    with self.assertRaisesRegex(ValueError, "invalid byte budget"):
                        self.evo.render_skill([], budget)
                    continue
                for state, archive in ((self.h0, self.h0_archive), (self.final, self.final_archive)):
                    (state / acceptance.SLOT / "SKILL.md").write_bytes(self.evo.render_skill([], budget))
                    config = state / "system_config.yaml"
                    config.write_bytes(config.read_bytes().replace(b"104192", b"200192"))
                    self.pack(state, archive)
                with self.assertRaisesRegex(ValueError, "exactly 16000"):
                    self.verify()

    def test_full_skill_snapshot_rejects_empty_extra_package_directory(self):
        (self.h0 / acceptance.SLOT / "topic").mkdir()
        self.pack(self.h0, self.h0_archive)
        with self.assertRaisesRegex(ValueError, "nested package directories"):
            self.verify()

    def test_native_method_siblings_are_exactly_manifest_owned(self):
        result = self.verify()
        methods = [name for name in result["final"]["filemap"] if name.startswith(acceptance.METHOD_PREFIX)]
        self.assertGreaterEqual(len(methods), 1)
        self.assertTrue(all(len(Path(name).parts) == 3 and Path(name).name == "SKILL.md" for name in methods))
        unknown = self.final / (acceptance.METHOD_PREFIX + "0" * 20)
        unknown.mkdir()
        self.pack(self.final, self.final_archive)
        with self.assertRaisesRegex(ValueError, "native method directory"):
            self.verify()
        (unknown / "SKILL.md").write_text("SYNTHETIC unmanifested skill")
        self.pack(self.final, self.final_archive)
        with self.assertRaisesRegex(ValueError, "exact manifest"):
            self.verify()

    def test_only_learned_slot_and_plain_version_may_change(self):
        for filename in ("AGENTS.md", "tools/synthetic.py", "memory/synthetic.txt"):
            with self.subTest(filename=filename):
                path = self.final / filename
                before = path.read_bytes() if path.exists() else None
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("SYNTHETIC forbidden State change\n")
                self.pack(self.final, self.final_archive)
                expected = "excluded transport material" if filename.startswith("memory/") else "outside learned slot"
                with self.assertRaisesRegex(ValueError, expected):
                    self.verify()
                if before is None:
                    path.unlink()
                    if filename.startswith("memory/"):
                        path.parent.rmdir()  # Do not mask the following config-change assertion with an excluded root.
                else:
                    path.write_bytes(before)
        config = self.final / "system_config.yaml"
        config.write_bytes(config.read_bytes().replace(b"frozen solver", b"different solver"))
        self.pack(self.final, self.final_archive)
        with self.assertRaisesRegex(ValueError, "outside learned slot"):
            self.verify()

    def test_versions_runtime_roster_and_live_state_are_bound(self):
        for changes, message in (({"final_version": 3}, "snapshot version"),
                                 ({"runtime": {**self.f.runtime, "thinking_level": "max"}}, "runtime"),
                                 ({"test_case_ids": self.req["test_case_ids"][:-1]}, "five distinct"),
                                 ({"test_case_ids": ["same"] * 5}, "five distinct"),
                                 ({"active_state": "other"}, "active State")):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                self.verify(**changes)
        config = self.final / "system_config.yaml"
        config.write_bytes(config.read_bytes().replace(b"version: 2\n", b"version: 1\n"))
        self.pack(self.final, self.final_archive)
        with self.assertRaisesRegex(ValueError, "higher final version"):
            self.verify(final_version=1)


class SnapshotSafetyTests(unittest.TestCase):
    def test_non_native_yaml_versions_and_duplicates_are_rejected(self):
        valid = b"version: 1\nsystem_prompt: |\n  SYNTHETIC\n"
        self.assertEqual(acceptance.config_version(valid), (1, b"system_prompt: |\n  SYNTHETIC\n"))
        for version in (b'version: "1"', b"version: 1 # comment", b"version: 01", b"version: true",
                        b"version: &v 1", b"'version': 1", b'"version": 1', b"version : 1",
                        b"version: 1\nversion: 2", b"? version\n: 1", b"{version: 1}",
                        b"---\nversion: 1", b"version: 1\n---\nversion: 2", b"version: 0"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                acceptance.config_version(version + b"\nsystem_prompt: SYNTHETIC\n")

    def test_archives_reject_escape_links_duplicates_and_special_entries(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="synthetic-freeze-tar-") as temp:
            archive = Path(temp) / "synthetic.tar.gz"
            archive.write_bytes(b"SYNTHETIC invalid gzip archive")
            with self.assertRaisesRegex(ValueError, "invalid native snapshot"):
                acceptance.snapshot(archive)
            for name, kind in (("../escape", tarfile.REGTYPE), ("/agent_state/absolute", tarfile.REGTYPE),
                    ("agent_state/../escape", tarfile.REGTYPE), ("agent_state/./alias", tarfile.REGTYPE),
                    ("agent_state//alias", tarfile.REGTYPE), ("agent_state/link", tarfile.SYMTYPE),
                    ("agent_state/hardlink", tarfile.LNKTYPE), ("agent_state/fifo", tarfile.FIFOTYPE),
                    ("agent_state/device", tarfile.CHRTYPE), ("agent_state", tarfile.DIRTYPE)):
                with self.subTest(name=name, kind=kind):
                    with tarfile.open(archive, "w:gz") as handle:
                        root = tarfile.TarInfo("agent_state")
                        root.type = tarfile.DIRTYPE
                        handle.addfile(root)
                        member = tarfile.TarInfo(name)
                        member.type = kind
                        if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                            member.linkname = "../outside"
                        handle.addfile(member, io.BytesIO(b""))
                    with self.assertRaises(ValueError):
                        acceptance.snapshot(archive)


if __name__ == "__main__":
    unittest.main()
