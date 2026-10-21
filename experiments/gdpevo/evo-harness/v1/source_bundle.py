#!/usr/bin/env python3
"""Model-free content/provenance checks for a source-built Harbor install bundle."""
import sys
sys.dont_write_bytecode = True
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile

APP = "npm/lib/node_modules/@prismshadow/penguin-cli"
PLUGIN = "@penguinharness/evo-harness-rsi-toolkit"
MODE_POLICY = "public-0755-0644-v1"
PUBLIC_TOP_LEVEL = {"bin", "npm", "node", "cli-build.json", "native-plugin-probe.json", "bundle.json"}
PRIVATE_NAMES = {".git", ".vault.toml", ".project_config.toml", ".npmrc", ".netrc", ".pypirc",
                 "api-token", "server.lock", ".env"}
OFFICIAL_EMPTY_NPMRC = "node/lib/node_modules/npm/.npmrc"
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stream_sha(handle):
    fingerprint = hashlib.sha256()
    for block in iter(lambda: handle.read(1024 * 1024), b""):
        fingerprint.update(block)
    return fingerprint.hexdigest()


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


def public_mode(mode, directory=False):
    return 0o755 if directory or mode & 0o111 else 0o644


def inventory(root, exclude=(), normalize_modes=False, include_directories=False, public=False):
    if public:
        require(not Path(root).is_symlink(), "public bundle root cannot be a symlink")
    root = Path(root).resolve()
    files, links, directories = {}, {}, {}
    if include_directories:
        directories[""] = 0o755 if normalize_modes else root.stat().st_mode & 0o7777
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        info = path.lstat()
        mode = info.st_mode
        if public:
            require(name.split("/", 1)[0] in PUBLIC_TOP_LEVEL, f"unexpected public bundle root entry: {name}")
            private = any(part.lower() in PRIVATE_NAMES or part.lower().startswith(".env.")
                          for part in path.relative_to(root).parts)
            # The checksum-verified official Node distribution contains this
            # empty npm default. No other npmrc, content or symlink is public.
            require(not private or (name == OFFICIAL_EMPTY_NPMRC and stat.S_ISREG(mode)
                                    and info.st_size == 0 and sha(path) == EMPTY_SHA256),
                    f"private file cannot enter public bundle: {name}")
        if name in exclude:
            continue
        if stat.S_ISLNK(mode):
            require(path.resolve().is_relative_to(root) and path.exists(), f"external or broken symlink: {name}")
            target = os.readlink(path)
            require(not public or not os.path.isabs(target), f"absolute symlink is not relocatable: {name}")
            links[name] = target
        elif stat.S_ISREG(mode):
            files[name] = {"sha256": sha(path), "mode": public_mode(mode) if normalize_modes else mode & (0o7777 if include_directories else 0o777)}
        else:
            require(stat.S_ISDIR(mode), f"special file: {name}")
            if include_directories:
                directories[name] = 0o755 if normalize_modes else mode & 0o7777
    result = {"files": files, "symlinks": links}
    if include_directories:
        result["directories"] = directories
    return result


def checked_build(build, commit):
    require(build.get("channel") == "source" and build.get("commit") == commit
            and build.get("dirty") is False, "stale, dirty or non-source compiled artifact; rebuild the clean commit")


