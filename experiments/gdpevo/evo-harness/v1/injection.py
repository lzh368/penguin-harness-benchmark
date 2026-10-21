"""Read-only held-out State/selection evidence, separate from verifier scoring."""
import hashlib
from datetime import datetime
import json
from pathlib import Path, PurePosixPath
import re
import shlex


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "missing/linked injection evidence")
    return json.loads(path.read_text())


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _path(value, workspace):
    require(isinstance(value, str) and value, "missing evidence path")
    path = PurePosixPath(value)
    require(".." not in path.parts, "noncanonical evidence path")
    return str(path if path.is_absolute() else PurePosixPath(workspace) / path)


def _native_read(pairs, path, raw, workspace):
    lines, observed = raw.decode().split("\n"), {}
    if lines[-1] == "":
        lines.pop()
    last = -1
    for i, pair in enumerate(pairs):
        call = pair[0]
        if call.get("name") != "read_file":
            continue
        args = json.loads(call["arguments"])
        if _path(args.get("file_path"), workspace) != path:
            continue
        for text in pair[1]["output"].split("\n"):
            match = re.match(r"^\s*(\d+)\t(.*)$", text)
            if match:
                observed[int(match[1])] = match[2]
        if last < 0 and lines and all(observed.get(n) == line for n, line in enumerate(lines, 1)):
            last = pair[1]["_position"]
    require(lines and all(observed.get(i) == line for i, line in enumerate(lines, 1)),
            "frozen reader/index not fully observed through native read_file")
    return last


def _selector_args(call, state, workspace):
    if call.get("name") != "exec_command":
        return None
    args = json.loads(call["arguments"])
    command = args.get("cmd", args.get("command", ""))
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    workdir = _path(args.get("workdir", workspace), workspace)
    if len(argv) >= 3 and argv[0] == "cd" and argv[2] == "&&":
        workdir = _path(argv[1], workdir)
        argv = argv[3:]
    if len(argv) != 5 or argv[:2] != ["python3", "-B"] or argv[3] != "--ids":
        return None
    if _path(argv[2], workdir) != state + "/skills/evo-harness-learned/select.py":
        return None
    return argv[4].split(",") if argv[4] else []


