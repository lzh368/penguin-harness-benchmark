#!/usr/bin/env python3
"""One-operation historical replay acceptance; no model or process dispatch.

Historical training evidence is validated by the original-version source proof
and the Penguin replay helper. This module registers/tests only one final State;
it never pretends to execute training, original-Solver reflection or a baseline.
"""
import sys
sys.dont_write_bytecode = True
import importlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("evo_replay_protocol", HERE / "protocol.py")
protocol = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(protocol)
registration = protocol.helper(HERE / "registration.py")
acceptance = protocol.helper(HERE / "acceptance.py")
injection = protocol.helper(HERE / "injection.py")
require, read, sha = protocol.require, protocol.read, protocol.sha
PROTOCOL = "evo-harness-offline-replay-v1"
METHOD_SCRIPTS = "plugins/evo-harness-rsi-toolkit/skills/evo-harness-rsi-toolkit/scripts"


def validate_replay_config(config, repo):
    require(config["protocol"] == PROTOCOL, "explicit offline replay protocol required")
    require(type(config["test_repeats"]) is int and config["test_repeats"] == 1
            and config["test_phase_order"] == ["final_test"], "replay authorizes one final-only test round")
    require(type(config["test_concurrency"]) is int and config["test_concurrency"] == 5,
            "replay requires five concurrent final tests")
    require(config["limits"] == {"general": 5, "topic": 5} and type(config["max_bytes"]) is int
            and config["max_bytes"] == 16000, "replay learning limits changed")
    require(config["paid_authorized"] is True and type(config["budget_usd"]) in (int, float)
            and 0 < config["budget_usd"] <= 10, "replay requires explicit per-run budget authorization")
    require(config["versions"]["skill"] in ("2026.10.22.9", "2026.10.22.10"), "wrong replay method version")
    require(config.get("audit_policy", "strict") in ("strict", "record-only"), "unknown replay audit policy")
    require(config.get("audit_policy") != "record-only" or config["versions"]["skill"] == "2026.10.22.10",
            "record-only cannot retroactively alter the old replay method")
    require(re.fullmatch(r"[0-9a-f]{64}", config["source_manifest_sha256"]) is not None
            and sha(config["source_manifest"]) == config["source_manifest_sha256"], "frozen source manifest changed")
    target_id(config)
    # This ephemeral projection reuses only public task metadata/isolation
    # checks. It is never saved, registered as training or returned as evidence
    # that any old full-protocol phase ran.
    projected = {**config, "protocol": "evo-harness-gdpevo-v1", "training_passes": 1,
        "attempts_per_task": 1, "batch_count": 1, "train_concurrency": 1,
        "test_phase_order": ["train_compile_freeze", "h0_test", "final_test"],
        "feedback": "train-api-score-correct", "empty_policy": "reuse-h0"}
    checked = protocol.validate_config(projected, repo)
    return {**checked, "protocol": PROTOCOL, "new_training_calls": 0, "new_baseline_calls": 0,
            "test_repeats": 1, "test_phase_order": ["final_test"]}


def target_id(config):
    value = config["test_agent_id"].removeprefix("create:").strip()
    require(re.fullmatch(r"[A-Za-z0-9_-]+", value), "invalid native replay Target id")
    return value


def base(req):
    config = read(req["config"])
    require(sha(req["config"]) == req["config_sha256"], "frozen replay config hash mismatch")
    validate_replay_config(config, config["deployment"]["benchmark_checkout"])
    return config


def method(req, config):
    tool = Path(req.get("evo_tool") or Path(config["deployment"]["penguin_checkout"]) / METHOD_SCRIPTS / "evo.py").resolve()
    require(tool.is_file() and tool.name == "evo.py", "replay method helper missing")
    sys.path.insert(0, str(tool.parent))
    try:
        # Native invocation already imports these exact modules. Standalone
        # verification imports the same pinned helper directory, not cwd code.
        for name in ("evo", "control", "replay"):
            existing = sys.modules.get(name)
            require(existing is None or Path(existing.__file__).resolve() == tool.with_name(name + ".py"),
                    "mixed replay helper versions in one process")
            importlib.import_module(name)
        return sys.modules["evo"], sys.modules["replay"]
    finally:
        sys.path.pop(0)


