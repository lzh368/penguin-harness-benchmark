"""SYNTHETIC native registration using real public task metadata; no model calls."""
import copy
import io
import json
from pathlib import Path
import tarfile
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import protocol
import registration


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-evo-registration-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = Path(__file__).resolve().parents[4]
        self.project = self.root / "penguin/synthetic-project"
        self.output = self.project / "benchmarks"
        self.output.mkdir(parents=True)
        self.config_path = self.root / "config.json"
        self.bundle = self.root / "synthetic-bundle.tar.gz"
        self.bundle_meta = {"schema": "evo-harness-source-bundle-v1", "source_commit": "a" * 40,
                            "penguin_version": "0.2.16", "node_version": "24.20.0"}
        with tarfile.open(self.bundle, "w:gz") as archive:
            blob = json.dumps(self.bundle_meta).encode()
            info = tarfile.TarInfo("penguin-bench/bundle.json")
            info.size = len(blob)
            archive.addfile(info, io.BytesIO(blob))
        image = self.root / "synthetic-image-manifest.json"
        image.write_text('{"synthetic": true}\n')
        self.receipt_path = self.root / "synthetic-preflight.json"
        self.config = copy.deepcopy(protocol.read(Path(__file__).with_name("profile.json")))
        self.config.update({"group": "003", "test_ids": [f"tg003-test-{n:03d}" for n in range(1, 6)],
            "runtime": {"provider": "fixture", "model_id": "no-model", "thinking_level": "xhigh",
                        "api_host": "synthetic.invalid", "effective_reasoning_effort": "high", "client_type": "synthetic"},
            "api_host": "synthetic.invalid", "benchmark_id": "synthetic-train", "test_benchmark_id": "synthetic-test",
            "bundle_sha256": registration.sha(self.bundle), "image_manifest_sha256": registration.sha(image)})
        self.config["versions"].update(penguin_commit="a" * 40, benchmark_commit="b" * 40, skill="2026.10.22.3")
        self.config["deployment"] = {"benchmark_checkout": str(self.repo), "penguin_home": str(self.project.parent),
            "project_id": self.project.name, "bundle": str(self.bundle), "bundle_sha256": self.config["bundle_sha256"],
            "image_manifest": str(image), "image_manifest_sha256": self.config["image_manifest_sha256"],
            "preflight_receipt": str(self.receipt_path), "bin_dir": str(self.root / "bin"), "uvx": "/synthetic/bin/uvx"}
        self.config["adapter"] = {"python_path": str(self.repo / "agents"), "agent": "penguin_agent:PenguinAgent",
            "penguin_version": "0.2.16", "node_version": "24.20.0", "run_timeout": "task", "max_turns": -1,
            "allowed_agent_hosts": ["synthetic.invalid"], "host_penguin_home": str(self.project.parent),
            "host_project_id": self.project.name, "install_bundle": str(self.bundle)}
        self.preflight = {"passed": True, "model_calls": 0, "test_concurrency": 5, "five_test_phases_overlap": True,
            "pins": {key: self.config["versions"][key] for key in ("penguin_commit", "benchmark_commit")},
            "task_ids": ["tg003-train", *self.config["test_ids"]]}
        self.preflight["pins"].update({key: self.config[key] for key in ("bundle_sha256", "image_manifest_sha256")})
        self.preflight["pins"].update({key: self.config["runtime"][key] for key in ("provider", "model_id", "api_host")})
        self.save_preflight()
        self.req = {"op": "prepare-benchmarks", "config": str(self.config_path), "repo": str(self.repo),
                    "output": str(self.output)}
        self.save_config()

    def save_preflight(self):
        self.receipt_path.write_text(json.dumps(self.preflight))
        self.config["deployment"]["preflight_receipt_sha256"] = registration.sha(self.receipt_path)

    def save_config(self):
        self.config_path.write_text(json.dumps(self.config))
        self.req["config_sha256"] = registration.sha(self.config_path)

    def run_prepare(self):
        return protocol.execute(self.req)

    def replace_bundle(self, blob):
        with tarfile.open(self.bundle, "w:gz") as archive:
            info = tarfile.TarInfo("penguin-bench/bundle.json")
            info.size = len(blob)
            archive.addfile(info, io.BytesIO(blob))
        digest = registration.sha(self.bundle)
        self.config["bundle_sha256"] = digest
        self.config["deployment"]["bundle_sha256"] = digest
        self.preflight["pins"]["bundle_sha256"] = digest
        self.save_preflight()
        self.save_config()

    def test_realistic_dependency_inventory_above_old_limit_registers_with_pins(self):
        manifest = copy.deepcopy(self.bundle_meta)
        manifest["payload"] = {"files": {
            f"npm/lib/node_modules/@prismshadow/penguin-cli/node_modules/fixture-package-{i:05d}/dist/module.js":
            {"mode": 420, "sha256": f"{i:064x}"} for i in range(23944)}, "symlinks": {}}
        blob = json.dumps(manifest, sort_keys=True, indent=2).encode()
        # gpu01's fully verified b984471b source bundle contains a 5,315,870
        # byte regular manifest. Match its actual scale with dependency records
        # and a few legal trailing whitespace bytes, without copying its payload.
        observed_manifest_bytes = 5315870
        self.assertLessEqual(len(blob), observed_manifest_bytes)
        blob += b" " * (observed_manifest_bytes - len(blob))
        self.assertEqual(len(blob), observed_manifest_bytes)
        self.assertGreater(len(blob), 4 * 1024 * 1024)
        self.assertLess(len(blob), registration.MAX_BUNDLE_MANIFEST_BYTES)
        self.replace_bundle(blob)
        result = self.run_prepare()
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(protocol.execute({**self.req, "op": "verify-benchmarks"}), result)
        # A larger accepted inventory never relaxes source or runtime identity.
        self.config["adapter"]["node_version"] = "24.18.0"
        self.save_config()
        with self.assertRaisesRegex(ValueError, "runtime pins differ"):
            self.run_prepare()
        self.config["adapter"]["node_version"] = "24.20.0"
        self.config["versions"]["penguin_commit"] = "c" * 40
        self.save_config()
        with self.assertRaisesRegex(ValueError, "runtime pins differ"):
            self.run_prepare()
        self.config["versions"]["penguin_commit"] = "a" * 40
        self.save_config()
        with self.bundle.open("ab") as stream:
            stream.write(b"changed frozen archive")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            self.run_prepare()

    def test_manifest_actual_64_mib_boundary_and_oversize_rejection(self):
        limit = registration.MAX_BUNDLE_MANIFEST_BYTES
        self.assertEqual(limit, 64 * 1024 * 1024)
        raw = json.dumps(self.bundle_meta).encode()
        # Legal trailing JSON whitespace exercises the actual bound without
        # constructing a huge decoded inventory or weakening the production cap.
        self.replace_bundle(raw + b" " * (limit - len(raw)))
        self.assertEqual(registration._bundle_manifest(self.bundle), self.bundle_meta)
        self.replace_bundle(raw + b" " * (limit + 1 - len(raw)))
        with patch.object(tarfile.TarFile, "extractfile", side_effect=AssertionError("must reject before read")):
            with self.assertRaisesRegex(ValueError, f"exceeds {limit} bytes"):
                registration._bundle_manifest(self.bundle)

    def test_manifest_link_or_non_object_rejected(self):
        with tarfile.open(self.bundle, "w:gz") as archive:
            info = tarfile.TarInfo("penguin-bench/bundle.json")
            info.type = tarfile.SYMTYPE
            info.linkname = "different.json"
            archive.addfile(info)
        with self.assertRaisesRegex(ValueError, "invalid source bundle manifest"):
            registration._bundle_manifest(self.bundle)
        self.replace_bundle(b"[]")
        with self.assertRaisesRegex(ValueError, "manifest object"):
            registration._bundle_manifest(self.bundle)

    def test_native_roster_complete_launch_and_full_runtime(self):
        result = self.run_prepare()
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["runtime"], self.config["runtime"])
        self.assertEqual(result["training"]["case_ids"], ["CASE-001-tg003-train"])
        self.assertEqual(result["test"]["case_ids"], [f"CASE-{i:03d}-tg003-test-{i:03d}" for i in range(1, 6)])
        for phase in ("training", "test"):
            root = self.output / result[phase]["benchmark_id"]
            manifest = tomllib.loads((root / "benchmark_config.toml").read_text())
            self.assertEqual(manifest["id"], root.name)
            self.assertEqual(manifest["status"], "published")
            for case in result[phase]["case_ids"]:
                self.assertRegex(case, r"^CASE-[A-Za-z0-9_-]+$")
                launch = protocol.read(root / case / "statement/harbor-launch.json")
                argv = launch["argv_template"]
                options = [argv[n + 1] for n, item in enumerate(argv) if item == "--ak"]
                self.assertIn("node_version=24.20.0", options)
                self.assertIn("agent_state_tar=<absolute-state-archive>", options)
                self.assertEqual(len(options), len({value.split("=", 1)[0] for value in options}))
                self.assertEqual(launch["runtime"], self.config["runtime"])
                self.assertEqual(Path(argv[argv.index("-p") + 1]).name, result[phase]["case_to_task"][case])
                self.assertIn("--allow-agent-host", argv)
                self.assertNotIn("--extra-docker-compose", argv)

    def test_reprepare_preserves_scoreboard_but_refuses_changed_statement(self):
        result = self.run_prepare()
        root = self.output / result["test"]["benchmark_id"]
        board = root / "scoreboard.yaml"
        board.write_text("evaluations: []\n# preserved fixture\n")
        self.assertEqual(self.run_prepare(), result)
        self.assertIn("preserved", board.read_text())
        (root / result["test"]["case_ids"][0] / "statement/README.md").write_text("SYNTHETIC changed")
        with self.assertRaisesRegex(ValueError, "material differs"):
            self.run_prepare()

    def test_verify_never_creates_missing_benchmark(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            protocol.execute({**self.req, "op": "verify-benchmarks"})
        self.assertEqual(list(self.output.iterdir()), [])
        prepared = self.run_prepare()
        self.assertEqual(protocol.execute({**self.req, "op": "verify-benchmarks"}), prepared)

    def test_omitted_or_wrong_node_pin_refused_before_registration(self):
        self.config["adapter"].pop("node_version")
        self.save_config()
        with self.assertRaises(KeyError):
            self.run_prepare()
        self.config["adapter"]["node_version"] = "24.18.0"
        self.save_config()
        with self.assertRaisesRegex(ValueError, "runtime pins differ"):
            self.run_prepare()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_config_or_preflight_roster_pins_cannot_be_substituted(self):
        original = self.req["config_sha256"]
        self.req["config_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "config hash"):
            self.run_prepare()
        self.req["config_sha256"] = original
        self.preflight["task_ids"][0] = "tg005-train"
        self.save_preflight()
        self.save_config()
        with self.assertRaisesRegex(ValueError, "preflight roster"):
            self.run_prepare()
        self.preflight["task_ids"][0] = "tg003-train"
        self.preflight["pins"]["benchmark_commit"] = "c" * 40
        self.save_preflight()
        self.save_config()
        with self.assertRaisesRegex(ValueError, "preflight pins"):
            self.run_prepare()

    def test_original_bad_case_names_are_not_silently_overwritten(self):
        old = self.output / self.config["test_benchmark_id"]
        (old / "tg003-test-001").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "Case roster"):
            self.run_prepare()
        self.assertFalse((self.output / self.config["benchmark_id"]).exists())
        self.assertTrue((old / "tg003-test-001").exists())

    def test_scope_symlinks_and_added_case_material_fail_closed(self):
        result = self.run_prepare()
        with self.assertRaisesRegex(ValueError, "Project benchmarks"):
            protocol.execute({**self.req, "output": str(self.root)})
        case = self.output / result["training"]["benchmark_id"] / result["training"]["case_ids"][0]
        (case / "statement/extra.txt").write_text("SYNTHETIC")
        with self.assertRaisesRegex(ValueError, "extra native Case material"):
            self.run_prepare()
        (case / "statement/extra.txt").unlink()
        statement = case / "statement/README.md"
        copy = self.root / "copy.md"
        copy.write_text(statement.read_text())
        statement.unlink()
        statement.symlink_to(copy)
        with self.assertRaisesRegex(ValueError, "linked Benchmark"):
            self.run_prepare()


if __name__ == "__main__":
    unittest.main()