def _trial(trial, cell, frozen, req, evo, acceptance, launch, config):
    result = read(trial / "result.json")
    metadata = result["agent_result"]["metadata"]
    runtime = req["runtime"]
    require(result["task_name"] == launch["task_id"] and result["trial_name"] == trial.name,
            "cell binds another task/trial")
    argv = launch["argv_template"]
    expected_task = argv[argv.index("-p") + 1]
    for original in (result["task_id"]["path"], result["config"]["task"]["path"]):
        require(_path(original, launch["cwd"]) == expected_task, "trial original task path differs from registered mapping")
    original_agent = result["config"]["agent"]
    require(original_agent["name"] == config["adapter"]["agent"]
            and original_agent["model_name"] == runtime["provider"] + "/" + runtime["model_id"],
            "trial launched another adapter/model")
    expected_options = dict(argv[i + 1].split("=", 1) for i, value in enumerate(argv) if value == "--ak")
    actual_options = original_agent["kwargs"]
    require(set(actual_options) == set(expected_options), "trial added or omitted an adapter option")
    for key, value in expected_options.items():
        if key != "agent_state_tar":
            require(str(actual_options.get(key)) == value, "trial adapter option differs: " + key)
    original_config = result["config"]
    require(original_config["timeout_multiplier"] == 1 and original_config["agent_timeout_multiplier"] in (None, 1)
            and original_config["verifier_timeout_multiplier"] in (None, 1)
            and original_config["agent_setup_timeout_multiplier"] == 2.5
            and not original_config["install_only"], "trial timeout/execution policy changed")
    require(all(original_agent[key] is None for key in ("override_timeout_sec", "override_setup_timeout_sec", "max_timeout_sec"))
            and not original_agent["resume_trajectory"] and original_agent["load_trajectory"] is None,
            "trial altered Target timeout or resumed a prior trajectory")
    require(original_agent["extra_allowed_hosts"] == config["adapter"]["allowed_agent_hosts"]
            and not original_agent["mcp_servers"] and not original_agent["skills"],
            "trial changed allowed hosts/tools/skills")
    environment = original_config["environment"]
    require(environment["type"] == "docker" and environment["import_path"] is None
            and environment["delete"] is True and not environment["extra_docker_compose"]
            and not environment["extra_allowed_hosts"] and not environment["kwargs"] and not environment["mounts"],
            "trial altered isolation or cleanup configuration")
    require(all(environment[key] is None for key in ("override_cpus", "override_memory_mb", "override_storage_mb",
                                                    "override_gpus", "override_tpu")), "trial changed frozen resources")
    verifier = original_config["verifier"]
    require(verifier["override_timeout_sec"] is None and verifier["max_timeout_sec"] is None and not verifier["disable"],
            "trial changed verifier execution")
    require(not original_config["extra_instruction_paths"] and not original_config["extra_instructions"]
            and original_config["source_trial"] is None, "trial substituted instructions or prior results")
    archive = Path(actual_options["agent_state_tar"])
    require(archive.is_absolute() and archive.parent == Path(req["benchmark"]) / ".jobs"
            and archive.name == trial.parent.name + ".agent-state.tar.gz", "trial did not use its own native State archive")
    info = result["agent_info"]
    require(info["name"] == "penguin" and info["model_info"] == {
        "name": runtime["model_id"], "provider": runtime["provider"]}, "actual agent identity differs")
    installed = read(trial / "agent/penguin-install.json")
    require(installed["agent_state"] == "custom" and installed["install"] == "bundle"
            and installed["bundle_sha256"] == config["bundle_sha256"]
            and installed["node_version"] == config["adapter"]["node_version"]
            and installed["penguin_version"] == config["adapter"]["penguin_version"],
            "actual installed CLI/State differs from frozen bundle")
    require(result["exception_info"] is None and result["finished_at"]
            and metadata["status"] in ("completed", "timeout", "aborted"), "trial did not finish a valid scored execution")
    usage = result["agent_result"].get("model_usage")
    if usage:
        require(set(usage) == {runtime["provider"] + "/" + runtime["model_id"]},
                "actual trial model usage differs from frozen runtime")
    require(metadata["provider"] == runtime["provider"] and metadata["model_id"] == runtime["model_id"]
            and metadata["thinking"] == runtime["thinking_level"], "trial runtime differs from frozen runtime")
    require(metadata["agent_state"] == "custom" and metadata.get("requests", 1) > 0, "trial did not run the assigned custom State")
    require(metadata["native_sessions_idle"] is True and metadata["process_cleanup"] == "server_shutdown",
            "trial cleanup evidence incomplete")
    reward = result["verifier_result"]["rewards"]["reward"]
    evo.finite(reward, high=1)
    require(abs(cell["score"] - round(reward * 100, 2)) <= 1e-8, "cell score differs from original verifier")
    actual_cost = result["agent_result"]["cost_usd"]
    require(cell["cost"] is None if actual_cost is None else cell["cost"] is not None
            and abs(cell["cost"] - actual_cost) <= 0.000001, "cell cost differs from native trial")
    timing = result["agent_execution"]
    duration = (datetime.fromisoformat(timing["finished_at"].replace("Z", "+00:00"))
                - datetime.fromisoformat(timing["started_at"].replace("Z", "+00:00"))).total_seconds() * 1000
    require(duration >= 0 and abs(cell["duration_ms"] - duration) <= 1, "cell duration differs from original trial")
    before, ready, after = (read(trial / "agent" / f"penguin-agent-state-{point}.json") for point in ("before", "ready", "after"))
    expected = {name: digest(raw) for name, raw in frozen.items()}
    require(before["schema"] == ready["schema"] == after["schema"] == "penguin-agent-state-files-v1", "unknown State evidence schema")
    require(before["files"] == expected, "installed State differs from frozen native snapshot")
    state = before["state"]
    require(ready["state"] == after["state"] == state, "trial State path changed")
    # A fresh native State freezes max_turns=-1 and the complete builtin tool
    # table before transport. Actual SDK loading and the native maxTurns YAML
    # update are byte-idempotent for this supported emitter shape (covered by
    # the model-free native roundtrip probe). No startup rewrite is exempted.
    require(ready["files"] == expected, "frozen State changed during native startup")
    require(ready["files"] == after["files"], "frozen State changed during held-out test")
    trace_root = trial / "agent/penguin/traces"
    sessions = {}
    for path in sorted(trace_root.rglob("*.jsonl")):
        require(not path.is_symlink() and path.resolve().is_relative_to(trace_root.resolve()), "linked trace evidence")
        rows = [json.loads(line) for line in path.read_text().split("\n") if line]
        metas = [row["payload"] for row in rows if row.get("type") == "session_meta" and not row.get("origin")]
        require(len(metas) == 1, "invalid native trace metadata")
        if metas[0]["session_id"] == metadata["session_id"]:
            sessions[str(path)] = (metas[0], rows)
    require(sessions, "missing original test Session trace")
    calls, outputs, metas, hashes, requests, row_position = {}, {}, [], {}, 0, 0
    for path, (meta, rows) in sessions.items():
        metas.append(meta)
        hashes[Path(path).name] = digest(Path(path).read_bytes())
        for row in rows:
            row_position += 1
            if row.get("origin"):
                continue
            if row.get("type") == "event_msg" and row.get("payload", {}).get("type") == "request_begin":
                requests += 1
            if row.get("type") != "model_msg":
                continue
            payload = row["payload"]
            dest = calls if payload.get("type") == "tool_call" else outputs if payload.get("type") == "tool_call_output" else None
            if dest is not None:
                ident = payload["tool_call_id"]
                require(ident not in dest, "ambiguous native test tool evidence")
                dest[ident] = {**payload, "_position": row_position}
    meta = metas[0]
    fields = ("session_id", "source", "provider", "model_id", "workspace", "agent_state")
    require(all(all(m[k] == meta[k] for k in fields) for m in metas), "test trace segment identity changed")
    require(meta["source"] in ("cli", "benchmark") and meta["provider"] == runtime["provider"]
            and meta["model_id"] == runtime["model_id"] and meta["agent_state"] == state,
            "native test trace binding mismatch")
    require(requests > 0 and set(calls) == set(outputs), "incomplete native request/action/observation pairing")
    pairs = [(call, outputs[key]) for key, call in calls.items()]
    slot = state + "/skills/evo-harness-learned"
    reader_read = _native_read(pairs, slot + "/SKILL.md", frozen[acceptance.SLOT + "/SKILL.md"], meta["workspace"])
    index_read = _native_read(pairs, slot + "/index.json", frozen[acceptance.SLOT + "/index.json"], meta["workspace"])
    attempts = [(i, ids, observation) for i, (call, observation) in enumerate(pairs)
                if (ids := _selector_args(call, state, meta["workspace"])) is not None]
    selections = []
    for attempt in attempts:
        stdout = attempt[2]["output"]
        has_receipt = any(isinstance(value, dict) and "selected" in value for value in evo.json_values(stdout))
        if not has_receipt and "Traceback (most recent call last):" in stdout and re.search(
                r"(?m)^AssertionError: (?:entry budget exceeded|context budget exceeded; select fewer entries)$", stdout):
            continue
        selections.append(attempt)
    require(len(selections) == 1, "requires exactly one successful original selector operation")
    position, selected, observation = selections[0]
    selector_call_position = pairs[position][0]["_position"]
    require(reader_read < selector_call_position and index_read < selector_call_position,
            "reader/index must be observed before selection")
    selector_observed = observation["_position"]
    for i, (call, _) in enumerate(pairs):
        if call["_position"] >= selector_observed:
            continue
        if call.get("name") == "read_file" or any(attempt[0] == i for attempt in attempts):
            continue
        require(call.get("name") == "exec_command", "task action occurred before guidance selection")
        args = json.loads(call["arguments"])
        command = args.get("cmd", args.get("command", ""))
        require("\n" not in command, "directory inspection must be one simple command")
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        argv = list(lexer)
        require(argv and argv[0] in ("pwd", "ls", "find") and not any(
            re.fullmatch(r"[;&|<>]+", token) or "$" in token or "`" in token
            for token in argv), "only native reads and simple directory inspection may precede selection")
        if argv[0] == "find":
            require(not any(token in ("-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls")
                            for token in argv), "find action may not precede selection")
    index = json.loads(frozen[acceptance.SLOT + "/index.json"])
    entries = {entry["id"]: entry for entry in index["entries"]}
    require(len(selected) == len(set(selected)) and len(selected) <= index["max_entries"]
            and all(key in entries for key in selected), "selector entry budget/identity mismatch")
    text = "\n\n".join(frozen[acceptance.SLOT + "/" + entries[key]["file"]].decode() for key in selected)
    receipt = {"selected": selected, "entry_hashes": {key: entries[key]["sha256"] for key in selected},
               "text": text, "bytes": len(text.encode()), "sha256": digest(text.encode())}
    require(receipt["bytes"] <= index["max_bytes"] and evo.contains_json(observation["output"], receipt),
            "selected guidance was not fully observed or exceeds frozen budget")
    for call, _ in pairs:
        if call.get("name") != "read_file":
            continue
        args = json.loads(call["arguments"])
        path = _path(args.get("file_path"), meta["workspace"])
        for key, entry in entries.items():
            require(path != slot + "/" + entry["file"] or key in selected, "unselected entry read bypasses injection budget")
    return {"case_id": cell["case_id"], "run": cell["run"], "trial": str(trial),
            "session_id": cell["session_id"], "native_session_id": metadata["session_id"],
            "selected": selected, "bytes": receipt["bytes"], "sha256": receipt["sha256"], "trace_sha256": hashes,
            "score": round(reward * 100, 2), "cost_status": metadata.get("cost_status", "unknown"),
            "cost": actual_cost, "duration_ms": round(duration)}


