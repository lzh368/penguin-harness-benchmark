#!/usr/bin/env python3
"""Model-free preparation/validation for native Penguin-driven experiments.

No subprocess launches, model calls, next-stage decisions or retry loop.
Usage: python3 -B protocol.py REQUEST.json
"""
import sys
sys.dont_write_bytecode = True
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import tomllib


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def helper(path):
    spec = importlib.util.spec_from_file_location("evo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_config(config, repo):
    require(config["protocol"] == "evo-harness-gdpevo-v1", "unknown protocol")
    group = config["group"]
    require(group in ("003", "005", "010", "014", "018", "024"), "unsupported group")
    require(config["train_ids"] == [f"train_{i:03d}" for i in range(1, 6)], "requires all five train members")
    require(config["test_ids"] == [f"tg{group}-test-{i:03d}" for i in range(1, 6)], "requires all five tests")
    require(config["training_passes"] == config["attempts_per_task"] == config["batch_count"] == 1,
            "v1 is exactly one batch/attempt/pass")
    require(type(config["test_repeats"]) is int and config["test_repeats"] > 0, "invalid repeats")
    require(config["train_concurrency"] == 1 and type(config["test_concurrency"]) is int
            and 1 <= config["test_concurrency"] <= 8, "invalid concurrency")
    require(config["feedback"] == "train-api-score-correct" and config["empty_policy"] == "reuse-h0",
            "protocol feedback/reuse mismatch")
    root = Path(repo) / "benchmarks/gdpevo/tasks" / f"task_group_{group}"
    paths = [root / f"tg{group}-train"] + [root / "test_tasks" / i for i in config["test_ids"]]
    tasks = []
    for path in paths:
        raw = (path / "task.toml").read_bytes()
        task = tomllib.loads(raw.decode())
        split = "train" if path == paths[0] else "test"
        require(task["metadata"]["split"] == split, "wrong split")
        require(task["agent"]["network_mode"] == "no-network" and task["agent"]["user"] == "agent",
                "task isolation changed")
        timeout = task["agent"]["timeout_sec"] - 210
        require(timeout > 0, "no solving time after cleanup reserve")
        tasks.append({"path": str(path), "split": split, "task_toml_sha256": hashlib.sha256(raw).hexdigest(),
                      "solving_timeout_sec": timeout, "upstream_commit": task["metadata"]["upstream_commit"]})
    require(config["harbor_version"] == "0.23.0", "unverified Harbor version")
    return {"tasks": tasks, "train_members": config["train_ids"], "test_members": config["test_ids"],
            "model_calls": 0, "paid_authorized": config["paid_authorized"]}


def prepare_trainer(req):
    config = read(req["config"])
    checked = validate_config(config, req["repo"])
    evo = helper(req["evo_tool"])
    evo.finite(req["training_budget_usd"], low=0.01)
    evo.ident(req["host_operation_id"])
    require(req["config_sha256"] == sha(req["config"]), "frozen config hash mismatch")
    base = Path(req["base_state"])
    target = Path(req["output"])
    require(not target.exists(), "Trainer destination already exists")
    require(base.is_dir() and not base.is_symlink(), "invalid base State")
    files = evo.filemap(base)
    # Use the real initialized native Target State; never assemble a pretend CLI config.
    require("system_config.yaml" in files and "AGENTS.md" in files, "initialize native Target first")
    require(all(not p.startswith(("memory/", "schedule/")) and p != ".vault.toml" for p in files),
            "supply sanitized H0 State without memory, schedules or vault")
    require(all(not p.startswith("skills/") or p.startswith("skills/evo-harness-learned/") for p in files),
            "Trainer H0 may carry only the learned slot")
    evo.validate_package(base / "skills/evo-harness-learned")
    skill = Path(req["method_skill"])
    require(skill.is_dir() and not skill.is_symlink(), "method Skill missing")
    evo.filemap(skill)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        stage = Path(tmp) / "agent_state"
        shutil.copytree(base, stage)
        installed = stage / "skills/evo-harness-rsi-toolkit"
        shutil.copytree(skill, installed)
        training = {"train_ids": config["train_ids"], "attempt_id": "attempt-1",
                    "limits": {"general": 5, "topic": 5}, "max_entries": 5, "max_bytes": 16000,
                    "runtime": config["runtime"], "workspace": "/app",
                    "training_budget_usd": req["training_budget_usd"],
                    "host_operation_id": req["host_operation_id"], "config_sha256": req["config_sha256"],
                    "compilation_reserve_sec": config["compilation_reserve_sec"],
                    "solving_timeout_sec": checked["tasks"][0]["solving_timeout_sec"]}
        require(training["compilation_reserve_sec"] < training["solving_timeout_sec"], "invalid compilation reserve")
        dump(installed / "training.json", training)
        with (stage / "AGENTS.md").open("a") as handle:
            handle.write("\nFor a continuous group training instruction, if run_subagent is available, read "
                         "skills/evo-harness-rsi-toolkit/references/group-training.md and complete only its "
                         "Container training root delegation before returning. A child without run_subagent "
                         "follows its assigned Solver, Reflector or Evolver role; do not recurse.\n")
        stage.rename(target)
    return {"state": str(target), "files": evo.filemap(target), "training": training}


def pack_state(req):
    evo = helper(req["evo_tool"])
    root = Path(req["state"])
    files = evo.filemap(root)
    require(not Path(req["output"]).exists(), "archive already exists")
    with tarfile.open(req["output"], "w:gz") as archive:
        for name in files:
            if name == ".vault.toml" or name.startswith(("memory/", "schedule/")):
                continue
            archive.add(root / name, arcname=name, recursive=False)
    return {"sha256": sha(req["output"]), "archive": req["output"]}


def verify_export(req):
    evo = helper(req["evo_tool"])
    submission = Path(req["submission"])
    record = read(req["training_export"])
    require(record["complete"] is True, "training export incomplete")
    require(record["runtime"] == req["runtime"], "training runtime changed")
    require(set(record["members"]) == set(req["train_ids"]), "incomplete training members")
    private = Path(req["private_learning"])
    contexts = []
    def private_file(name):
        path = private / name
        require(not path.is_symlink() and path.resolve().is_relative_to(private.resolve()), "private path escape")
        return path
    # Original container paths map only to exported trace files. Callers cannot
    # substitute unrelated native sessions via a global trace search.
    trace_root = Path(req["trace_root"])
    available = {}
    for path in trace_root.rglob("*.jsonl"):
        require(path.name not in available, "ambiguous exported trace")
        available[path.name] = str(path)
    path_map = {}
    def bind_path(original, target):
        if original in path_map:
            require(Path(path_map[original]).resolve() == Path(target).resolve(), "evidence path mapping changed")
        path_map[original] = str(target)
    for original in record["trace_paths"]:
        require(Path(original).name in available, "exported trace missing")
        bind_path(original, available[Path(original).name])
    for task in req["train_ids"]:
        evo.ident(task)
        path = submission / "answers" / f"{task}.json"
        require(sha(path) == record["members"][task]["answer_sha256"], "answer changed after feedback")
        require(isinstance(read(path), dict), "answer is not an object")
        member = record["members"][task]
        context_path = private_file(member["context_file"])
        require(sha(context_path) == member["context_sha256"], "context hash mismatch")
        context = read(context_path)
        require(context["task_id"] == task and context["attempt_id"] == member["attempt_id"], "member binding mismatch")
        require(context["answer_sha256"] == sha(path), "context/answer mismatch")
        require(context["trace"]["binding"]["session_id"] == member["solver_session_id"], "Solver mismatch")
        require(hashlib.sha256(evo.encoded(context["feedback"])).hexdigest() == member["feedback_sha256"], "feedback mismatch")
        contexts.append(context)
        bind_path(member["container_context_path"], context_path)
    evo.validate_contexts(contexts, req["train_ids"], path_map)
    candidate_path = private_file(record["candidates_file"])
    decisions_path = private_file(record["decisions_file"])
    library_path = private_file(record["library_file"])
    require(sha(library_path) == record["library_sha256"], "pre-batch library changed")
    bind_path(record["container_library_path"], library_path)
    bind_path(record["container_candidates_path"], candidate_path)
    require(sha(candidate_path) == record["candidates_sha256"] and sha(decisions_path) == record["decisions_sha256"],
            "learning output changed")
    evo.validate_candidates(read(candidate_path), {(c["task_id"], c["attempt_id"]): c for c in contexts})
    audits = record["roles"]
    require(sum(a["role"] == "evolver" for a in audits) == 1, "requires one Evolver")
    require({a["task_id"] for a in audits if a["role"] == "reflector"} ==
            {c["task_id"] for c in contexts if c["negative"]}, "Reflector coverage mismatch")
    reflected, sessions = [], {c["trace"]["binding"]["session_id"] for c in contexts}
    for audit in audits:
        require(audit["role"] in ("reflector", "evolver"), "invalid role")
        require(audit["binding"]["session_id"] not in sessions, "learning roles require fresh sessions")
        sessions.add(audit["binding"]["session_id"])
        require(set(audit["materials"]) == set(audit["material_files"]), "material mapping differs")
        require(set(audit["outputs"]) == set(audit["output_files"]), "output mapping differs")
        for original, relative in audit["material_files"].items():
            bind_path(original, private_file(relative))
        for original, relative in audit["output_files"].items():
            bind_path(original, private_file(relative))
        materials = list(audit["material_files"])
        require(len(audit["outputs"]) == 1, "one role output required")
        if audit["role"] == "reflector":
            member = record["members"][audit["task_id"]]
            require(member["container_context_path"] in materials, "Reflector did not receive bound context")
            proposals = read(path_map[audit["outputs"][0]])
            require(all(c["task_id"] == audit["task_id"] for c in proposals), "Reflector output crosses task boundary")
            reflected.extend(proposals)
        else:
            require(record["container_candidates_path"] in materials and record["container_library_path"] in materials,
                    "Evolver did not receive batch and library")
            require({m["container_context_path"] for m in record["members"].values()} <= set(materials),
                    "Evolver did not read complete batch evidence")
            require(sha(path_map[audit["outputs"][0]]) == sha(decisions_path), "Evolver decisions differ")
        require(audit["role_text_path"] in materials, "role instructions not read")
        evo.audit_role({"traces": audit["traces"], "binding": audit["binding"], "materials": materials,
                        "outputs": audit["outputs"], "path_map": path_map})
    require(reflected == read(candidate_path), "candidate concatenation differs from Reflector outputs")
    for binding in [c["trace"]["binding"] for c in contexts] + [a["binding"] for a in audits]:
        require(all(binding[k] == req["runtime"][k] for k in ("provider", "model_id")), "frozen runtime mismatch")
    evo.verify_compilation({"train_ids": req["train_ids"], "contexts": [str(private_file(record["members"][t]["context_file"])) for t in req["train_ids"]],
        "candidates": str(candidate_path), "decisions": str(decisions_path), "library": str(library_path),
        "limits": record["limits"], "max_entries": record["max_entries"], "max_bytes": record["max_bytes"],
        "skill": str(submission / "skill"), "path_map": path_map})
    package = evo.validate_package(submission / "skill")
    require(package["sha256"] == record["skill_sha256"], "export fingerprint mismatch")
    return package


def matrix(req):
    """Validate native worker cells; return shared scoreboard shape only when complete."""
    evo = helper(req["evo_tool"])
    case_ids, repeats = req["case_ids"], req["runs"]
    require(case_ids and len(case_ids) == len(set(case_ids)), "invalid case roster")
    require(type(repeats) is int and repeats > 0, "invalid run count")
    cells = {}
    sessions = set()
    for cell in read(req["cells"]):
        require(cell["protocol_version"] == 1, "unknown cell protocol")
        key = (cell["case_id"], cell["run"])
        require(key not in cells, "duplicate cell; reconcile same worker instead of selecting retry")
        require(key[0] in case_ids and type(key[1]) is int and 1 <= key[1] <= repeats, "foreign cell")
        for field, expected in req["identity"].items():
            require(cell.get(field) == expected, f"cell runtime/state mismatch: {field}")
        if cell["status"] == "ok":
            evo.finite(cell["score"], high=100)
            evo.finite(cell["duration_ms"])
            if cell["cost"] is not None:
                evo.finite(cell["cost"])
            require(cell["session_id"], "missing bound session")
            require(cell["session_id"] not in sessions, "test cells require fresh sessions")
            sessions.add(cell["session_id"])
        else:
            require(cell["status"] == "failed" and "score" not in cell, "failed cell cannot carry score")
        cells[key] = cell
    missing = [{"case_id": c, "run": n} for c in case_ids for n in range(1, repeats + 1)
               if (c, n) not in cells or cells[(c, n)]["status"] != "ok"]
    if missing:
        return {"complete": False, "missing": missing, "score": None, "evaluation": None}
    def mean(values):
        return sum(values) / len(values)
    def cost_mean(values):
        known = [v for v in values if v is not None]
        return round(mean(known), 6) if known else None
    cases = []
    for c in case_ids:
        rows = [cells[(c, n)] for n in range(1, repeats + 1)]
        cases.append({"case": c, "score": round(mean([v["score"] for v in rows]), 2),
                      "cost": cost_mean([v["cost"] for v in rows]),
                      "duration_ms": round(mean([v["duration_ms"] for v in rows])),
                      "runs": [{k: v[k] for k in ("score", "cost", "duration_ms", "session_id")} for v in rows]})
    ident = req["identity"]
    evaluation = {"time": req["time"], "agent_id": ident["agent_id"], "version": ident["expected_version"],
                  "provider": ident["provider"], "model_id": ident["model_id"], "thinking_level": ident["thinking_level"],
                  "summary_title": req["summary_title"], "summary": req["summary"],
                  "score": round(mean([v["score"] for v in cases]), 2),
                  "cost": cost_mean([v["cost"] for v in cases]),
                  "duration_ms": round(mean([v["duration_ms"] for v in cases])), "cases": cases}
    return {"complete": True, "missing": [], "evaluation": evaluation,
            "cost_complete": all(v["cost"] is not None for v in cells.values())}


def execute(req):
    operations = {"preflight": lambda r: validate_config(read(r["config"]), r["repo"]),
                  "prepare-trainer": prepare_trainer, "pack-state": pack_state,
                  "verify-export": verify_export, "matrix": matrix}
    return operations[req["op"]](req)


if __name__ == "__main__":
    try:
        require(len(sys.argv) == 2, "usage: python3 -B protocol.py REQUEST.json")
        print(json.dumps(execute(read(sys.argv[1])), ensure_ascii=False, allow_nan=False))
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(2)