def published_plugin_files(tracked, package):
    # Mirror this plugin's explicit npm publish surface, not arbitrary globs or
    # paths from a manifest. package.json and LICENSE are npm's implicit files.
    published = package.get("files")
    allowed = {"icon.svg", "skills", "LICENSE", "hooks"}
    require(isinstance(published, list) and all(isinstance(p, str) and p in allowed for p in published)
            and len(published) == len(set(published)), "unsupported Evo-Harness package files declaration")
    prefix = "plugins/evo-harness-rsi-toolkit/"
    names = {p.removeprefix(prefix) for p in tracked if p.startswith(prefix)}
    directories = set(published) & {"skills", "hooks"}
    files = {"package.json", "LICENSE"} | (set(published) & {"icon.svg"})
    return {p for p in names if p in files or any(p.startswith(d + "/") for d in directories)}


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
        require(inventory(deployed / "dist", normalize_modes=True)
                == inventory(source / "packages" / name / "dist", normalize_modes=True),
                f"deployed {name} differs from source build")
    plugin = app / "node_modules" / PLUGIN
    require(plugin.resolve().is_relative_to(root), "plugin resolves outside bundle")
    plugin_files = inventory(plugin, normalize_modes=True)
    source_plugin = source / "plugins/evo-harness-rsi-toolkit"
    tracked = set(git(source, "ls-tree", "-r", "--name-only", commit, "--",
                      "plugins/evo-harness-rsi-toolkit").splitlines())
    expected = published_plugin_files(tracked, read(source_plugin / "package.json"))
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
                "mode_policy": MODE_POLICY,
                "payload": inventory(root, exclude=("bundle.json",), normalize_modes=True,
                                     include_directories=True, public=True)}
    (root / "bundle.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {k: manifest[k] for k in ("source_commit", "penguin_version", "node_version", "cli_sha256", "plugin_sha256")}


def verify(root, commit, staged=False):
    root = Path(root).resolve()
    require(not (root / "bundle.json").is_symlink(), "bundle manifest cannot be a symlink")
    manifest = read(root / "bundle.json")
    require(manifest["schema"] == "evo-harness-source-bundle-v1" and manifest["source_commit"] == commit,
            "bundle source identity mismatch")
    public = manifest.get("mode_policy") == MODE_POLICY
    require(manifest.get("mode_policy") in (None, MODE_POLICY), "unknown public mode policy")
    require(not staged or public, "staging projection requires a public mode manifest")
    if public:
        mode = (root / "bundle.json").stat().st_mode
        require((public_mode(mode) if staged else mode & 0o7777) == 0o644, "bundle manifest permissions differ")
    require(manifest["payload"] == inventory(root, exclude=("bundle.json",), normalize_modes=staged,
                                            include_directories=public, public=public), "bundle payload changed")
    checked_build(read(root / "cli-build.json"), commit)
    checked_build(read(root / "native-plugin-probe.json")["build"], commit)
    return {"verified": True, "source_commit": commit, "model_calls": 0}


def verify_staged(root, commit):
    """Check the exact public projection; never chmod pnpm/store hardlinks."""
    return verify(root, commit, staged=True)


def verify_archive(archive, commit):
    """Check this builder's sealed tar metadata/content without extracting it.

    This is a publication consistency check after record/verify-staged, not a
    general-purpose untrusted archive extraction facility.
    """
    # gzip random access would repeatedly decompress a large pnpm graph when
    # manifest order differs from tar's filesystem order. Read every payload
    # once; regular-file hashes also authenticate later hardlink entries.
    with tarfile.open(archive, "r|gz") as tar:
        table, fingerprints, manifest = {}, {}, None
        for member in tar:
            name = member.name.rstrip("/")
            require(name not in table, "duplicate bundle archive member")
            table[name] = member
            if member.isfile():
                handle = tar.extractfile(member)
                require(handle is not None, "missing archive file content")
                if name == "penguin-bench/bundle.json":
                    require(member.mode == 0o644 and member.size < 64 * 1024 * 1024,
                            "invalid archive manifest")
                    manifest = json.load(handle)
                else:
                    fingerprints[name] = stream_sha(handle)
            elif member.islnk():
                require(member.linkname in fingerprints and table[member.linkname].isfile(),
                        "archive hardlink target differs")
                fingerprints[name] = fingerprints[member.linkname]
        manifest_member = table.get("penguin-bench/bundle.json")
        require(manifest_member and manifest_member.isfile() and manifest_member.mode == 0o644
                and manifest_member.size < 64 * 1024 * 1024 and manifest is not None, "invalid archive manifest")
        require(manifest.get("schema") == "evo-harness-source-bundle-v1"
                and manifest.get("source_commit") == commit and manifest.get("mode_policy") == MODE_POLICY,
                "archive source/mode identity mismatch")
        payload = manifest["payload"]
        expected = {"penguin-bench/bundle.json"}
        expected.update("penguin-bench" + ("/" + name if name else "") for name in payload["directories"])
        expected.update("penguin-bench/" + name for name in payload["files"])
        expected.update("penguin-bench/" + name for name in payload["symlinks"])
        require(set(table) == expected, "archive members differ from sealed payload")
        for name, mode in payload["directories"].items():
            member = table["penguin-bench" + ("/" + name if name else "")]
            require(member.isdir() and mode == member.mode == 0o755, "archive directory permissions differ")
        for name, facts in payload["files"].items():
            member = table["penguin-bench/" + name]
            require((member.isfile() or member.islnk()) and member.mode == facts["mode"]
                    and member.mode in (0o644, 0o755), "archive file permissions differ")
            require(fingerprints[member.name] == facts["sha256"],
                    "archive payload content changed")
        for name, target in payload["symlinks"].items():
            member = table["penguin-bench/" + name]
            require(member.issym() and member.linkname == target, "archive symlink differs")
    return {"verified_archive": True, "source_commit": commit, "mode_policy": MODE_POLICY, "model_calls": 0}


if __name__ == "__main__":
    try:
        command, *args = sys.argv[1:]
        result = {"check-source": check_source, "record": record, "verify": verify,
                  "verify-staged": verify_staged, "verify-archive": verify_archive}[command](*args)
        print(json.dumps(result or {"clean_source": True}))
    except (ValueError, KeyError, OSError, TypeError, tarfile.TarError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(2)
