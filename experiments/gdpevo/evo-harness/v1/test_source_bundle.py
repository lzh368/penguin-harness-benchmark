"""Synthetic provenance guard tests; no model calls or real experiment claims."""
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import source_bundle as bundle


class SourceBundleTests(unittest.TestCase):
    def archive_using_builder_command(self, stage, archive):
        commands = [line for line in Path(bundle.__file__).with_name("make_source_bundle.sh").read_text().splitlines()
                    if line.startswith("tar --mode=")]
        self.assertEqual(len(commands), 1)
        subprocess.run(["bash", "-c", commands[0]], check=True,
                       env={**os.environ, "STAGE": str(stage), "PARTIAL": str(archive)})

    def test_published_plugin_includes_icon_and_excludes_tests(self):
        prefix = "plugins/evo-harness-rsi-toolkit/"
        expected = {"package.json", "LICENSE", "icon.svg", "skills/evo-harness-rsi-toolkit/SKILL.md",
                    "skills/evo-harness-rsi-toolkit/scripts/evo.py"}
        tracked = [prefix + p for p in expected | {"tests/test_evo.py"}]
        package = {"files": ["icon.svg", "skills", "LICENSE"]}
        self.assertEqual(bundle.published_plugin_files(tracked, package), expected)

    def test_hooks_follow_explicit_files_declaration_without_other_paths(self):
        prefix = "plugins/evo-harness-rsi-toolkit/"
        tracked = [prefix + name for name in ("package.json", "LICENSE", "hooks/guard.mjs",
                   "hooks/lib.mjs", "tests/test_evo.py", "private/secret", "hooks-not-published.mjs")]
        self.assertEqual(bundle.published_plugin_files(tracked, {"files": ["hooks"]}),
                         {"package.json", "LICENSE", "hooks/guard.mjs", "hooks/lib.mjs"})
        self.assertEqual(bundle.published_plugin_files(tracked, {"files": ["LICENSE"]}),
                         {"package.json", "LICENSE"})
        for entries in (None, "hooks", ["*"], ["../hooks"], ["/hooks"], ["hooks/../private"],
                        ["private"], ["hooks/**"], ["hooks", "hooks"], [None]):
            with self.subTest(files=entries), self.assertRaisesRegex(ValueError, "unsupported.*files"):
                bundle.published_plugin_files(tracked, {"files": entries})

    def make_record_fixture(self, temp):
        source, root = Path(temp) / "source", Path(temp) / "bundle"
        app = root / bundle.APP
        commit, version, node = "a" * 40, "0.0.0-synthetic", "v22.0.0"
        for name in ("cli", "core", "server"):
            source_dist = source / "packages" / name / "dist"
            source_dist.mkdir(parents=True)
            filename = "penguin.js" if name == "cli" else "index.js"
            (source_dist / filename).write_text("// SYNTHETIC " + name + "\n")
            target = app if name == "cli" else app / "node_modules/@prismshadow" / ("penguin-" + name)
            (target / "dist").mkdir(parents=True)
            (target / "dist" / filename).write_bytes((source_dist / filename).read_bytes())
        (app / "package.json").write_text(json.dumps({"version": version}))
        (source / "pnpm-lock.yaml").write_text("# SYNTHETIC\n")
        package = {"name": bundle.PLUGIN, "files": ["skills", "hooks", "LICENSE", "icon.svg"]}
        source_files = {"package.json": json.dumps(package), "LICENSE": "SYNTHETIC",
                        "icon.svg": "<svg/>", "skills/evo/SKILL.md": "SYNTHETIC",
                        "hooks/guard.mjs": "// SYNTHETIC guard\n", "hooks/lib.mjs": "// SYNTHETIC dependency\n"}
        plugin = app / "node_modules" / bundle.PLUGIN
        prefix = "plugins/evo-harness-rsi-toolkit/"
        for name, content in source_files.items():
            for path in (source / prefix / name, plugin / name):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
        build = {"channel": "source", "commit": commit, "dirty": False, "version": version,
                 "runtime": {"node": node, "platform": "linux"}}
        (root / "cli-build.json").write_text(json.dumps(build))
        (root / "native-plugin-probe.json").write_text(json.dumps({"build": build, "plugin": {"synthetic": True}}))
        def git_fixture(_source, *args):
            if args[0] == "ls-tree":
                return "\n".join(prefix + name for name in source_files)
            if args == ("rev-parse", commit + "^{tree}"):
                return "b" * 40
            raise AssertionError("unexpected synthetic git command: " + repr(args))
        return source, root, plugin, commit, node, git_fixture

    def test_record_rejects_missing_or_modified_hook_source(self):
        for mutation in ("missing", "modified"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(prefix="synthetic-bundle-") as temp:
                source, root, plugin, commit, node, git_fixture = self.make_record_fixture(temp)
                hook = plugin / "hooks/guard.mjs"
                if mutation == "missing":
                    hook.unlink()
                else:
                    hook.write_text("// SYNTHETIC different hook\n")
                expected = "missing/unexpected" if mutation == "missing" else "content differs"
                with patch.object(bundle, "check_source"), patch.object(bundle, "git", side_effect=git_fixture), \
                        self.assertRaisesRegex(ValueError, expected):
                    bundle.record(source, commit, root, node)
                self.assertFalse((root / "bundle.json").exists())

    def test_record_seals_all_hooks_and_verify_rejects_deleted_or_changed_hook(self):
        for mutation in ("missing", "modified"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(prefix="synthetic-bundle-") as temp:
                source, root, plugin, commit, node, git_fixture = self.make_record_fixture(temp)
                with patch.object(bundle, "check_source"), patch.object(bundle, "git", side_effect=git_fixture):
                    bundle.record(source, commit, root, node)
                self.assertTrue(bundle.verify(root, commit)["verified"])
                sealed = json.loads((root / "bundle.json").read_text())["plugin_files"]["files"]
                for filename in ("hooks/guard.mjs", "hooks/lib.mjs"):
                    self.assertEqual(sealed[filename]["sha256"], bundle.sha(plugin / filename))
                hook = plugin / "hooks/lib.mjs"
                if mutation == "missing":
                    hook.unlink()
                else:
                    hook.write_text("// SYNTHETIC changed after sealing\n")
                with self.assertRaisesRegex(ValueError, "payload changed"):
                    bundle.verify(root, commit)

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

    def test_restrictive_umask_tar_has_exact_public_modes_without_chmod_hardlinks(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-public-bundle-") as temp:
            temp = Path(temp)
            old_umask = os.umask(0o077)
            try:
                source, root, plugin, commit, node, git_fixture = self.make_record_fixture(temp)
                npmrc = root / bundle.OFFICIAL_EMPTY_NPMRC
                npmrc.parent.mkdir(parents=True)
                npmrc.write_bytes(b"")
                # Match the builder's real archive root while retaining source
                # and the synthetic shared store outside it.
                stage = temp / "stage"
                stage.mkdir()
                root.rename(stage / "penguin-bench")
                root = stage / "penguin-bench"
                deployed = root / bundle.APP / "dist/penguin.js"
                original = source / "packages/cli/dist/penguin.js"
                deployed.unlink()
                os.link(original, deployed)
                store = temp / "shared-store-executable"
                store.write_text("#!/bin/sh\nprintf 'public-mode-probe\\n'\n")
                store.chmod(0o700)
                (root / "bin").mkdir()
                os.link(store, root / "bin/probe")
                (root / "bin/relative-link").symlink_to("probe")
                (root / "bin").chmod(0o2770)
                before = {str(p): (p.stat().st_ino, p.stat().st_mode, bundle.sha(p)) for p in (original, store)}
                with patch.object(bundle, "check_source"), patch.object(bundle, "git", side_effect=git_fixture):
                    bundle.record(source, commit, root, node)
                self.assertTrue(bundle.verify_staged(root, commit)["verified"])
                self.assertEqual(bundle.read(root / "bundle.json")["payload"]["files"][bundle.OFFICIAL_EMPTY_NPMRC],
                                 {"sha256": bundle.EMPTY_SHA256, "mode": 0o644})
                with self.assertRaisesRegex(ValueError, "permissions differ|payload changed"):
                    bundle.verify(root, commit)
                archive = temp / "public.tar.gz"
                self.archive_using_builder_command(stage, archive)
                rewinds = []
                original_seek = gzip.GzipFile.seek
                def observe_seek(handle, offset, whence=0):
                    if whence == 0 and offset < handle.tell():rewinds.append(offset)
                    return original_seek(handle, offset, whence)
                with patch.object(gzip.GzipFile, "seek", observe_seek):
                    self.assertTrue(bundle.verify_archive(archive, commit)["verified_archive"])
                self.assertEqual(rewinds, [], "archive verification must not repeatedly decompress its payload")
                self.assertEqual(before, {str(p): (p.stat().st_ino, p.stat().st_mode, bundle.sha(p)) for p in (original, store)})
                self.assertEqual(deployed.stat().st_ino, original.stat().st_ino)
                with tarfile.open(archive) as packed:
                    for member in packed.getmembers():
                        if member.isdir():self.assertEqual(member.mode, 0o755)
                        elif member.isfile() or member.islnk():self.assertIn(member.mode, (0o644, 0o755))
                    self.assertEqual(packed.getmember("penguin-bench/bin/probe").mode, 0o755)
                    self.assertEqual(packed.getmember("penguin-bench/bundle.json").mode, 0o644)
                    empty_config = packed.getmember("penguin-bench/" + bundle.OFFICIAL_EMPTY_NPMRC)
                    self.assertTrue(empty_config.isfile())
                    self.assertEqual((empty_config.size, empty_config.mode), (0, 0o644))
                extracted = temp / "extracted"
                extracted.mkdir()
                subprocess.run(["tar", "-xzf", str(archive), "--same-permissions", "--no-same-owner", "-C", str(extracted)], check=True)
                published = extracted / "penguin-bench"
                self.assertTrue(bundle.verify(published, commit)["verified"])
                self.assertEqual((published / bundle.OFFICIAL_EMPTY_NPMRC).read_bytes(), b"")
                # Real non-root process reads every public file and executes
                # the public binary. In root CI only this synthetic tree is
                # traversable to nobody; normal runs already use a non-root UID.
                temp.chmod(0o755);extracted.chmod(0o755)
                identity = {"user": 65534, "group": 65534, "extra_groups": []} if os.geteuid() == 0 else {}
                probe = "import os,pathlib,subprocess; assert os.geteuid()!=0; r=pathlib.Path.cwd(); [p.read_bytes() for p in r.rglob('*') if p.is_file()]; assert subprocess.check_output([str(r/'bin/probe')])==b'public-mode-probe\\n'; print('non-root-public-access-ok')"
                result = subprocess.run([sys.executable, "-c", probe], cwd=published, check=True,
                                        capture_output=True, text=True, **identity)
                self.assertEqual(result.stdout.strip(), "non-root-public-access-ok")
                published.chmod(0o700)
                with self.assertRaisesRegex(ValueError, "payload changed"):
                    bundle.verify(published, commit)
            finally:
                os.umask(old_umask)

    def test_official_empty_npmrc_exception_is_exact_regular_and_empty(self):
        cases = ("nonempty", "newline", "symlink", "directory", "fifo", "other-npm", "other-root", "case")
        for mutation in cases:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(prefix="synthetic-npmrc-") as temp:
                source, root, plugin, commit, node, git_fixture = self.make_record_fixture(temp)
                name = {"other-npm": "node/lib/node_modules/other/.npmrc",
                        "other-root": "npm/lib/node_modules/npm/.npmrc",
                        "case": "node/lib/node_modules/npm/.NPMRC"}.get(mutation, bundle.OFFICIAL_EMPTY_NPMRC)
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                if mutation == "symlink":
                    (path.parent / "empty-public-file").write_bytes(b"")
                    path.symlink_to("empty-public-file")
                elif mutation == "directory":
                    path.mkdir()
                elif mutation == "fifo":
                    os.mkfifo(path)
                else:
                    path.write_bytes(b"SYNTHETIC token" if mutation == "nonempty" else b"\n" if mutation == "newline" else b"")
                with patch.object(bundle, "check_source"), patch.object(bundle, "git", side_effect=git_fixture), \
                        self.assertRaisesRegex(ValueError, "private file"):
                    bundle.record(source, commit, root, node)
                self.assertFalse((root / "bundle.json").exists())

    def test_public_inventory_refuses_private_files_and_unrelocatable_links(self):
        for filename in (".env", ".env.production", ".vault.toml", ".project_config.toml", "api-token", ".npmrc"):
            with self.subTest(private=filename), tempfile.TemporaryDirectory(prefix="synthetic-private-bundle-") as temp:
                source, root, plugin, commit, node, git_fixture = self.make_record_fixture(temp)
                (root / bundle.APP / filename).write_text("SYNTHETIC private, never archive")
                with patch.object(bundle, "check_source"), patch.object(bundle, "git", side_effect=git_fixture), \
                        self.assertRaisesRegex(ValueError, "private file"):
                    bundle.record(source, commit, root, node)
                self.assertFalse((root / "bundle.json").exists())
        with tempfile.TemporaryDirectory(prefix="synthetic-bundle-links-") as temp:
            root = Path(temp) / "bundle"
            (root / "bin").mkdir(parents=True)
            outside = Path(temp) / "private"
            outside.write_text("SYNTHETIC external")
            outside.chmod(0o600)
            original = (outside.stat().st_mode, bundle.sha(outside))
            target = root / "bin/target"
            target.write_text("SYNTHETIC public")
            for link_target, reason in ((outside, "external or broken"), (target, "absolute symlink")):
                link = root / "bin/link"
                link.symlink_to(link_target)
                with self.assertRaisesRegex(ValueError, reason):
                    bundle.inventory(root, normalize_modes=True, include_directories=True, public=True)
                self.assertEqual(original, (outside.stat().st_mode, bundle.sha(outside)))
                link.unlink()

    def test_archive_with_unfixed_permissions_is_not_publishable(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-unfixed-bundle-") as temp:
            source, root, plugin, commit, node, git_fixture = self.make_record_fixture(temp)
            stage = Path(temp)/"stage"
            stage.mkdir();root.rename(stage/"penguin-bench");root=stage/"penguin-bench"
            root.chmod(0o700)
            with patch.object(bundle, "check_source"), patch.object(bundle, "git", side_effect=git_fixture):
                bundle.record(source, commit, root, node)
            archive = Path(temp)/"bad.tar.gz"
            subprocess.run(["tar", "-czf", str(archive), "-C", str(stage), "penguin-bench"], check=True)
            with self.assertRaisesRegex(ValueError, "directory permissions differ"):
                bundle.verify_archive(archive, commit)


if __name__ == "__main__":
    unittest.main()
