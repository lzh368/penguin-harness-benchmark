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


def _shell_commands(call, workspace):
    """Parse a deliberately small literal shell grammar, never execute it.

    Quotes/escapes are handled by shlex. Expansion, background execution,
    subshells, and arbitrary redirections are outside this evidence interface.
    The returned commands still need their program/operand allowlist checked.
    """
    require(call.get("name") == "exec_command", "not a shell observation")
    args = json.loads(call["arguments"])
    require(not args.get("run_in_background"), "background inspection is not a foreground read")
    command = args.get("cmd", args.get("command", ""))
    require(isinstance(command, str) and command and len(command) <= 32768
            and not any(c in command for c in "\n\r\0$`{}()"), "unsupported inspection shell syntax")
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = list(lexer)
    require(tokens and len(tokens) <= 512, "empty/oversized inspection command")
    # Retain just enough quote provenance for find -name '*.json'. A second
    # non-POSIX lex preserves whole quoted words. Only an unambiguous, fully
    # quoted token at the same position may carry wildcard characters; mixed
    # quoted/unquoted concatenation is deliberately outside this grammar.
    quoted_lexer = shlex.shlex(command, posix=False, punctuation_chars=";&|<>")
    quoted_lexer.whitespace_split = True
    quoted_lexer.commenters = ""
    quoted_tokens = list(quoted_lexer)
    # shlex removes quoting. Requiring the actual literal fd spelling prevents
    # accepting `2 > /dev/null` or `"2">/dev/null` as stderr redirection.
    stderr_count = len(re.findall(r"(?<![^\s;&|])2>\s*(?:/dev/null|'/dev/null'|\"/dev/null\")(?=$|[\s;&|])", command))
    commands, argv, patterns, previous, redirects = [], [], [], None, 0
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == ">":
            require(argv and argv[-1] == "2" and i + 1 < len(tokens) and tokens[i + 1] == "/dev/null",
                    "only literal stderr redirection to /dev/null is allowed")
            argv.pop(); redirects += 1; i += 2
            continue
        if token in (";", "&&", "||", "|"):
            require(argv, "empty inspection command")
            commands.append({"argv": argv, "connector": previous, "quoted_patterns": patterns})
            argv, patterns, previous = [], [], token
        else:
            require(not re.fullmatch(r"[;&|<>]+", token)
                    and not any(c in token for c in ";&|<>~"), "unsupported inspection operator/expansion")
            if any(c in token for c in "*?[]"):
                original = quoted_tokens[i] if len(quoted_tokens) == len(tokens) else ""
                require(len(original) >= 2 and original[0] in "'\"" and original[-1] == original[0]
                        and shlex.split(original) == [token], "unquoted inspection expansion")
                patterns.append(len(argv))
            argv.append(token)
        i += 1
    require(argv or previous == ";", "unfinished inspection command")
    if argv:
        commands.append({"argv": argv, "connector": previous, "quoted_patterns": patterns})
    require(redirects == stderr_count, "stderr redirection must use literal 2>/dev/null")
    return commands, _path(args.get("workdir", workspace), workspace), redirects


def _file_operands(argv):
    """Operands of cat/head; no stdin pseudo-file or option that names a file."""
    name, rest = argv[0], argv[1:]
    if rest and rest[0] == "--":
        rest = rest[1:]
    elif name == "head":
        while rest and rest[0].startswith("-"):
            option = rest.pop(0)
            if option == "--":
                break
            if option in ("-n", "-c", "--lines", "--bytes"):
                require(rest and re.fullmatch(r"[0-9]+", rest[0]), "head needs a literal nonnegative bound")
                rest.pop(0)
            else:
                require(re.fullmatch(r"-(?:[nc])?[0-9]+|--(?:lines|bytes)=[0-9]+|-[qv]", option),
                        "unsupported head option")
    require(all(value != "-" and not value.startswith("-") for value in rest), "unsupported read operand")
    return rest


def _public_file(path, workspace):
    root = workspace.rstrip("/")
    return path in (root + "/instruction.md", root + "/environment_access.md") or path.startswith(root + "/input/")