def benchmarks(req, config):
    deploy = config["deployment"]
    request = {**req, "repo": req.get("repo", deploy["benchmark_checkout"]),
        "output": req.get("output", str(Path(deploy["penguin_home"]) / deploy["project_id"] / "benchmarks"))}
    _, checked, output = registration._inputs(
        request, validate_replay_config, preflight_scope="offline-replay-test-only")
    benchmark_id, files = registration._files(config, checked, "test")
    old = ("The training group contains five sequential members in one trial. Test cases\n"
           "are independent; the main Penguin launches five native workers concurrently\n"
           "within one frozen State phase, finishes the matrix, then advances itself.")
    new = ("This offline replay registers final testing only: five independent native\n"
           "workers run once, concurrently, against the same frozen Skill State.\n"
           "No new training run, baseline or additional test round is authorized.")
    files = {name: value.replace(old, new) if name.endswith("statement/README.md") else value
             for name, value in files.items()}
    directory = output / benchmark_id
    if directory.exists() or directory.is_symlink():
        registration._verify(directory, files)
    else:
        require(req["op"] == "prepare-benchmarks", "native replay Benchmark missing")
        with tempfile.TemporaryDirectory(prefix=".evo-replay-registration-", dir=output) as temporary:
            stage = Path(temporary) / benchmark_id
            for name, content in files.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            (stage / "scoreboard.yaml").write_text("evaluations: []\n")
            require(not directory.exists(), "Benchmark created concurrently; reconcile first")
            stage.rename(directory)
        registration._verify(directory, files)
    roster = registration.case_roster(config)["test"]
    return {"schema": "evo-offline-replay-benchmarks-v1", "protocol": PROTOCOL, "complete": True,
        "config_sha256": req["config_sha256"], "runtime": config["runtime"], "model_calls": 0,
        "new_training_calls": 0, "new_baseline_calls": 0,
        "test": {"benchmark_id": benchmark_id, "case_ids": list(roster), "case_to_task": roster,
                 "files": {name: acceptance.sha(value.encode()) for name, value in files.items()}}}


def freeze(req, config):
    evo, replay = method(req, config)
    source = replay.verify_source(config)
    learned = replay.verify_learning_seal(config, req["learning_seal"])
    require(source["complete"] is True and learned["complete"] is True
            and learned["source_manifest_sha256"] == config["source_manifest_sha256"], "replay learning seal incomplete")
    package = acceptance.verify_skill_budget(evo.validate_package(learned["package_path"]))
    require(package["sha256"] == learned["package_sha256"], "compiled replay package changed")
    registered = benchmarks({**req, "op": "verify-benchmarks"}, config)
    initial, final = acceptance.snapshot(req["initial_snapshot"]), acceptance.snapshot(req["final_snapshot"])
    first_version, first_config = acceptance.config_version(initial["files"]["system_config.yaml"])
    last_version, last_config = acceptance.config_version(final["files"]["system_config.yaml"])
    initial_package = acceptance.package_from_snapshot(initial["files"], evo)
    final_package = acceptance.package_from_snapshot(final["files"], evo)
    require(initial_package["empty"] is True, "replay initial State must contain an empty learned package")
    require(not any((name.startswith("skills/") and not acceptance.learned_state_file(name))
                    or name.startswith("hooks/") for name in initial["files"]),
            "initial replay Target cannot contain method hooks or foreign Skills")
    require(final_package == package, "frozen replay package differs from sealed compilation")
    def outside(files):
        return {name: raw for name, raw in files.items()
                if name != "system_config.yaml" and not acceptance.learned_state_file(name)}
    require(first_config == last_config and outside(initial["files"]) == outside(final["files"]),
            "replay State changed outside learned Skill paths and version")
    equivalent = initial_package == package
    require((first_version == last_version and initial["files"] == final["files"]) if equivalent
            else last_version > first_version, "replay final version does not match package change")
    for state, version in ((initial, first_version), (final, last_version)):
        acceptance.verify_output_budget(state["files"]["system_config.yaml"], config["max_bytes"])
        state["filemap"] = {name: acceptance.sha(raw) for name, raw in sorted(state.pop("files").items())}
        state["filemap_sha256"] = acceptance.sha(evo.encoded(state["filemap"]))
        state["version"] = version
    deploy = config["deployment"]
    expected_target = Path(deploy["penguin_home"]) / deploy["project_id"] / "agents" / target_id(config) / "agent_state"
    require(Path(req["target_state"]) == expected_target and expected_target.resolve() == expected_target,
            "replay freeze names another native Target State")
    require(acceptance.transported_filemap(expected_target, evo) == final["filemap"],
            "live replay Target differs from frozen State")
    result = {"schema": "evo-offline-replay-freeze-v1", "protocol": PROTOCOL, "complete": True,
        "config_sha256": req["config_sha256"], "source_manifest_sha256": config["source_manifest_sha256"],
        "learning_seal_sha256": sha(req["learning_seal"]), "frozen_package_sha256": package["sha256"],
        "equivalent_initial": equivalent, "initial": initial, "final": final,
        "test": registered["test"], "test_case_ids": registered["test"]["case_ids"],
        "runtime": config["runtime"], "test_repeats": 1, "model_calls": 0}
    if config.get("audit_policy") == "record-only":
        result.update({key: learned[key] for key in ("audit_passed", "audit_status", "audit_records")})
        result.update(audit_policy="record-only", diagnostic_only=True,
                      complete_means="frozen artifacts collected; not a validity certification")
    return result


