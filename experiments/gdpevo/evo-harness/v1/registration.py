"""Deterministic native Benchmark files; no model, process or phase dispatch."""
import hashlib
import json
from pathlib import Path
import re
import shlex
import tarfile
import tempfile


# The source bundle records every installed dependency file, so its JSON
# inventory can exceed 4 MiB even when the compressed bundle is about 100 MiB.
# Bound the uncompressed manifest independently of the payload archive size.
MAX_BUNDLE_MANIFEST_BYTES = 64 * 1024 * 1024


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def case_roster(config):
    """Native Case ids are not Harbor task ids (native readers require CASE-)."""
    train = f"tg{config['group']}-train"
    return {"training": {f"CASE-001-{train}": train},
            "test": {f"CASE-{i:03d}-{task}": task for i, task in enumerate(config["test_ids"], 1)}}


def _id(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]+", value), "invalid native id")
    return value


def _absolute(value):
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts, "require canonical absolute paths")
    require(path == path.resolve(), "symlink/noncanonical registration path")
    return path


def _bundle_manifest(path):
    """Read only the already hash-bound manifest, with a fixed memory bound."""
    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            if member.name != "penguin-bench/bundle.json":
                continue
            require(member.isfile() and member.size > 0, "invalid source bundle manifest")
            require(member.size <= MAX_BUNDLE_MANIFEST_BYTES,
                    f"source bundle manifest exceeds {MAX_BUNDLE_MANIFEST_BYTES} bytes")
            with archive.extractfile(member) as stream:
                blob = stream.read(member.size + 1)
            require(len(blob) == member.size, "source bundle manifest length mismatch")
            manifest = json.loads(blob)
            require(isinstance(manifest, dict), "invalid source bundle manifest object")
            return manifest
    raise ValueError("missing source bundle manifest")


def _inputs(req, validate_config, *, preflight_scope="full-rsi"):
    config = read(req["config"])
    require(sha(req["config"]) == req["config_sha256"], "frozen config hash mismatch")
    checked = validate_config(config, req["repo"])
    deploy, adapter, runtime = config["deployment"], config["adapter"], config["runtime"]
    repo = _absolute(req["repo"])
    require(repo == _absolute(deploy["benchmark_checkout"]), "wrong benchmark checkout")
    for key in ("provider", "model_id", "thinking_level", "api_host"):
        require(isinstance(runtime.get(key), str) and runtime[key], f"missing frozen runtime {key}")
    require(runtime["thinking_level"] in ("low", "medium", "high", "xhigh", "max"), "invalid thinking level")
    require(config["api_host"] == runtime["api_host"], "frozen API host mismatch")
    require(adapter["allowed_agent_hosts"] == [runtime["api_host"]], "only frozen model host is allowed")
    require(adapter["agent"] == "penguin_agent:PenguinAgent", "wrong native Harbor adapter")
    require(_absolute(adapter["python_path"]) == repo / "agents", "wrong adapter source")
    require(adapter["penguin_version"] == config["penguin_version"], "Penguin version mismatch")
    require(re.fullmatch(r"\d+\.\d+\.\d+", adapter["node_version"]), "missing/invalid pinned Node version")
    require(adapter["run_timeout"] == "task" and adapter["max_turns"] == -1, "task limits changed")
    require(adapter["host_penguin_home"] == deploy["penguin_home"]
            and adapter["host_project_id"] == deploy["project_id"], "wrong host configuration source")
    _id(deploy["project_id"])
    for field in ("benchmark_id", "test_benchmark_id"):
        _id(config[field])
    require(config["benchmark_id"] != config["test_benchmark_id"], "train/test Benchmark ids collide")
    output = _absolute(req["output"])
    require(output == _absolute(deploy["penguin_home"]) / deploy["project_id"] / "benchmarks",
            "registration must use frozen native Project benchmarks directory")
    require(output.is_dir(), "initialize the native Project first")
    require(_absolute(adapter["install_bundle"]) == _absolute(deploy["bundle"]), "bundle path mismatch")
    for path, expected in ((deploy["bundle"], config["bundle_sha256"]),
                           (deploy["image_manifest"], config["image_manifest_sha256"]),
                           (deploy["preflight_receipt"], deploy["preflight_receipt_sha256"])):
        require(sha(_absolute(path)) == expected, "frozen deployment artifact changed")
    require(deploy["bundle_sha256"] == config["bundle_sha256"]
            and deploy["image_manifest_sha256"] == config["image_manifest_sha256"], "deployment pins disagree")
    for field in ("penguin_commit", "benchmark_commit"):
        require(re.fullmatch(r"[0-9a-f]{40}", config["versions"][field]), "source must be a full commit")
    # Full payload/duplicate validation belongs to source_bundle.py and the
    # frozen preflight. The archive SHA above binds that exact verified input;
    # this read only needs its manifest, never a getmembers() payload scan.
    bundle = _bundle_manifest(deploy["bundle"])
    require(bundle["schema"] == "evo-harness-source-bundle-v1"
            and bundle["source_commit"] == config["versions"]["penguin_commit"]
            and bundle["penguin_version"] == adapter["penguin_version"]
            and bundle["node_version"] == adapter["node_version"], "source bundle/adapter runtime pins differ")
    require(re.fullmatch(r"\d{4}\.\d{2}\.\d{2}\.[1-9]\d*", config["versions"]["skill"]), "invalid method version")
    preflight = read(deploy["preflight_receipt"])
    require(preflight["passed"] is True and preflight["model_calls"] == 0
            and preflight["test_concurrency"] == 5 and preflight["five_test_phases_overlap"] is True,
            "matching isolation preflight has not passed")
    expected_pins = {key: config["versions"][key] for key in ("penguin_commit", "benchmark_commit")}
    expected_pins.update({key: config[key] for key in ("bundle_sha256", "image_manifest_sha256")})
    expected_pins.update({key: runtime[key] for key in ("provider", "model_id", "api_host")})
    require(preflight["pins"] == expected_pins, "preflight pins differ from frozen config")
    # The final-only replay probes five test environments; the full RSI
    # protocol still requires its training environment plus all five tests.
    # Scope is fixed by the caller, never inferred from an incomplete roster.
    if preflight_scope == "offline-replay-test-only":
        require(config["protocol"] == "evo-harness-offline-replay-v1"
                and preflight.get("schema") == "evo-offline-replay-deployment-preflight-v1",
                "offline replay requires its explicit test-only preflight scope")
        expected_tasks = config["test_ids"]
    else:
        require(preflight_scope == "full-rsi" and config["protocol"] == "evo-harness-gdpevo-v1",
                "full RSI preflight scope differs from protocol")
        expected_tasks = [f"tg{config['group']}-train", *config["test_ids"]]
    require(preflight["task_ids"] == expected_tasks, "preflight roster differs")
    return config, checked, output


