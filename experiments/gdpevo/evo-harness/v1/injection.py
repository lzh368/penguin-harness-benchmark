"""Read-only held-out State/full-Skill evidence, separate from verifier scoring."""
import hashlib
import importlib.util
from datetime import datetime
import json
from pathlib import Path
import re


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "missing/linked injection evidence")
    return json.loads(path.read_text())


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class IncompleteSkillObservation(ValueError):
    """Missing read evidence; never a substitute for actual State identity."""


class GeneralBodyRead(ValueError):
    """A loading-policy deviation, distinct from a missing or forged trial."""


def _native_read(pairs, path, raw, workspace, slot, inspection, skill_files=None):
    lines, observed = raw.decode().split("\n"), {}
    if lines[-1] == "":
        lines.pop()
    last = -1
    # Parallel tool calls can finish out of call order. Coverage exists only
    # after every required page's original observation has actually arrived.
    for pair in sorted(pairs, key=lambda value: value[1]["_position"]):
        call = pair[0]
        observation = pair[1]
        if (observation.get("stop_reason") != "completed" or observation.get("images")
                or call["_position"] >= observation["_position"]):
            continue
        if call.get("name") == "exec_command":
            if inspection.complete_file_stdout(call, observation["output"], raw, path, workspace, slot,
                                                skill_files=skill_files):
                last = observation["_position"] if last < 0 else min(last, observation["_position"])
            continue
        if call.get("name") != "read_file":
            continue
        args = json.loads(call["arguments"])
        if inspection._path(args.get("file_path"), workspace) != path:
            continue
        for text in pair[1]["output"].split("\n"):
            match = re.match(r"^\s*(\d+)\t(.*)$", text)
            if match:
                observed[int(match[1])] = match[2]
        if last < 0 and lines and all(observed.get(n) == line for n, line in enumerate(lines, 1)):
            last = pair[1]["_position"]
    if last < 0:
        raise IncompleteSkillObservation("frozen Skill not fully observed through native read_file or exact foreground cat")
    return last



def loading_files(frozen, evo, acceptance):
    files = evo.package_state_files(acceptance.package_files_from_state(frozen, evo))
    reader = evo.parse_reader(files[acceptance.SLOT + "/SKILL.md"])
    required = {acceptance.SLOT + "/SKILL.md", *(entry["state_path"] for entry in reader["entries"]
                                                if entry["load_mode"] == "body")}
    forbidden = {entry["state_path"] for entry in reader["entries"] if entry["load_mode"] == "description"}
    return files, {path: files[path] for path in sorted(required)}, forbidden


def reject_general_body_reads(pairs, forbidden, workspace, inspection):
    """General descriptions are already in the Reader; no Agent file read adds bodies.

    Reuse the existing literal command grammar for actual cat/head operations,
    not substring matching of documentation or echoed paths. The cooperative
    trace policy does not claim to sandbox obfuscated arbitrary programs.
    """
    for call, _ in pairs:
        if call.get("name") == "read_file":
            args = json.loads(call["arguments"])
            if inspection._path(args.get("file_path"), workspace) in forbidden:
                raise GeneralBodyRead("general Skill body read violates description-only loading")
        elif call.get("name") == "exec_command":
            try:
                commands, workdir, _ = inspection._shell_commands(call, workspace)
            except (ValueError, TypeError, KeyError):
                continue
            for command in commands:
                argv = command["argv"]
                if argv[0] == "cd" and len(argv) == 2:
                    workdir = inspection._path(argv[1], workdir)
                    continue
                if argv[0] not in ("cat", "head"):
                    continue
                paths = [inspection._path(value, workdir) for value in inspection._file_operands(argv)]
                if forbidden.intersection(paths):
                    raise GeneralBodyRead("general Skill body read violates description-only loading")