def verify_matrix(req, matrix, acceptance, evo, registration, validate_config):
    config = read(req["config"])
    deploy = config["deployment"]
    registered = registration.execute({"op": "verify-benchmarks", "config": req["config"],
        "config_sha256": req["config_sha256"], "repo": deploy["benchmark_checkout"],
        "output": str(Path(deploy["penguin_home"]) / deploy["project_id"] / "benchmarks")}, validate_config)
    require(req["runtime"] == config["runtime"], "injection runtime differs from frozen full runtime")
    require(Path(req["benchmark"]) == Path(deploy["penguin_home"]) / deploy["project_id"] / "benchmarks" / registered["test"]["benchmark_id"],
            "injection names another Benchmark")
    matrix_req = read(req["matrix_request"])
    require(matrix_req["case_ids"] == registered["test"]["case_ids"], "injection roster differs from explicit native mapping")
    require(matrix(matrix_req)["complete"] is True, "injection requires complete native test matrix")
    require(all(matrix_req["identity"][key] == req["runtime"][key] for key in ("provider", "model_id", "thinking_level"))
            and matrix_req["identity"]["expected_version"] == req["expected_version"], "injection matrix identity mismatch")
    frozen = acceptance.snapshot(req["snapshot"])["files"]
    require(acceptance.config_version(frozen["system_config.yaml"])[0] == req["expected_version"], "injection snapshot version mismatch")
    acceptance.package_from_snapshot(frozen, evo)
    index = json.loads(frozen[acceptance.SLOT + "/index.json"])
    acceptance.verify_output_budget(frozen["system_config.yaml"], index["max_bytes"])
    jobs = Path(req["benchmark"]) / ".jobs"
    require(jobs.is_dir() and not jobs.is_symlink(), "native Benchmark jobs missing")
    trials = {}
    for result in jobs.glob("*/*/result.json"):
        trial = result.parent
        require(trial.resolve().is_relative_to(jobs.resolve()) and not trial.is_symlink(), "trial path escape")
        trials.setdefault(trial.name, []).append(trial)
    receipts = []
    for cell in read(matrix_req["cells"]):
        ident = cell["session_id"]
        require(isinstance(ident, str) and ident.startswith("harbor:"), "test cell does not bind a Harbor trial")
        name = ident.removeprefix("harbor:")
        require(re.fullmatch(r"[A-Za-z0-9_.-]+", name) and len(trials.get(name, [])) == 1,
                "test trial identity missing or ambiguous")
        launch = read(Path(req["benchmark"]) / cell["case_id"] / "statement/harbor-launch.json")
        require(launch["task_id"] == registered["test"]["case_to_task"][cell["case_id"]], "registered task mapping changed")
        receipts.append(_trial(trials[name][0], cell, frozen, req, evo, acceptance, launch, config))
    return {"schema": "evo-injection-matrix-v1", "complete": True, "cells": receipts,
            "runtime": req["runtime"], "snapshot_sha256": digest(Path(req["snapshot"]).read_bytes()), "model_calls": 0}