def _readonly_commands(call, workspace, slot):
    """Allow only public-file reads, fixed reader/index reads and discovery.

    Pipes can feed a bounded stdin head; every branch of &&/||/; must itself be
    read-only. This is an evidence grammar, not a general shell sandbox.
    """
    commands, workdir, redirects = _shell_commands(call, workspace)
    reads = []
    fixed = {slot + "/SKILL.md", slot + "/index.json"}
    for item in commands:
        argv = item["argv"]
        program = argv[0]
        require(program in ("pwd", "ls", "find", "cat", "head", "echo"), "only literal read-only inspection may precede selection")
        require(all(program == "find" and index >= 2 and argv[index - 1] in ("-name", "-iname")
                    for index in item["quoted_patterns"]), "quoted patterns are only find name predicates")
        if program in ("cat", "head"):
            operands = _file_operands(argv)
            require(operands or program == "head" and item["connector"] == "|", "read needs a literal file")
            paths = [_path(value, workdir) for value in operands]
            require(all(path in fixed or _public_file(path, workspace) for path in paths),
                    "inspection may not read entry bodies or nonpublic files")
            reads.extend(paths)
        elif program == "pwd":
            require(all(value in ("-L", "-P") for value in argv[1:]), "unsupported pwd operand")
            operands = []
        elif program == "echo":
            operands = []  # Literal strings cannot prove a file read.
        elif program == "ls":
            operands = []
            options = True
            for value in argv[1:]:
                if options and value == "--":
                    options = False
                elif options and value.startswith("-"):
                    require(re.fullmatch(r"-[aAlhRFd1tSrp]+|--(?:all|almost-all|recursive|directory|color=(?:never|auto|always))", value),
                            "unsupported ls option")
                else:
                    operands.append(value)
        else:
            operands, tail = [], list(argv[1:])
            while tail and not tail[0].startswith("-"):
                operands.append(tail.pop(0))
            while tail:
                option = tail.pop(0)
                if option in ("-type", "-maxdepth", "-mindepth", "-name", "-iname"):
                    require(tail, "missing find predicate")
                    value = tail.pop(0)
                    require((option == "-type" and value in ("f", "d", "l"))
                            or (option in ("-maxdepth", "-mindepth") and re.fullmatch(r"[0-9]+", value))
                            or (option in ("-name", "-iname") and not value.startswith("-")), "unsupported find predicate")
                else:
                    require(option in ("-print", "-print0"), "find action may not precede selection")
        # Directory/name discovery exposes no entry bodies. Preserve ordinary
        # pwd/ls/find discovery outside /app, including the native skills root.
        if item["connector"] == "|":
            require(program == "head" and not operands, "inspection pipes only feed stdin head")
    return commands, workdir, redirects, reads


def _native_read(pairs, path, raw, workspace, slot):
    lines, observed = raw.decode().split("\n"), {}
    if lines[-1] == "":
        lines.pop()
    last = -1
    # Parallel tool calls can finish out of call order. Coverage exists only
    # after every required page's original observation has actually arrived.
    for pair in sorted(pairs, key=lambda value: value[1]["_position"]):
        call = pair[0]
        observation = pair[1]
        if observation.get("stop_reason") != "completed" or call["_position"] >= observation["_position"]:
            continue
        if call.get("name") == "exec_command":
            try:
                commands, workdir, redirects, _ = _readonly_commands(call, workspace, slot)
            except (ValueError, TypeError, KeyError):
                continue
            argv = commands[0]["argv"]
            # Exact stdout from one resolved cat operand is a full observation.
            # Combined commands, redirection, pipelines and head cannot supply
            # this proof even when their visible text happens to resemble it.
            if (len(commands) == 1 and not redirects and argv[0] == "cat"
                    and [_path(value, workdir) for value in _file_operands(argv)] == [path]
                    and observation["output"].encode() == raw):
                last = observation["_position"] if last < 0 else min(last, observation["_position"])
            continue
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
    require(last >= 0, "frozen reader/index not fully observed through native read_file or exact foreground cat")
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