def _trial(trial, cell, frozen, req, evo, acceptance, launch, config, *, audit_policy="strict"):
    require(audit_policy in ("strict", "record-only"), "unknown trial audit policy")
    diagnostic = audit_policy == "record-only"
    replay_policy = (config.get("protocol") == "evo-harness-offline-replay-v1"
            and config.get("audit_policy") == "record-only" and config["versions"]["skill"] == "2026.10.22.10")
    full_policy = (config.get("protocol") == "evo-harness-gdpevo-v1"
            and config.get("receipt_policy") == "native-trial-v1" and config["versions"]["skill"] in ("2026.10.22.12", "2026.10.23.1"))
    require(not diagnostic or replay_policy or full_policy,
            "record-only requires an explicitly frozen diagnostic policy")
    audit_issues = []
    def audit(condition, code, message, status="failed"):
        if condition:
            return
        if not diagnostic:
            raise ValueError(message)
        audit_issues.append({"code": code, "status": status, "message": message})
    def artifact(name):
        path = trial / "agent" / name
        if diagnostic and not path.exists():
            audit(False, "missing_receipt", "missing original receipt: " + name, "pending")
            return {}
        return read(path)
    def recorded_fields(value, expected, message):
        # Absent receipt fields are unknown; a recorded contradiction is not.
        for key, wanted in expected.items():
            if diagnostic and (key not in value or value[key] is None):
                audit(False, "missing_receipt_field", message + ": " + key, "pending")
            else:
                require(value.get(key) == wanted, message)
    inspection = evo.inspection  # Explicitly loaded from this reviewed method source.
    result = read(trial / "result.json")
    metadata = result["agent_result"]["metadata"]
    runtime = req["runtime"]
    require(result["task_name"] == launch["task_id"] and result["trial_name"] == trial.name,
            "cell binds another task/trial")
    argv = launch["argv_template"]
    expected_task = argv[argv.index("-p") + 1]
    for original in (result["task_id"]["path"], result["config"]["task"]["path"]):
        require(inspection._path(original, launch["cwd"]) == expected_task, "trial original task path differs from registered mapping")
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
    archive_template = str(Path(req["benchmark"]) / ".jobs" / "<unique-job-name>.agent-state.tar.gz")
    require(launch.get("state_archive_template") == archive_template,
            "registered State archive template differs from native attempt binding")
    require(archive.is_absolute() and archive.parent == Path(req["benchmark"]) / ".jobs"
            and str(archive) == archive_template.replace("<unique-job-name>", trial.parent.name),
            "trial did not use its own native State archive")
    info = result["agent_info"]
    require(info["name"] == "penguin" and info["model_info"] == {
        "name": runtime["model_id"], "provider": runtime["provider"]}, "actual agent identity differs")
    installed = artifact("penguin-install.json")
    recorded_fields(installed, {"agent_state": "custom", "install": "bundle",
        "bundle_sha256": config["bundle_sha256"], "node_version": config["adapter"]["node_version"],
        "penguin_version": config["adapter"]["penguin_version"]},
        "actual installed CLI/State differs from frozen bundle")
    require(result["exception_info"] is None and result["finished_at"]
            and metadata["status"] in ("completed", "timeout", "aborted"), "trial did not finish a valid scored execution")
    usage = result["agent_result"].get("model_usage")
    if usage:
        require(set(usage) == {runtime["provider"] + "/" + runtime["model_id"]},
                "actual trial model usage differs from frozen runtime")
    recorded_fields(metadata, {"provider": runtime["provider"], "model_id": runtime["model_id"],
                    "thinking": runtime["thinking_level"]}, "trial runtime differs from frozen runtime")
    require(metadata["agent_state"] == "custom" and metadata.get("requests", 1) > 0, "trial did not run the assigned custom State")
    recorded_fields(metadata, {"native_sessions_idle": True, "process_cleanup": "server_shutdown"},
                    "trial cleanup evidence incomplete")
    reward = result["verifier_result"]["rewards"]["reward"]
    evo.finite(reward, high=1)
    require(abs(cell["score"] - round(reward * 100, 2)) <= 1e-8, "cell score differs from original verifier")
    actual_cost = result["agent_result"].get("cost_usd")
    require(cell["cost"] is None if actual_cost is None else cell["cost"] is not None
            and abs(cell["cost"] - actual_cost) <= 0.000001, "cell cost differs from native trial")
    timing = result["agent_execution"]
    duration = (datetime.fromisoformat(timing["finished_at"].replace("Z", "+00:00"))
                - datetime.fromisoformat(timing["started_at"].replace("Z", "+00:00"))).total_seconds() * 1000
    require(duration >= 0 and abs(cell["duration_ms"] - duration) <= 1, "cell duration differs from original trial")
    before, ready, after = (artifact(f"penguin-agent-state-{point}.json") for point in ("before", "ready", "after"))
    expected = {name: digest(raw) for name, raw in frozen.items()}
    if not diagnostic:
        require(before["schema"] == ready["schema"] == after["schema"] == "penguin-agent-state-files-v1", "unknown State evidence schema")
        require(before["files"] == expected, "installed State differs from frozen native snapshot")
        state = before["state"]
        require(ready["state"] == after["state"] == state, "trial State path changed")
        require(ready["files"] == expected, "frozen State changed during native startup")
        require(ready["files"] == after["files"], "frozen State changed during held-out test")
    else:
        paths = []
        for point, receipt in zip(("before", "ready", "after"), (before, ready, after)):
            if receipt.get("state"):
                paths.append(receipt["state"])
            else:
                audit(False, "missing_state_path", point + " State path not recorded", "pending")
            if receipt.get("schema") != "penguin-agent-state-files-v1":
                audit(False, "unknown_state_receipt", point + " State receipt schema missing/unrecognized", "pending")
            hashes = receipt.get("files", {})
            require(isinstance(hashes, dict), "State fingerprint inventory is not an object")
            require(set(hashes) <= set(expected), "frozen State contains unexpected files")
            for name, wanted in expected.items():
                if hashes.get(name) is None:
                    audit(False, "missing_state_fingerprint", point + ": " + name, "pending")
                else:
                    require(hashes[name] == wanted, "recorded State fingerprint differs from frozen snapshot")
        require(not paths or len(set(paths)) == 1, "trial State path changed")
        state = paths[0] if paths else None
    trace_root = trial / "agent/penguin/traces"
    sessions = {}
    for path in sorted(trace_root.rglob("*.jsonl")):
        require(not path.is_symlink() and path.resolve().is_relative_to(trace_root.resolve()), "linked trace evidence")
        rows = [json.loads(line) for line in path.read_text().split("\n") if line]
        metas = [row["payload"] for row in rows if row.get("type") == "session_meta" and not row.get("origin")]
        require(len(metas) == 1, "invalid native trace metadata")
        if metas[0]["session_id"] == metadata["session_id"]:
            sessions[str(path)] = (metas[0], rows)
    audit(bool(sessions), "missing_test_trace", "missing original test Session trace", "pending")
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
    # Missing trace delivery is pending evidence. Contradictory identity or
    # duplicate tool ids above are still hard errors, never ordinary omissions.
    meta = metas[0] if metas else {"workspace": "/app"}
    fields = ("session_id", "source", "provider", "model_id", "workspace", "agent_state")
    if state is None and metas:
        state = meta["agent_state"]  # Trace path is not a replacement fingerprint.
    if state is None:
        state = "/unobserved-native-state"
    if metas:
        require(all(all(m[k] == meta[k] for k in fields) for m in metas), "test trace segment identity changed")
        require(meta["source"] in ("cli", "benchmark") and meta["provider"] == runtime["provider"]
                and meta["model_id"] == runtime["model_id"] and meta["agent_state"] == state,
                "native test trace binding mismatch")
    audit(requests > 0 and set(calls) == set(outputs), "incomplete_observation_pairs",
          "incomplete native request/action/observation pairing", "pending")
    pairs = [(call, outputs[key]) for key, call in calls.items() if key in outputs]
    slot = state + "/skills/evo-harness-learned"
    package = acceptance.package_from_snapshot(frozen, evo)
    all_files, required_files, forbidden_files = loading_files(frozen, evo, acceptance)
    skill_files = {state + "/" + name: raw for name, raw in required_files.items()}
    forbidden = {state + "/" + name for name in forbidden_files}
    try:
        reject_general_body_reads([(call, outputs.get(key, {})) for key, call in calls.items()],
                                  forbidden, meta["workspace"], inspection)
    except GeneralBodyRead as exc:
        # A real completed measurement remains collectable. It must not pass
        # the loading audit, or silently disappear from a diagnostic matrix.
        # Strict audits still raise; frozen record-only audits retain failure.
        audit(False, "general_body_read", str(exc))
    observed = {}
    automatic = config.get("context_transport") == "agents-md-v1"
    context_receipt = None
    if automatic:
        package_files = acceptance.package_files_from_state(frozen, evo)
        try:
            context_receipt = evo.context_observation(metas, package_files)
            observed = {path: -1 for path in skill_files}
        except evo.IncompleteContextObservation as exc:
            audit(False, "incomplete_skill_observation", str(exc), "pending")
    for path, raw in skill_files.items():
        if automatic:
            continue
        try:
            observed[path] = _native_read(pairs, path, raw, meta["workspace"], slot, inspection,
                                          skill_files=set(skill_files))
        except IncompleteSkillObservation as exc:
            audit(False, "incomplete_skill_observation", str(exc) + ": " + path, "pending")
    read_observed = max(observed.values()) if len(observed) == len(skill_files) else None
    # Read-only public inspection may precede the full Skill. Any business tool
    # call, answer write or nonpublic file read must follow its actual complete
    # observation, including when calls were issued concurrently.
    for call, _ in pairs:
        if read_observed is None or call["_position"] >= read_observed:
            continue
        if call.get("name") == "read_file":
            args = json.loads(call["arguments"])
            path = inspection._path(args.get("file_path"), meta["workspace"])
            require(path in skill_files or path == inspection.native_instructions(slot) or inspection._public_file(path, meta["workspace"]),
                    "nonpublic read preceded the complete Skill observation")
            continue
        if call.get("name") != "exec_command":
            audit(False, "action_before_full_observation", "task action occurred before complete Skill observation")
            continue
        try:
            inspection._readonly_commands(call, meta["workspace"], slot, skill_files=set(skill_files))
        except ValueError as exc:
            if not diagnostic:
                raise
            # An unsupported preparation command is not proof of a hidden
            # access. Explicit literal private file reads remain hard failures.
            try:
                commands, workdir, _ = inspection._shell_commands(call, meta["workspace"])
            except (ValueError, TypeError, KeyError):
                commands, workdir = [], meta["workspace"]
            for command in commands:
                argv = command["argv"]
                if argv[0] == "cd" and len(argv) == 2:
                    workdir = inspection._path(argv[1], workdir)
                elif argv[0] in ("cat", "head"):
                    for operand in inspection._file_operands(argv):
                        path = inspection._path(operand, workdir)
                        require(path in skill_files or path == inspection.native_instructions(slot)
                                or inspection._public_file(path, meta["workspace"]),
                                "nonpublic read preceded the complete Skill observation")
            audit(False, "pre_read_command_unverified", str(exc))
    loaded = {name: raw for name, raw in required_files.items() if state + "/" + name in observed}
    result = {"case_id": cell["case_id"], "run": cell["run"], "trial": str(trial),
            "job": trial.parent.name, "state_archive": str(archive),
            "installed_state_filemap_sha256": digest(evo.encoded(expected)),
            "session_id": cell["session_id"], "native_session_id": metadata["session_id"],
            "skill_sha256": package["sha256"], "skill_bytes": sum(map(len, all_files.values())),
            "loading_policy": "official-cl-context-body-general-description",
            "skill_files": {name: digest(raw) for name, raw in all_files.items()},
            "loaded_skill_files": {name: digest(raw) for name, raw in loaded.items()},
            "loaded_skill_bytes": sum(map(len, loaded.values())),
            "entries": package["entries"], "read_observed_position": read_observed,
            "file_read_observed_positions": {name.removeprefix(state + "/"): position for name, position in observed.items()},
            "trace_sha256": hashes,
            "score": round(reward * 100, 2), "cost_status": metadata.get("cost_status", "unknown"),
            "cost": actual_cost, "duration_ms": round(duration)}
    if diagnostic:
        status = "failed" if any(item["status"] == "failed" for item in audit_issues) else "pending" if audit_issues else "passed"
        result.update(audit_policy=audit_policy, audit_status=status, audit_passed=not audit_issues,
                      audit_issues=audit_issues, diagnostic_only=True,
                      required_skill_files={name: digest(raw) for name, raw in required_files.items()})
    if automatic:
        result.update(context_transport="agents-md-v1", model_context=context_receipt)
    return result