def round_matrix(req, config):
    require(type(req["round"]) is int and req["round"] == 1, "only final test round 1 is authorized")
    freeze_request = read(req["freeze_request"])
    require(freeze_request["op"] == "verify-freeze" and freeze_request["config"] == req["config"]
            and freeze_request["config_sha256"] == req["config_sha256"], "round belongs to another frozen configuration")
    frozen = freeze(freeze_request, config)
    evo, _ = method(freeze_request, config)
    identities = {"agent_id": target_id(config), "expected_version": frozen["final"]["version"],
                  **{k: config["runtime"][k] for k in ("provider", "model_id", "thinking_level")}}
    cells = read(req["cells"])
    result = protocol.matrix({"evo_tool": freeze_request["evo_tool"], "cells": req["cells"],
        "case_ids": frozen["test_case_ids"], "runs": 1, "identity": identities,
        "time": req.get("time", "original-trial-bound"), "summary_title": "Offline replay final test",
        "summary": "One final-only test round; historical training source, no new baseline."})
    if not result["complete"]:
        return {"protocol": PROTOCOL, "complete": False, "round": 1, "cells": cells,
                "matrix": result, "model_calls": 0}
    deploy = config["deployment"]
    benchmark = Path(deploy["penguin_home"]) / deploy["project_id"] / "benchmarks" / config["test_benchmark_id"]
    jobs = benchmark / ".jobs"
    require(jobs.is_dir() and not jobs.is_symlink(), "native replay Benchmark jobs missing")
    trials = {}
    for original in jobs.glob("*/*/result.json"):
        trial = original.parent
        require(trial.resolve().is_relative_to(jobs.resolve()) and not trial.is_symlink(), "trial path escape")
        trials.setdefault(trial.name, []).append(trial)
    snapshot = acceptance.snapshot(freeze_request["final_snapshot"])["files"]
    receipts = []
    for cell in cells:
        ident = cell["session_id"]
        require(isinstance(ident, str) and ident.startswith("harbor:"), "replay cell lacks a Harbor trial")
        name = ident.removeprefix("harbor:")
        require(re.fullmatch(r"[A-Za-z0-9_.-]+", name) and len(trials.get(name, [])) == 1,
                "replay original trial missing or ambiguous")
        launch = read(benchmark / cell["case_id"] / "statement/harbor-launch.json")
        require(launch["task_id"] == frozen["test"]["case_to_task"][cell["case_id"]], "replay task mapping changed")
        receipts.append(injection._trial(trials[name][0], cell, snapshot,
            {"runtime": config["runtime"], "benchmark": str(benchmark)}, evo, acceptance, launch, config,
            audit_policy=config.get("audit_policy", "strict")))
    require(len({row["native_session_id"] for row in receipts}) == 5, "replay tests must use five fresh native Sessions")
    known = round(sum(row["cost"] for row in cells if row["cost"] is not None), 6)
    pending = [row["case_id"] for row in cells if row["cost"] is None]
    result = {"schema": "evo-offline-replay-test-matrix-v1", "protocol": PROTOCOL, "complete": True,
        "round": 1, "test_repeats": 1, "test": frozen["test"], "final": frozen["final"],
        "config_sha256": req["config_sha256"], "source_manifest_sha256": config["source_manifest_sha256"],
        "freeze_request_sha256": sha(req["freeze_request"]), "cells_sha256": sha(req["cells"]),
        "frozen_package_sha256": frozen["frozen_package_sha256"], "cells": cells,
        "matrix": result, "trials": receipts, "costs": {"known_test_cost_usd": known,
        "pending_test_cells": pending, "cost_complete": not pending,
        "scope": "Target trials only; main, learning and worker fees remain in native replay budget ledger"},
        "model_calls": 0, "new_training_calls": 0, "new_baseline_calls": 0}
    if config.get("audit_policy") == "record-only":
        statuses = [frozen["audit_status"], *(row["audit_status"] for row in receipts)]
        status = "failed" if "failed" in statuses else "pending" if "pending" in statuses else "passed"
        audit_record = {"schema": "evo-replay-measurement-audit-v1", "config_sha256": req["config_sha256"],
            "source_manifest_sha256": config["source_manifest_sha256"], "audit_policy": "record-only",
            "audit_status": status, "learning_audit_records": frozen["audit_records"],
            "trials": [{k: row[k] for k in ("case_id", "session_id", "trial", "audit_status", "audit_issues", "trace_sha256")}
                       for row in receipts], "diagnostic_only": True}
        raw = evo.encoded(audit_record)
        path = Path(req["freeze_request"]).parent / "trial-audits" / (acceptance.sha(raw) + ".json")
        if path.exists():
            require(path.is_file() and not path.is_symlink() and path.read_bytes() == raw, "replay audit record changed")
        else:
            evo.write_once(path, raw)
        result.update(audit_policy="record-only", audit_status=status, audit_passed=status == "passed",
            audit_records=[*frozen["audit_records"], str(path)], diagnostic_only=True,
            complete_means="five real measurements collected; not a validity certification")
    return result


