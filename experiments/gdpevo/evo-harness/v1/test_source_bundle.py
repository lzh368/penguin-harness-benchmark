"""Synthetic provenance guard tests; no model calls or real experiment claims."""
import json
from pathlib import Path
import tempfile
import unittest
import source_bundle as bundle


class SourceBundleTests(unittest.TestCase):
    def test_published_plugin_includes_icon_and_excludes_tests(self):
        prefix = "plugins/evo-harness-rsi-toolkit/"
        expected = {"package.json", "LICENSE", "icon.svg", "skills/evo-harness-rsi-toolkit/SKILL.md",
                    "skills/evo-harness-rsi-toolkit/scripts/evo.py"}
        tracked = [prefix + p for p in expected | {"tests/test_evo.py"}]
        self.assertEqual(bundle.published_plugin_files(tracked), expected)

    def test_requires_clean_build_from_exact_commit(self):
        commit = "a" * 40
        good = {"channel": "source", "commit": commit, "dirty": False}
        bundle.checked_build(good, commit)
        for changed in ({"commit": "b" * 40}, {"dirty": True}, {"dirty": None}, {"channel": "release"}):
            with self.assertRaises(ValueError):
                bundle.checked_build({**good, **changed}, commit)

    def test_payload_is_relocatable_and_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-bundle-") as tmp:
            root = Path(tmp)
            commit = "a" * 40
            build = {"channel": "source", "commit": commit, "dirty": False}
            (root / "cli-build.json").write_text(json.dumps(build))
            (root / "native-plugin-probe.json").write_text(json.dumps({"build": build}))
            (root / "runtime.js").write_text("// SYNTHETIC\n")
            (root / "internal-link").symlink_to("runtime.js")
            manifest = {"schema": "evo-harness-source-bundle-v1", "source_commit": commit,
                        "payload": bundle.inventory(root)}
            (root / "bundle.json").write_text(json.dumps(manifest))
            self.assertTrue(bundle.verify(root, commit)["verified"])
            (root / "runtime.js").write_text("// SYNTHETIC modified\n")
            with self.assertRaisesRegex(ValueError, "payload changed"):
                bundle.verify(root, commit)

    def test_symlinks_cannot_depend_on_source_checkout_or_missing_file(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-bundle-") as tmp:
            root = Path(tmp) / "bundle"
            root.mkdir()
            outside = Path(tmp) / "source.js"
            outside.write_text("// SYNTHETIC\n")
            link = root / "dependency"
            for target in (outside, root / "missing"):
                link.symlink_to(target)
                with self.assertRaisesRegex(ValueError, "external or broken"):
                    bundle.inventory(root)
                link.unlink()


if __name__ == "__main__":
    unittest.main()
