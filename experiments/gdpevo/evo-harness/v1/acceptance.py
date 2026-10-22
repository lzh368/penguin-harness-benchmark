"""Deterministic freeze acceptance; no model calls, task launch or orchestration."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile

SLOT = "skills/evo-harness-learned"
METHOD_PREFIX = "skills/evo-method-"
FULL_SKILL_MAX_BYTES = 16000
TRANSPORT_EXCLUDES = frozenset((".vault.toml", "memory", "schedule"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def verify_skill_budget(package):
    """The GDPevo experiment fixes this value; generic Evo exporters do not."""
    require(type(package.get("max_bytes")) is int
            and package["max_bytes"] == FULL_SKILL_MAX_BYTES,
            "GDPevo complete Skill byte budget must be exactly 16000")
    return package


def config_version(raw):
    """Accept the native emitter's plain top-level keys, with one integer version.

    This is intentionally not a general YAML parser: alternate key spellings,
    flow maps, document directives and duplicate top-level keys are refused.
    Everything except the single version line is compared byte for byte.
    """
    text = raw.decode("utf-8")
    require("\r" not in text and "\x00" not in text, "unsupported native system_config encoding")
    keys, version, remaining = set(), None, []
    for line in text.splitlines(keepends=True):
        if line.strip() and not line.startswith((" ", "#")):
            match = re.fullmatch(r"([a-zA-Z_][a-zA-Z0-9_]*):[^\n]*(?:\n)?", line)
            require(match is not None and match[1] not in keys, "unsupported or duplicate native top-level YAML key")
            key = match[1]
            keys.add(key)
            if key == "version":
                match = re.fullmatch(r"version: ([1-9][0-9]*)\n", line)
                require(match is not None, "native version must be one plain positive integer line")
                version = int(match[1])
                continue
        remaining.append(line)
    require(version is not None, "native State version is missing")
    return version, "".join(remaining).encode("utf-8")


def verify_output_budget(raw, max_bytes):
    """Native emitter syntax, not an inferred/default command output limit.

    A UTF-8 byte may require six JSON escape characters. Preserve the method's
    16000-byte complete-Skill budget and reserve room for native output wrappers.
    Both foreground and resumed command output must carry the full file.
    """
    text = raw.decode()
    require(re.findall(r"(?m)^max_turns: (-?\d+)$", text) == ["-1"], "freeze native max_turns: -1 before transport")
    require("tools:\n  builtin:\n" in text, "initialize the full native builtin tool configuration")
    required = 6 * max_bytes + 8192
    for name in ("exec_command", "input_command"):
        blocks = re.findall(r"(?m)^    - name: " + name + r"\n((?:(?!    - name:|  [A-Za-z_]|\S).*(?:\n|$))*)", text)
        require(len(blocks) == 1, "missing/duplicate native command output configuration")
        values = re.findall(r"(?m)^      maxOutputLength: ([0-9]+)$", blocks[0])
        require(len(values) == 1 and int(values[0]) >= required,
                f"native {name} maxOutputLength must be at least {required} for complete Skill reads")
    return required


def snapshot(path):
    """Read regular files only; never extract an archive-controlled path."""
    try:
        return _snapshot(path)
    except (tarfile.TarError, EOFError) as exc:
        raise ValueError("invalid native snapshot archive") from exc


def transported_filemap(root, evo):
    """The same top-level exclusions as native State transport/state-files.

    Keep the generic Evo filemap strict for learning evidence and packages.
    Native memory/schedule scaffolding is not part of a frozen test payload.
    """
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), "invalid Target State")
    files = {}
    for path in sorted(root.iterdir()):
        if path.name in TRANSPORT_EXCLUDES:
            continue
        require(not path.is_symlink(), "linked Target State entry")
        if path.is_dir():
            files.update({path.name + "/" + name: value for name, value in evo.filemap(path).items()})
        else:
            require(path.is_file(), "non-regular Target State entry")
            files[path.name] = sha(path.read_bytes())
    return files


def _snapshot(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "snapshot must be a regular native archive")
    raw = path.read_bytes()
    files, entries = {}, {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive:
            name = member.name.removesuffix("/") if member.isdir() else member.name
            parts = name.split("/")
            require(parts[0] == "agent_state" and all(p not in ("", ".", "..") for p in parts)
                    and "\\" not in name and "\x00" not in name,
                    "snapshot path must stay under agent_state without aliases")
            require(len(parts) == 1 or parts[1] not in TRANSPORT_EXCLUDES,
                    "native snapshot contains excluded transport material")
            require(name not in entries, "duplicate snapshot entry")
            require(member.isdir() or member.type in (tarfile.REGTYPE, tarfile.AREGTYPE),
                    "snapshot links and non-regular entries are forbidden")
            require(len(parts) > 1 or member.isdir(), "agent_state root must be a directory")
            entries[name] = "dir" if member.isdir() else "file"
            if member.isdir():
                require(not name.startswith("agent_state/" + SLOT + "/")
                        and not (name.startswith("agent_state/" + METHOD_PREFIX) and len(parts) > 3),
                        "full Skill snapshot cannot contain nested package directories")
                continue
            stream = archive.extractfile(member)
            require(stream is not None, "snapshot file cannot be read")
            body = stream.read()
            require(len(body) == member.size, "truncated snapshot file")
            files["/".join(parts[1:])] = body
    require(entries.get("agent_state") == "dir", "native snapshot agent_state root is missing")
    for name in entries:
        require(all(entries.get(str(parent)) != "file" for parent in PurePosixPath(name).parents),
                "snapshot file/directory path conflict")
    for name, kind in entries.items():
        if kind == "dir" and name.startswith("agent_state/" + METHOD_PREFIX):
            require(re.fullmatch(r"agent_state/skills/evo-method-[0-9a-f]{20}", name)
                    and entries.get(name + "/SKILL.md") == "file",
                    "native method directory must contain its manifest-bound SKILL.md")
    require("system_config.yaml" in files and "AGENTS.md" in files, "native snapshot State files are missing")
    return {"snapshot_sha256": sha(raw), "files": files}


def learned_state_file(name):
    return name.startswith(SLOT + "/") or name.startswith(METHOD_PREFIX)


def package_files_from_state(data, evo):
    """Recover the export package using its exact, canonical sibling mapping.

    Reserved sibling paths cannot be hidden outside the learned slot. The Evo
    parser validates path/name identity; the complete package validator verifies
    bodies and the State mapper must reproduce every reserved file byte.
    """
    selected = {name: raw for name, raw in data.items() if learned_state_file(name)}
    reader_path = SLOT + "/SKILL.md"
    require(reader_path in selected, "snapshot learned slot must contain only SKILL.md and declared native methods")
    reader = evo.parse_reader(selected[reader_path])
    package = {"SKILL.md": selected[reader_path]}
    for entry in reader["entries"]:
        require(entry["state_path"] in selected, "snapshot lacks a declared native method Skill")
        package[entry["path"]] = selected[entry["state_path"]]
    require(evo.package_state_files(package) == selected,
            "snapshot learned slot must contain only SKILL.md and exact manifest native methods")
    return package


def package_from_snapshot(data, evo):
    selected = package_files_from_state(data, evo)
    with tempfile.TemporaryDirectory(prefix="evo-freeze-verify-") as temp:
        root = Path(temp)
        for name, raw in selected.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        return verify_skill_budget(evo.validate_package(root))


def verify_freeze(req, verify_export, evo):
    # Always rerun full provenance validation, even for an empty learned package.
    # Missing/incomplete seals are errors, never a reason to fall back to H0.
    export_raw = Path(req["export_request"]).read_bytes()
    export_req = json.loads(export_raw)
    exported = verify_export(export_req)
    require(isinstance(exported, dict) and exported.get("complete") is not False,
            "training export verification is incomplete")
    training_raw = Path(export_req["training_export"]).read_bytes()
    require(json.loads(training_raw).get("complete") is True, "training export incomplete")
    require(req["runtime"] == export_req["runtime"], "freeze runtime differs from training export")
    runtime = req["runtime"]
    require(isinstance(runtime, dict) and all(isinstance(runtime.get(k), str) and runtime[k].strip()
            for k in ("provider", "model_id", "thinking_level")), "invalid frozen runtime")
    roster = req["test_case_ids"]
    require(isinstance(roster, list) and len(roster) == 5 and all(isinstance(c, str) for c in roster)
            and len(roster) == len(set(roster)), "freeze requires five distinct test cases")
    for case in roster:
        evo.ident(case)
    versions = [req["h0_version"], req["final_version"]]
    require(all(type(v) is int and v > 0 for v in versions), "invalid frozen versions")
    active = req.get("active_state", "final")
    require(active in ("h0", "final"), "invalid active State")
    h0, final = snapshot(req["h0_snapshot"]), snapshot(req["final_snapshot"])
    h0_version, h0_config = config_version(h0["files"]["system_config.yaml"])
    final_version, final_config = config_version(final["files"]["system_config.yaml"])
    require([h0_version, final_version] == versions, "snapshot version differs from frozen version")
    h0_package = package_from_snapshot(h0["files"], evo)
    final_package = package_from_snapshot(final["files"], evo)
    require(h0_package["empty"] is True, "H0 must start with the valid empty learned reader")
    require(final_package == exported, "final snapshot learned slot differs from verified export")
    h0_skill = evo.parse_reader(h0["files"][SLOT + "/SKILL.md"])
    final_skill = evo.parse_reader(final["files"][SLOT + "/SKILL.md"])
    for state in (h0, final):
        verify_output_budget(state["files"]["system_config.yaml"], h0_skill["max_bytes"])
    require(h0_skill["max_bytes"] == final_skill["max_bytes"],
            "complete Skill byte budget must remain frozen between H0 and final")
    automatic = req.get("context_transport") == "agents-md-v1"
    if automatic:
        profile_path = Path(export_req["private_learning"]) / "frozen-training.json"
        require(read(profile_path).get("context_transport") == "agents-md-v1", "context transport differs from frozen training")
        for state in (h0, final):
            files = state["files"]
            require(b"{{AGENTS_MD}}" in files["system_config.yaml"] and re.search(rb"(?m)^skills:\n  enabled: false$", files["system_config.yaml"]), "native context requires AGENTS_MD and disabled duplicate Skill metadata index")
            context = evo.model_context(package_files_from_state(files, evo))
            _, installed = evo.split_context(files["AGENTS.md"].decode())
            require(installed == context["block"], "State context is not the complete deterministic frozen Skill")
    def outside_slot(files):
        return {name: (evo.split_context(raw.decode())[0].encode() if automatic and name == "AGENTS.md" else raw)
                for name, raw in files.items() if name != "system_config.yaml" and not learned_state_file(name)}
    require(h0_config == final_config and outside_slot(h0["files"]) == outside_slot(final["files"]),
            "State changed outside learned slot and top-level version")
    equivalent = h0_package == final_package
    if equivalent:
        require(h0_version == final_version and h0["files"] == final["files"],
                "equivalent H0 requires identical State content and versions")
    else:
        require(final_version > h0_version, "changed learned slot requires a higher final version")
    for state, version in ((h0, h0_version), (final, final_version)):
        state["filemap"] = {name: sha(raw) for name, raw in sorted(state.pop("files").items())}
        state["filemap_sha256"] = sha(evo.encoded(state["filemap"]))
        state["version"] = version
    target_files = transported_filemap(req["target_state"], evo)
    require(target_files == (h0 if active == "h0" else final)["filemap"],
            "live Target State differs from active frozen snapshot")
    return {"schema": "evo-freeze-verification-v1", "complete": True, "equivalent_h0": equivalent,
            "active_state": active, "runtime": runtime, "test_case_ids": roster,
            "export_request_sha256": sha(export_raw), "training_export_sha256": sha(training_raw),
            "skill_sha256": exported["sha256"], "h0": h0, "final": final,
            "target_state_filemap_sha256": sha(evo.encoded(target_files)),
            "verification_input_sha256": sha(evo.encoded(req)), "model_calls": 0}
