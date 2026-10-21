#!/usr/bin/env python3
"""Model-free content/provenance checks for a source-built Harbor install bundle."""
import sys
sys.dont_write_bytecode = True
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

APP = "npm/lib/node_modules/@prismshadow/penguin-cli"
PLUGIN = "@penguinharness/evo-harness-rsi-toolkit"


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def git(source, *args):
    return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()


def check_source(source, commit):
    require(re.fullmatch(r"[0-9a-f]{40}", commit), "supply a full lowercase source commit")
    require(git(source, "rev-parse", "HEAD") == commit, "source HEAD does not match commit")
    require(not git(source, "status", "--porcelain", "--untracked-files=all"), "source checkout is dirty")
    for name in ("cli", "core", "server"):
        require((Path(source) / "packages" / name / "dist").is_dir(), f"build {name} first")
    require((Path(source) / "plugins/evo-harness-rsi-toolkit/package.json").is_file(), "source lacks Evo-Harness")


def inventory(root, exclude=()):
    root = Path(root).resolve()
    files, links = {}, {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if name in exclude:
            continue
        if path.is_symlink():
            require(path.resolve().is_relative_to(root) and path.exists(), f"external or broken symlink: {name}")
            links[name] = os.readlink(path)
        elif path.is_file():
            files[name] = {"sha256": sha(path), "mode": path.stat().st_mode & 0o777}
        else:
            require(path.is_dir(), f"special file: {name}")
    return {"files": files, "symlinks": links}


def checked_build(build, commit):
    require(build.get("channel") == "source" and build.get("commit") == commit
            and build.get("dirty") is False, "stale, dirty or non-source compiled artifact; rebuild the clean commit")


def published_plugin_files(tracked):
    prefix = "plugins/evo-harness-rsi-toolkit/"
    names = {p.removeprefix(prefix) for p in tracked if p.startswith(prefix)}
    return {p for p in names if p in ("package.json", "LICENSE", "icon.svg") or p.startswith("skills/")}


def record(source, commit, root, node_version):
    source, root = Path(source).resolve(), Path(root).resolve()
    check_source(source, commit)
    app = root / APP
    cli_build = read(root / "cli-build.json")
    probe = read(root / "native-plugin-probe.json")
    checked_build(cli_build, commit)
    checked_build(probe["build"], commit)
    require(cli_build["runtime"]["node"] == node_version, "unexpected independent Node runtime")
    require(cli_build["runtime"]["platform"] == "linux", "Linux runtime required")
    version = read(app / "package.json")["version"]
    require(version == cli_build["version"] == probe["build"]["version"], "runtime version mismatch")
    # Dist must be the actual output of this checkout, including server/core chunks.
    for name in ("cli", "core", "server"):
        deployed = app if name == "cli" else app / "node_modules/@prismshadow" / f"penguin-{name}"
        require(inventory(deployed / "dist") == inventory(source / "packages" / name / "dist"),
                f"deployed {name} differs from source build")
    plugin = app / "node_modules" / PLUGIN
    require(plugin.resolve().is_relative_to(root), "plugin resolves outside bundle")
    plugin_files = inventory(plugin)
    source_plugin = source / "plugins/evo-harness-rsi-toolkit"
    tracked = set(git(source, "ls-tree", "-r", "--name-only", commit, "--",
                      "plugins/evo-harness-rsi-toolkit").splitlines())
    expected = published_plugin_files(tracked)
    require(set(plugin_files["files"]) == expected and not plugin_files["symlinks"],
            "deployed plugin has missing/unexpected published files")
    require(all(sha(source_plugin / p) == item["sha256"] for p, item in plugin_files["files"].items()),
            "deployed plugin content differs from source commit")
    manifest = {"schema": "evo-harness-source-bundle-v1", "source_commit": commit,
                "source_tree": git(source, "rev-parse", f"{commit}^{{tree}}"),
                "source_lockfile_sha256": sha(source / "pnpm-lock.yaml"),
                "penguin_version": version, "node_version": node_version, "arch": os.uname().machine,
                "cli_sha256": sha(app / "dist/penguin.js"),
                "plugin_sha256": hashlib.sha256(canonical(plugin_files)).hexdigest(),
                "plugin_files": plugin_files, "plugin_load": probe["plugin"],
                "payload": inventory(root, exclude=("bundle.json",))}
    (root / "bundle.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {k: manifest[k] for k in ("source_commit", "penguin_version", "node_version", "cli_sha256", "plugin_sha256")}


def verify(root, commit):
    root = Path(root).resolve()
    manifest = read(root / "bundle.json")
    require(manifest["schema"] == "evo-harness-source-bundle-v1" and manifest["source_commit"] == commit,
            "bundle source identity mismatch")
    require(manifest["payload"] == inventory(root, exclude=("bundle.json",)), "bundle payload changed")
    checked_build(read(root / "cli-build.json"), commit)
    checked_build(read(root / "native-plugin-probe.json")["build"], commit)
    return {"verified": True, "source_commit": commit, "model_calls": 0}


if __name__ == "__main__":
    try:
        command, *args = sys.argv[1:]
        result = {"check-source": check_source, "record": record, "verify": verify}[command](*args)
        print(json.dumps(result or {"clean_source": True}))
    except (ValueError, KeyError, OSError, TypeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(2)