def canonical_worker_cell(req, evo, acceptance):
    """Derive a cell from one worker-bound, terminal original trial, never a plan.

    Wrong runtime/State is still rejected by _trial. Missing audit observations
    remain diagnostic; the original worker reply is preserved by the caller.
    """
    identity, launch, config = req["identity"], req["launch"], req["config_value"]
    name = req["session_id"]
    require(isinstance(name, str) and name.startswith("harbor:")
            and re.fullmatch(r"[A-Za-z0-9_.-]+", name[7:]), "original worker must bind one Harbor trial")
    jobs = Path(req["benchmark"]) / ".jobs"
    matches = list(jobs.glob("*/" + name[7:]))
    require(len(matches) == 1 and matches[0].resolve().is_relative_to(jobs.resolve())
            and not matches[0].is_symlink(), "worker Harbor trial missing or ambiguous")
    trial = matches[0]
    if not (trial / "result.json").exists():
        return {"pending": True, "reason": "original Harbor trial has not finished; await it without redispatch"}
    result = read(trial / "result.json")
    if not result.get("finished_at"):
        return {"pending": True, "reason": "original Harbor trial is still running"}
    if config["versions"]["skill"] == "2026.10.23.1":
        spec = importlib.util.spec_from_file_location("evo_worker_job", Path(__file__).with_name("worker_job.py"))
        worker_job = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker_job)
        job = worker_job.status(trial.parent)
        if job["pending"]:
            return {"pending": True, "reason": job["next_action"], "original_job": job}
    frozen = acceptance.snapshot(req["snapshot"])["files"]
    require(acceptance.config_version(frozen["system_config.yaml"])[0] == identity["expected_version"], "actual frozen version mismatch")
    reward = result["verifier_result"]["rewards"]["reward"]
    evo.finite(reward, high=1)
    timing = result["agent_execution"]
    duration = (datetime.fromisoformat(timing["finished_at"].replace("Z", "+00:00"))
                - datetime.fromisoformat(timing["started_at"].replace("Z", "+00:00"))).total_seconds() * 1000
    actual = result["agent_result"]
    cell = {"protocol_version": 1, "status": "ok", **identity, "score": round(reward * 100, 2),
            "cost": actual.get("cost_usd"), "duration_ms": round(duration), "session_id": name}
    audit = _trial(trial, cell, frozen, req, evo, acceptance, launch, config, audit_policy="record-only" if config.get("receipt_policy") == "native-trial-v1" else "strict")
    return {"pending": False, "cell": cell, "audit": audit, "trial": str(trial),
            "raw_result_sha256": digest((trial / "result.json").read_bytes())}


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
    package = acceptance.package_from_snapshot(frozen, evo)
    skill_files, required_files, _ = loading_files(frozen, evo, acceptance)
    acceptance.verify_output_budget(frozen["system_config.yaml"], package["max_bytes"])
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
        receipts.append(_trial(trials[name][0], cell, frozen, req, evo, acceptance, launch, config,
            audit_policy="record-only" if config.get("receipt_policy") == "native-trial-v1" else "strict"))
    return {"schema": "evo-injection-matrix-v3", "injection_mode": "full_skill",
            "skill_sha256": package["sha256"], "skill_bytes": sum(map(len, skill_files.values())),
            "skill_files": {name: digest(raw) for name, raw in skill_files.items()},
            "loading_policy": "official-cl-context-body-general-description",
            "loaded_skill_files": {name: digest(raw) for name, raw in required_files.items()},
            "loaded_skill_bytes": sum(map(len, required_files.values())),
            "complete": all(r.get("audit_passed", True) for r in receipts), "measurement_complete": True, "cells": receipts,
            "runtime": req["runtime"], "snapshot_sha256": digest(Path(req["snapshot"]).read_bytes()), "model_calls": 0}