def _files(config, checked, phase):
    """Like native builtin-benchmarks.ts: public launch, private 100-point rubric."""
    deploy, adapter, runtime = config["deployment"], config["adapter"], config["runtime"]
    benchmark_id = config["benchmark_id" if phase == "training" else "test_benchmark_id"]
    commit = config["versions"]["benchmark_commit"]
    repository = config.get("benchmark_repository", "https://github.com/Prism-Shadow/penguin-harness-benchmark")
    link = f"{repository}/blob/{commit}/README.md#rules-for-agents"
    # TOML JSON strings are compatible basic strings; fixed fields carry no private inputs.
    files = {"benchmark_config.toml": (
        f"id = {json.dumps(benchmark_id)}\ntitle = {json.dumps('Evo-Harness GDPevo ' + config['group'] + ' ' + phase)}\n"
        f"version = {json.dumps(config['versions']['skill'])}\nstatus = \"published\"\nruns = {1 if phase == 'training' else config['test_repeats']}\n\n[origin]\nkind = \"agent\"\n")}
    package = None
    if config.get("benchmark_layout") == "gdpevo-domain-packages-v1":
        package = Path(deploy["benchmark_checkout"]) / "packages" / benchmark_id
        source_commit = config["versions"]["benchmark_source_commit"]
        require(source_commit == "73457bde7376f42de49d384c603933fe18d97000", "unreviewed split package source")
        source_url = "https://github.com/Saigyouji-Yuyuko1000/penguin-harness-benchmark/tree/" + source_commit + "/packages/" + benchmark_id
        files["benchmark_config.toml"] = (package / "benchmark_config.toml").read_text().split("[origin]", 1)[0] + (
            "[origin]\nkind = \"import\"\nurl = " + json.dumps(source_url) + "\ncommit = " + json.dumps(source_commit)
            + "\npath = " + json.dumps("packages/" + benchmark_id) + "\n")
    tasks = checked["tasks"][:1] if phase == "training" else checked["tasks"][1:]
    for (case_id, task_id), task in zip(case_roster(config)[phase].items(), tasks, strict=True):
        argv = [deploy["uvx"], "--from", "harbor==" + config["harbor_version"], "harbor", "run",
                "-p", task["path"], "-a", adapter["agent"], "-m", runtime["provider"] + "/" + runtime["model_id"]]
        options = {"thinking": runtime["thinking_level"], "penguin_version": adapter["penguin_version"],
                   "node_version": adapter["node_version"], "run_timeout": adapter["run_timeout"],
                   "max_turns": adapter["max_turns"], "install_bundle": adapter["install_bundle"],
                   "host_penguin_home": adapter["host_penguin_home"], "host_project_id": adapter["host_project_id"],
                   "agent_state_tar": "<absolute-state-archive>"}
        for key, value in options.items():
            argv.extend(["--ak", f"{key}={value}"])
        argv.extend(["--allow-agent-host", runtime["api_host"], "--agent-setup-timeout-multiplier", "2.5",
                     "-k", "1", "-n", "1", "--job-name", "<unique-job-name>", "-o", "<benchmark-jobs-dir>", "-y"])
        launch = {"schema": "evo-harbor-launch-v1", "task_id": task_id, "case_id": case_id,
                  "cwd": deploy["benchmark_checkout"], "environment": {"PYTHONPATH": adapter["python_path"],
                  "PATH_prefix": deploy["bin_dir"]}, "argv_template": argv, "runtime": runtime,
                  "state_archive_template": str(Path(deploy["penguin_home"]) / deploy["project_id"] /
                      "benchmarks" / benchmark_id / ".jobs" / "<unique-job-name>.agent-state.tar.gz"),
                  "bundle_sha256": config["bundle_sha256"], "penguin_commit": config["versions"]["penguin_commit"],
                  "wait_command": ["python3", "-B", str(Path(deploy["benchmark_checkout"]) /
                      "experiments/gdpevo/evo-harness/v1/worker_job.py"), str(Path(deploy["penguin_home"]) /
                      deploy["project_id"] / "benchmarks" / benchmark_id / ".jobs" / "<unique-job-name>")],
                  "task_toml_sha256": task["task_toml_sha256"], "solving_timeout_sec": task["solving_timeout_sec"]}
        if package is not None:
            original_statement = package / case_id / "statement/README.md"
            original_rubric = package / case_id / "rubric/README.md"
            launch["source_package"] = {"id": benchmark_id, "commit": source_commit,
                "statement_sha256": sha(original_statement), "rubric_sha256": sha(original_rubric),
                "manifest_sha256": sha(package / "benchmark_config.toml"),
                "adaptation": "resolve frozen runtime, State archive and checkout; task/grader unchanged"}
            files[f"{case_id}/statement/package-source.md"] = original_statement.read_text()
        statement = f"""# Evo-Harness GDPevo {config['group']} / {task_id}

Native case `{case_id}` maps to unmodified Harbor task `{task_id}`. The task's
original instruction, services, answer format and verifier remain authoritative.

## How this case is run

The evaluation worker launches Harbor {config['harbor_version']} from the frozen
benchmark checkout `{deploy['benchmark_checkout']}` at commit `{commit}`, following
[Rules for agents]({link}) and the native shared evaluation protocol.
The adjacent `harbor-launch.json` fixes the complete adapter options and runtime.
Replace only the three placeholders for the State archive, unique job name and
this Benchmark's `.jobs` directory. Pack the assigned native Target/Trainer State
as instructed by evaluation.md; never run a stock Agent or omit the archive.
Use the exact case-sensitive `test_agent_id` from the native worker request and
its `STATE = App Data Dir/agents/<test_agent_id>/agent_state` path from evaluation.md.
Do not lowercase or hand-invent another Agent directory; confirm that exact
`STATE/system_config.yaml` exists before packing it. For each launch attempt,
choose its unused job name first, then substitute that exact case-sensitive name
in `harbor-launch.json`'s `state_archive_template`; use that exact value for both
`tar -czf` and `agent_state_tar`, preserving case. Confirm the archive is a regular
file before Harbor starts. Do not normalize either path, derive it from a returned
trial-directory name, or rename a job after launch. A retry authorized by the
unchanged native pre-start policy needs a new job and its own matching archive,
packed from the same frozen State. Retain the earlier command, failure and
no-Target-start evidence; do not reuse the previous attempt's archive path.
Run the exact Harbor argv in the foreground of native `exec_command`; do not
use shell `&` or `nohup` (native command cleanup reaps background children).
If the native call yields a `process_id`, keep that handle and use
`input_command` until the original process actually ends. A yielded call is not
a completed Harbor job. The launch JSON's `wait_command` is a read-only entry:
substitute the same job name to inspect/wait for that original job's terminal
`result.json` and trial records. It never launches or retries. If the process
has ended but the job is incomplete, preserve the pending records and report
that condition; do not clean the environment or dispatch another Target.
Inspect existing verifier evidence before deciding whether anything is missing;
a cleanup exception alone does not authorize another answer or score.
The launch already includes all `--ak` options, including Node and bundle pins;
do not insert duplicates or use adapter defaults. Confirm the model-host helper
agrees with the frozen allowed host. Credentials stay in private configuration.

```bash
export PATH={shlex.quote(deploy['bin_dir'])}:"$PATH"
export PYTHONPATH={shlex.quote(adapter['python_path'])}
{shlex.join(argv)}
```

Solving cap: {task['solving_timeout_sec']} seconds, plus the unchanged task's
210-second abort/cleanup reserve; `run_timeout=task`, `max_turns=-1`.
The training group contains five sequential members in one trial. Test cases
are independent; the main Penguin launches five native workers concurrently
within one frozen State phase, finishes the matrix, then advances itself.
This statement dispatches nothing. No task/grader modifications are permitted.

Score = original Harbor verifier reward × 100. Missing verifier results or
zero-model install/config failures are unscored engineering failures, never
manufactured zero scores. A valid reward remains valid when cost is unknown:
keep `cost: null` and the pending budget reservation rather than rerunning.
Incomplete cleanup blocks method completion without deleting original scores.
Keep original failures;
only the native evaluation retry policy may authorize a new pre-start attempt.
"""
        files[f"{case_id}/statement/README.md"] = statement
        files[f"{case_id}/statement/harbor-launch.json"] = encoded(launch)
        files[f"{case_id}/rubric/README.md"] = (
            "# Scoring rubric (max 100 points)\n\n"
            "- 100 pts: the original Harbor verifier reward multiplied by 100. "
            "Use only its numeric reward; there is no manual judging or replacement scoring rule. "
            "Keep missing/invalid infrastructure results unscored. Never send test scoring "
            "details, hidden inputs or rubric contents to a Solver or any learning role.\n")
        if package is not None:
            files[f"{case_id}/rubric/README.md"] = original_rubric.read_text()
            files[f"{case_id}/statement/README.md"] = (
                "Imported split package: `" + benchmark_id + "` at `" + source_commit + "`.\n"
                "The original public statement is preserved in package-source.md. Its generic launch "
                "examples are resolved by the frozen execution binding below. The experiment uses "
                "learning method2026.10.23.6: five sequential train members once, one actual judge "
                "per member and one batch compilation; repeated training allowed by the general "
                "benchmark is not enabled. User-frozen model, limits and concurrency govern this run.\n\n"
                + files[f"{case_id}/statement/README.md"])
    return benchmark_id, files