def _inspection_segments(command):
    """Split literal top-level command spans without interpreting their code.

    Keep original bytes/quoting for each span. In particular, semicolons inside
    Python or echo strings must not create fictitious cat/head commands. Shell
    grouping/substitutions stay opaque; only independent top-level spans are
    candidates for the existing small literal parser.
    """
    if not isinstance(command, str) or len(command) > 32768:
        return []
    segments, start, quote, depth, escaped, connector, comment = [], 0, None, 0, False, "", False
    for index, char in enumerate(command):
        if comment:
            if char == "\n":
                comment, start, connector = False, index + 1, "\n"
            continue
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
        elif char in "({":
            depth += 1
        elif char in ")}":
            depth = max(0, depth - 1)
        elif not depth and char == "#" and (index == 0 or command[index - 1].isspace()):
            # Comments end at the next newline. Post-selection task commands
            # can be multiline even though pre-selection inspection is not.
            segments.append((command[start:index], connector))
            comment = True
        elif not depth and char in ";&|\n":
            span = command[start:index]
            if span.strip():
                segments.append((span, connector))
                connector = char
            else:
                connector += char
            start = index + 1
    if not quote and not depth and not escaped and not comment:
        segments.append((command[start:], connector))
    return [(value, join) for value, join in segments if value.strip()]


def _check_entry_reads(call, workspace, slot, entry_paths, selected, selector_observed):
    """No explicit read of a frozen body can evade the selector's budget.

    Apply to native reads and explicit literal cat/head operands, including
    otherwise unsupported flags and mixed post-selection commands. This is a
    cooperative trace audit, not a sandbox for arbitrary task programs: path
    text in Python, JSON or shell strings does not establish a file read.
    """
    args = json.loads(call["arguments"])
    paths = []
    if call.get("name") == "read_file":
        paths = [_path(args.get("file_path"), workspace)]
    elif call.get("name") == "exec_command":
        workdir, pending_cd = _path(args.get("workdir", workspace), workspace), None
        for segment, connector in _inspection_segments(args.get("cmd", args.get("command", ""))):
            if pending_cd is not None and connector == "&&":
                workdir = pending_cd  # The following command requires cd success.
            pending_cd = None
            try:
                lexer = shlex.shlex(segment, posix=True, punctuation_chars="<>")
                lexer.whitespace_split = True
                argv = list(lexer)
            except ValueError:
                continue
            if not argv:
                continue
            if argv[0] == "cd":
                operand = argv[1:] if argv[1:2] != ["--"] else argv[2:]
                if len(operand) == 1 and not any(c in operand[0] for c in "$`*?[]~"):
                    try:
                        pending_cd = _path(operand[0], workdir)
                    except ValueError:
                        pass
                continue
            if argv[0] not in ("cat", "head", "/bin/cat", "/usr/bin/cat", "/bin/head", "/usr/bin/head"):
                continue
            # Do not parse the unrelated program in another span or emulate
            # expansions. Exact operands remain observable with other flags,
            # redirections or ordinary /bin and /usr/bin utility aliases.
            for value in argv[1:]:
                if value.startswith("-"):
                    continue
                try:
                    paths.append(_path(value, workdir))
                except ValueError:
                    continue
    for path in paths:
        if path in entry_paths:
            require(call["_position"] > selector_observed and entry_paths[path] in selected,
                    "unselected or premature entry read bypasses injection budget")


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
    reader_read = _native_read(pairs, slot + "/SKILL.md", frozen[acceptance.SLOT + "/SKILL.md"], meta["workspace"], slot)
    index_read = _native_read(pairs, slot + "/index.json", frozen[acceptance.SLOT + "/index.json"], meta["workspace"], slot)
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
        _readonly_commands(call, meta["workspace"], slot)
    index = json.loads(frozen[acceptance.SLOT + "/index.json"])
    entries = {entry["id"]: entry for entry in index["entries"]}
    require(len(selected) == len(set(selected)) and len(selected) <= index["max_entries"]
            and all(key in entries for key in selected), "selector entry budget/identity mismatch")
    text = "\n\n".join(frozen[acceptance.SLOT + "/" + entries[key]["file"]].decode() for key in selected)
    receipt = {"selected": selected, "entry_hashes": {key: entries[key]["sha256"] for key in selected},
               "text": text, "bytes": len(text.encode()), "sha256": digest(text.encode())}
    require(receipt["bytes"] <= index["max_bytes"] and evo.contains_json(observation["output"], receipt),
            "selected guidance was not fully observed or exceeds frozen budget")
    entry_paths = {slot + "/" + entry["file"]: key for key, entry in entries.items()}
    for i, (call, _) in enumerate(pairs):
        if any(attempt[0] == i for attempt in attempts):
            continue
        _check_entry_reads(call, meta["workspace"], slot, entry_paths, selected, selector_observed)
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