def measurement_record(req, config):
    """Preserve original measured numbers before any diagnostic audit can fail.

    This is an inventory, not acceptance: actual task/model fields are retained
    so a mismatched trial can never be silently relabeled as the expected case.
    """
    deploy = config["deployment"]
    jobs = Path(deploy["penguin_home"]) / deploy["project_id"] / "benchmarks" / config["test_benchmark_id"] / ".jobs"
    measurements, scored = [], {}
    for path in sorted(jobs.glob("*/*/result.json")):
        require(path.resolve().is_relative_to(jobs.resolve()) and not path.is_symlink(), "diagnostic trial path escape")
        record = {"result_path": str(path), "result_sha256": sha(path)}
        try:
            original = read(path)
            value = (original.get("verifier_result") or {}).get("rewards", {}).get("reward")
            valid = type(value) in (int, float) and 0 <= value <= 1
            task = original.get("task_name")
            record.update(task_name=task, trial_name=original.get("trial_name"),
                score=round(value * 100, 2) if valid else None, reward=value if valid else None,
                finished_at=original.get("finished_at"), exception_info=original.get("exception_info"),
                model_name=(original.get("config") or {}).get("agent", {}).get("model_name"),
                cost_usd=(original.get("agent_result") or {}).get("cost_usd"),
                acceptance="not_certified")
            if valid and original.get("finished_at"):
                scored[task] = scored.get(task, 0) + 1
        except (ValueError, KeyError, TypeError) as exc:
            record.update(score=None, evidence_error=type(exc).__name__)
        measurements.append(record)
    value = {"schema": "evo-replay-original-measurements-v1", "config_sha256": req["config_sha256"],
        "audit_policy": "record-only", "diagnostic_only": True, "measurements": measurements,
        "duplicate_scored_tasks": sorted(task for task, count in scored.items() if count > 1),
        "note": "Original trial measurements only; not an audit pass, task attribution or experiment validity certificate."}
    raw = registration.encoded(value).encode()
    path = Path(req["freeze_request"]).parent / "trial-audits" / (acceptance.sha(raw) + ".measurements.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        require(path.is_file() and not path.is_symlink() and path.read_bytes() == raw, "original measurements record changed")
    else:
        with path.open("xb") as stream:
            stream.write(raw)
    return str(path), value


def execute(req):
    config = base(req)
    if req["op"] == "verify-source":
        _, replay = method(req, config)
        return replay.verify_source(config)
    if req["op"] in ("prepare-benchmarks", "verify-benchmarks"):
        return benchmarks(req, config)
    if req["op"] == "verify-freeze":
        return freeze(req, config)
    if req["op"] in ("verify-round", "verify-test-matrix"):
        if config.get("audit_policy") != "record-only":
            return round_matrix(req, config)
        path, measurements = measurement_record(req, config)
        try:
            require(not measurements["duplicate_scored_tasks"], "duplicate actual scored trials in one replay round")
            result = round_matrix(req, config)
        except (ValueError, KeyError, OSError, TypeError) as exc:
            # Record and stop; this branch never converts a hard failure into
            # successful acceptance or invents a score from worker prose.
            result = {"protocol": PROTOCOL, "complete": False, "hard_failure": True,
                "audit_policy": "record-only", "audit_passed": False, "audit_status": "failed",
                "audit_error": str(exc), "diagnostic_only": True, "model_calls": 0}
        result["measurement_record"] = path
        return result
    raise ValueError("unsupported offline replay operation")


if __name__ == "__main__":
    try:
        require(len(sys.argv) == 2, "usage: python3 -B offline_replay.py REQUEST.json")
        print(json.dumps(execute(read(sys.argv[1])), ensure_ascii=False, allow_nan=False))
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