def _verify(directory, files):
    require(directory.is_dir() and not directory.is_symlink(), "native Benchmark missing or linked")
    expected_cases = {name.split("/")[0] for name in files if name.startswith("CASE-")}
    actual_cases = {p.name for p in directory.iterdir() if p.name.startswith("CASE-")}
    require(actual_cases == expected_cases, "native Case roster differs")
    for name, content in files.items():
        path = directory / name
        require(path.resolve().is_relative_to(directory.resolve()) and not path.is_symlink(), "linked Benchmark material")
        require(path.is_file() and path.read_text() == content, f"frozen Benchmark material differs: {name}")
    for case in expected_cases:
        actual = {p.relative_to(directory).as_posix() for p in (directory / case).rglob("*") if p.is_file()}
        require(actual == {name for name in files if name.startswith(case + "/")}, "extra native Case material")


def execute(req, validate_config):
    config, checked, output = _inputs(req, validate_config)
    planned = {phase: _files(config, checked, phase) for phase in ("training", "test")}
    # Validate every existing directory before making any change; never overwrite
    # manual drafts, scoreboards or earlier experiments on a name collision.
    for benchmark_id, files in planned.values():
        target = output / benchmark_id
        if target.exists() or target.is_symlink():
            _verify(target, files)
        elif req["op"] == "verify-benchmarks":
            raise ValueError("native Benchmark missing")
    result = {"schema": "evo-native-benchmarks-v1", "config_sha256": req["config_sha256"],
              "runtime": config["runtime"], "model_calls": 0}
    for phase, (benchmark_id, files) in planned.items():
        target = output / benchmark_id
        if not target.exists():
            with tempfile.TemporaryDirectory(prefix=".evo-registration-", dir=output) as temporary:
                stage = Path(temporary) / benchmark_id
                for name, content in files.items():
                    path = stage / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content)
                (stage / "scoreboard.yaml").write_text("evaluations: []\n")
                require(not target.exists(), "Benchmark created concurrently; reconcile first")
                stage.rename(target)
        _verify(target, files)
        result[phase] = {"benchmark_id": benchmark_id, "case_ids": list(case_roster(config)[phase]),
                         "case_to_task": case_roster(config)[phase], "files": {
                             name: hashlib.sha256(content.encode()).hexdigest() for name, content in files.items()}}
    return result
