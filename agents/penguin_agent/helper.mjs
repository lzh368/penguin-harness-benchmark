// In-container helper for run.sh; runs on the private Node runtime, no dependencies.
//
//   node helper.mjs now
//       Prints the current time in epoch milliseconds.
//   node helper.mjs ready <data root> [deadline epoch ms] [projectId agentId]
//       Makes one authenticated loopback HTTP readiness check. Prints no token/body;
//       with a target, also requires its Project and readable Agent config/State.
//       Exits 1 for bounded startup retry, 2 for a permanent target/auth failure.
//   node helper.mjs run-status <penguin-run.json>
//       Prints "<status> <sessionId>" from `penguin run --json` output, or "error -".
//   node helper.mjs prepare-evo-wait <root> <project> <agent> <start ms> <timeout>
//       Binds guarded group training to one original absolute deadline, before run.
//   node helper.mjs evo-wait-window <plan>
//       Prints the remaining GNU hard timeout and integer CLI soft timeout.
//   node helper.mjs wait-evo <root> <project> <agent> <rootSession> <plan>
//       GET-only wait for strict Evo terminal acceptance and native idle, across
//       background notice Tasks. No receipt is never evidence of completion.
//   node helper.mjs abort <data root> <sessionId> <wait seconds>
//       Aborts the session's running Task through the local server and waits until the
//       session is idle. Prints one JSON line.
//   node helper.mjs quiesce <data root> <projectId> <rootSessionId> <wait seconds>
//       Checks all native Sessions and root background tasks in this trial's private root.
//       Aborts leftover work through native APIs within one cleanup deadline; never
//       submits messages/tasks. Exits 0 if initially idle, 5 if aborted to idle, 6 if
//       idle cannot be verified. Only native idle permits a final usage read. This
//       does not prove leaf OS processes ended; server shutdown disposes those later.
//   node helper.mjs verify-cost <session-cost.json> <model-cost.json>
//       Checks both native cost responses are JSON group lists. Does not assert that
//       every request is priced; the native hasUncosted fields remain authoritative.
//   node helper.mjs set-max-turns <data root> <projectId> <agentId> <n>
//       Sets the Agent's max_turns through the local server and reads it back. Prints one
//       JSON line; exits 1 when the value read back differs.
//   node helper.mjs redact <.project_config.toml> <dir>
//       Replaces every api_key value found in the config with "[REDACTED]" in all regular
//       files under <dir>. Prints one JSON line with counts; never prints a key.
//   node helper.mjs outcome <file> <json>
//       Writes <json> (validated) to <file>.
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

// Record bytes actually installed/read by the trial, without retaining State
// content or opening secrets. Test acceptance compares these hashes to its
// frozen snapshot; the helper itself does not judge or modify any State.
function stateFiles(root) {
  try {
    const files = {};
    const visit = (dir, prefix = "") => {
      for (const item of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
        const name = prefix + item.name;
        if (name === ".vault.toml" || name === "memory" || name === "schedule") continue;
        const full = path.join(dir, item.name);
        if (item.isSymbolicLink() || (!item.isDirectory() && !item.isFile())) throw new Error("non-regular State entry");
        if (item.isDirectory()) visit(full, name + "/");
        else files[name] = crypto.createHash("sha256").update(fs.readFileSync(full)).digest("hex");
      }
    };
    if (fs.lstatSync(root).isSymbolicLink()) throw new Error("linked State");
    visit(root);
    out({ schema: "penguin-agent-state-files-v1", state: root, files });
  } catch {
    out({ schema: "penguin-agent-state-files-v1", complete: false, error: "State inventory unavailable or contains non-regular entries" });
    process.exitCode = 1;
  }
}

function out(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Only guarded Evo group training spans multiple native Tasks. A CLI Task's
// first idle is not the terminal experiment boundary; no model/phase is started here.
function regularJson(file) {
  const stat = fs.lstatSync(file);
  if (!stat.isFile() || stat.size > 4 * 1024 * 1024 || fs.realpathSync(file) !== path.resolve(file)) throw new Error("invalid_regular_record");
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

const validNativeId = (value) => typeof value === "string" && /^[A-Za-z0-9_-]+$/.test(value);
const fileSha = (file) => crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");

function evoWaitProfile(root, projectId, agentId) {
  if (!validNativeId(projectId) || !validNativeId(agentId)) throw new Error("invalid_identity");
  const agent = path.resolve(root, projectId, "agents", agentId);
  const profilePath = path.join(agent, "agent_state/skills/evo-harness-rsi-toolkit/training.json");
  const profile = regularJson(profilePath);
  if (profile.guard_version !== 2 || profile.attempt_id !== "attempt-1" ||
      !Array.isArray(profile.train_ids) || profile.train_ids.length !== 5 ||
      !profile.train_ids.every(validNativeId) || new Set(profile.train_ids).size !== 5 ||
      !path.isAbsolute(profile.private_learning ?? "")) throw new Error("invalid_training_profile");
  const guard = path.join(agent, "agent_state/hooks/evo-harness-rsi-toolkit/guard.mjs");
  if (!fs.lstatSync(guard).isFile() || fs.realpathSync(guard) !== guard) throw new Error("missing_training_guard");
  return { agent, profilePath, profile, profileSha: fileSha(profilePath) };
}

function prepareEvoWait(root, projectId, agentId, startedMs, timeout) {
  try {
    const source = evoWaitProfile(root, projectId, agentId);
    const match = /^(\d+)s$/.exec(timeout);
    const start = Number(startedMs), seconds = match ? Number(match[1]) : NaN;
    if (!Number.isSafeInteger(start) || start <= 0 || !Number.isSafeInteger(seconds) || seconds <= 0 ||
        !Number.isSafeInteger(start + seconds * 1000)) throw new Error("bounded_training_timeout_required");
    out({ schema: "penguin-evo-native-wait-v1", root: path.resolve(root), project_id: projectId,
      agent_id: agentId, training_path: source.profilePath, training_sha256: source.profileSha,
      started_at_ms: start, deadline_ms: start + seconds * 1000 });
  } catch {
    out({ prepared: false, error: "invalid_guarded_training_wait_configuration" });
    process.exitCode = 2;
  }
}

function evoWaitWindow(planPath) {
  try {
    const plan = regularJson(planPath);
    const remaining = plan.deadline_ms - Date.now();
    if (plan.schema !== "penguin-evo-native-wait-v1" || !(remaining > 0)) throw new Error("expired");
    // GNU timeout enforces the precise remaining window, including CLI startup.
    // Round the CLI's integer-only soft window UP so it cannot end up to 999 ms
    // early; the hard window still ends at the original deadline.
    process.stdout.write(`${remaining / 1000}s ${Math.ceil(remaining / 1000)}s\n`);
  } catch { process.exitCode = 124; }
}

// An aborted auto-resumed Task may cancel its script stop hook before that hook
// writes acceptance. Only native structural events from the exact root Session
// can classify this as failure. Never infer completion from trace text.
function nativeRootAborted(agent, sessionId) {
  const traceRoot = path.join(agent, "traces");
  if (!fs.existsSync(traceRoot)) return false;
  const candidates = [];
  const pattern = new RegExp(`^${sessionId}_(\\d+)\\.jsonl$`);
  const visit = (dir, depth) => {
    if (depth > 2 || fs.lstatSync(dir).isSymbolicLink()) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) visit(path.join(dir, entry.name), depth + 1);
      else if (entry.isFile()) {
        const match = pattern.exec(entry.name);
        if (match) candidates.push({ file: path.join(dir, entry.name), segment: Number(match[1]) });
      }
    }
  };
  visit(traceRoot, 0);
  candidates.sort((a, b) => b.segment - a.segment);
  if (!candidates.length) return false;
  const fd = fs.openSync(candidates[0].file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const size = fs.fstatSync(fd).size;
    const cap = 1024 * 1024;
    const head = Buffer.alloc(Math.min(size, cap));
    fs.readSync(fd, head, 0, head.length, 0);
    const firstLine = head.toString("utf8").split("\n")[0];
    const meta = JSON.parse(firstLine);
    if (meta.type !== "session_meta" || meta.origin?.length || meta.payload?.session_id !== sessionId ||
        path.resolve(meta.payload?.agent_state ?? "") !== path.join(agent, "agent_state")) return false;
    const offset = Math.max(0, size - cap);
    const tail = Buffer.alloc(size - offset);
    fs.readSync(fd, tail, 0, tail.length, offset);
    const lines = tail.toString("utf8").split("\n");
    if (offset) lines.shift(); // A truncated first row cannot prove an event.
    let aborted = false, sawRequest = false;
    for (const line of lines) {
      if (!line.trim()) continue;
      let row;
      try { row = JSON.parse(line); } catch { return false; }
      if (row.origin?.length || row.type !== "event_msg") continue;
      if (row.payload?.type === "request_begin") { aborted = false; sawRequest = true; }
      if (row.payload?.type === "abort" && sawRequest) aborted = true;
    }
    return aborted;
  } finally { fs.closeSync(fd); }
}

async function waitEvo(root, projectId, agentId, sessionId, planPath) {
  const started = Date.now();
  const receipt = { status: "unsettled", terminal: false, native_sessions_idle: false,
    snapshots: 0, waited_ms: 0, model_calls: 0, task_requests: 0 };
  let deadline = 0;
  const check = (value) => { if (!value) throw new Error("invalid_native_wait_evidence"); };
  const remaining = () => {
    const value = deadline - Date.now();
    if (!(value > 0)) throw new Error("training_deadline");
    return Math.min(3000, value);
  };
  async function get(route) {
    const response = await apiOnce(root, "GET", route, undefined, remaining());
    check(response?.ok);
    const result = await response.json();
    remaining(); // A late response body must not certify success beyond the deadline.
    return result;
  }
  try {
    const source = evoWaitProfile(root, projectId, agentId);
    const plan = regularJson(planPath);
    check(validNativeId(sessionId) && plan.schema === "penguin-evo-native-wait-v1" &&
      plan.root === path.resolve(root) && plan.project_id === projectId && plan.agent_id === agentId &&
      plan.training_path === source.profilePath && plan.training_sha256 === source.profileSha &&
      Number.isSafeInteger(plan.deadline_ms) && plan.deadline_ms > plan.started_at_ms);
    deadline = plan.deadline_ms;
    receipt.deadline_ms = deadline;
    const scratch = path.join(source.agent, "scratchpad", sessionId);
    for (;;) {
      remaining();
      check(fileSha(source.profilePath) === source.profileSha);
      const acceptancePath = path.join(scratch, "evo-acceptance.json");
      const blockedPath = path.join(scratch, "evo-hook-blocked.json");
      let terminal = null;
      if (fs.existsSync(blockedPath)) {
        regularJson(blockedPath);
        terminal = false;
      } else if (fs.existsSync(acceptancePath)) {
        const mode = regularJson(path.join(scratch, "EVO_CONTROL.json"));
        check(mode.schema === 2 && mode.mode === "training" && mode.session_id === sessionId &&
          mode.training_path === source.profilePath && mode.training_sha256 === source.profileSha &&
          mode.private_learning === source.profile.private_learning);
        const accepted = regularJson(acceptancePath);
        if (accepted.complete === true && /^[0-9a-f]{64}$/.test(accepted.skill_sha256)) terminal = true;
        else if (accepted.complete === false && accepted.status === "method_failed") terminal = false;
        else throw new Error("invalid_terminal_acceptance");
      }
      const sessions = await nativeTrialSessions(get, projectId, sessionId);
      const idle = sessions.every((session) => session.status === "idle" && !session.queued &&
        !session.background.processes && !session.background.subagents);
      receipt.snapshots++;
      receipt.native_sessions_idle = idle;
      remaining();
      const rootSession = sessions.find((session) => session.id === sessionId);
      // A root abort does not abort its independent background children. Once
      // the root itself is idle, a structural abort is a failure boundary and
      // the existing quiesce cleanup must stop those children without delay.
      const aborted = terminal === null && rootSession.status === "idle" && !rootSession.queued &&
        nativeRootAborted(source.agent, sessionId);
      remaining(); // Synchronous trace reads are inside the same absolute window.
      if (aborted) {
        receipt.status = "method_failed"; receipt.terminal = true;
        receipt.native_abort = true; process.exitCode = 5; break;
      }
      if (terminal === false) {
        // A true failed terminal is a cleanup boundary, even if cleanup must stop
        // children from a cutoff. It never becomes a successful completion.
        receipt.status = "method_failed"; receipt.terminal = true; process.exitCode = 5; break;
      }
      if (terminal === true && idle) {
        receipt.status = "completed"; receipt.terminal = true; break;
      }
      // Missing acceptance is still pending even in the child-settle -> notice
      // delivery gap. Only the strict stop hook creates a terminal acceptance.
      await sleep(Math.min(250, remaining()));
    }
  } catch {
    const timedOut = deadline > 0 && Date.now() >= deadline;
    receipt.status = timedOut ? "timed_out" : "unsettled";
    receipt.error = timedOut ? "original_training_deadline_reached" : "could_not_verify_native_training_terminal";
    process.exitCode = timedOut ? 124 : 6;
  }
  receipt.waited_ms = Date.now() - started;
  out(receipt);
}

function runStatus(file) {
  try {
    const parsed = JSON.parse(fs.readFileSync(file, "utf8").trim().split("\n").pop());
    const status = typeof parsed.status === "string" ? parsed.status : "error";
    const sessionId = typeof parsed.sessionId === "string" ? parsed.sessionId : "-";
    process.stdout.write(`${status} ${sessionId}\n`);
  } catch {
    process.stdout.write("error -\n");
  }
}

function verifyCost(files) {
  let valid = files.length === 2;
  try {
    for (const file of files) {
      const value = JSON.parse(fs.readFileSync(file, "utf8"));
      valid &&= value !== null && typeof value === "object" && Array.isArray(value.groups) &&
        value.groups.every((group) => group !== null && typeof group === "object" && !Array.isArray(group));
    }
  } catch { valid = false; }
  out({ valid });
  if (!valid) process.exitCode = 1;
}

function verifyStop(file) {
  let valid = false;
  try { valid = JSON.parse(fs.readFileSync(file, "utf8")).ok === true; } catch { /* no receipt */ }
  out({ valid });
  if (!valid) process.exitCode = 1;
}

function readOr(file, fallback) {
  try {
    return fs.readFileSync(file, "utf8");
  } catch {
    return fallback;
  }
}

// Same addressing as the CLI: localhost (the server reserves 127.0.0.1 for previews) and
// the Bearer token the server writes to <root>/api-token at every boot. Right after the
// listener/lock appears the App may still answer 503 "penguin-server is starting".
// Read the token afresh per attempt; neither a TCP listener nor a token means HTTP ready.
async function apiOnce(root, method, apiPath, body, timeoutMs = 3000) {
  const lock = JSON.parse(readOr(path.join(root, "server.lock"), "{}"));
  const token = readOr(path.join(root, "api-token"), "").trim();
  if (!Number.isInteger(lock.port) || lock.port < 1 || lock.port > 65535 || !token) return null;
  return fetch(`http://localhost:${lock.port}${apiPath}`, {
    method,
    redirect: "error",
    signal: AbortSignal.timeout(timeoutMs),
    headers: {
      authorization: `Bearer ${token}`,
      ...(body !== undefined ? { "content-type": "application/json" } : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
}

async function isStarting(response) {
  return response.status === 503 &&
    (await response.clone().text()).trim() === "penguin-server is starting";
}

async function ready(root, deadlineMs, projectId, agentId) {
  const result = { ready: false, http_status: null };
  const targeted = projectId !== undefined || agentId !== undefined;
  const deadline = deadlineMs === undefined ? Date.now() + 1000 : Number(deadlineMs);
  if (targeted) Object.assign(result, { stage: "projects", retryable: true });
  const request = async (apiPath) => {
    const remainingMs = deadline - Date.now();
    return Number.isFinite(remainingMs) && remainingMs > 0
      ? apiOnce(root, "GET", apiPath, undefined, Math.min(1000, Math.ceil(remainingMs)))
      : null;
  };
  try {
    if (targeted && ![projectId, agentId].every((id) => typeof id === "string" && /^[A-Za-z0-9_-]+$/.test(id))) {
      result.retryable = false;
      throw new Error("invalid_readiness_target");
    }
    // /api/projects requires authentication; a public page or listening socket
    // would not prove that this root's token and the actual App are ready.
    const response = await request("/api/projects");
    if (response !== null) {
      result.http_status = response.status;
      if (!targeted) {
        result.ready = response.status === 200;
        await response.body?.cancel();
      } else if (response.status !== 200) {
        // The per-boot token can rotate between reading api-token and fetch.
        // A 401 permits another bounded read-only check with a fresh token.
        result.retryable = response.status === 401 || await isStarting(response);
        await response.body?.cancel();
      } else {
        // App listening precedes seedAdmin/provisionInitialProject completion.
        // Never use a config PUT (or a Task) to probe that initialization window.
        result.retryable = false;
        const projects = await response.json();
        if (!Array.isArray(projects?.projects) || !projects.projects.every((p) => p && typeof p.projectId === "string")) {
          throw new Error("invalid_project_inventory");
        }
        result.retryable = true;
        if (projects.projects.some((p) => p.projectId === projectId)) {
          result.stage = "agent_config";
          const config = await request(`/api/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}/config`);
          result.http_status = config?.status ?? null;
          if (config !== null) {
            if (config.status !== 200) {
              result.retryable = config.status === 401 || config.status === 404 || await isStarting(config);
              await config.body?.cancel();
            } else {
              result.retryable = false;
              const view = await config.json();
              const expected = path.resolve(root, projectId, "agents", agentId, "agent_state");
              result.ready = view?.config !== null && typeof view?.config === "object" && !Array.isArray(view.config)
                && typeof view.stateDir === "string" && path.resolve(view.stateDir) === expected;
            }
          }
        }
      }
    }
  } catch {
    // Never copy response bodies, credentials, or arbitrary transport errors to logs.
  }
  // Parsing/validating a response also consumes the original startup allowance.
  if (targeted && result.ready && Date.now() >= deadline) {
    result.ready = false;
    result.retryable = true;
  }
  out(result);
  if (!result.ready) process.exitCode = targeted && !result.retryable ? 2 : 1;
}

async function api(root, method, apiPath, body) {
  let last;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const response = await apiOnce(root, method, apiPath, body);
    if (response !== null) {
      last = response;
      const retry = last.status === 401 || await isStarting(last);
      if (!retry || attempt === 39) return last;
      await last.body?.cancel();
    }
    await sleep(500);
  }
  if (last === undefined) throw new Error("the server wrote no API token");
  return last;
}

// Sets the Agent's max_turns (system_config.yaml) through the Agent config API, which
// changes that one key and keeps the rest of the stock config, then reads it back.
// Exits non-zero unless the value read back is the one requested. Target readiness
// must precede this operation. A failed/ambiguous mutation is never retried.
async function setMaxTurns(root, projectId, agentId, value) {
  const result = { requested: Number(value) };
  const configPath = `/api/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}/config`;
  try {
    const put = await apiOnce(root, "PUT", configPath, { config: { maxTurns: Number(value) } });
    result.put_status = put?.status ?? null;
    await put?.body?.cancel();
    if (put?.ok) {
      const get = await apiOnce(root, "GET", configPath);
      result.get_status = get?.status ?? null;
      if (get?.ok) result.max_turns = (await get.json())?.config?.maxTurns ?? null;
      else await get?.body?.cancel();
    }
  } catch {
    result.error = "agent_config_update_unavailable";
  }
  out(result);
  if (result.max_turns !== result.requested) process.exitCode = 1;
}

async function abort(root, sessionId, waitSeconds) {
  const started = Date.now();
  const result = { aborted: false, status: "unknown", waited_ms: 0 };
  try {
    const res = await api(root, "POST", `/api/sessions/${encodeURIComponent(sessionId)}/abort`);
    result.aborted = res.status === 202;
    result.http_status = res.status;
    const deadline = started + Number(waitSeconds) * 1000;
    while (Date.now() < deadline) {
      const info = await api(root, "GET", `/api/sessions/${encodeURIComponent(sessionId)}`);
      if (info.ok) {
        // GET /api/sessions/:id answers { session: SessionInfo }.
        const body = await info.json();
        result.status = body?.session?.status ?? "unknown";
        if (result.status === "idle") break;
      }
      await sleep(1000);
    }
  } catch (err) {
    result.error = err instanceof Error ? err.message : String(err);
  }
  result.waited_ms = Date.now() - started;
  out(result);
}

// One shared parser for passive waiting and terminal cleanup. Native SessionService
// omits backgroundTasks for zero counts; subagent rows alone are not live state.
async function nativeTrialSessions(get, projectId, rootSessionId) {
  const check = (value) => { if (!value) throw new Error("invalid_native_session_listing"); };
  const agents = await get(`/api/projects/${encodeURIComponent(projectId)}/agents`);
  check(Array.isArray(agents.agents));
  const sessions = [];
  for (const agent of agents.agents) {
    check(validNativeId(agent.agentId));
    const listing = await get(`/api/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agent.agentId)}/sessions`);
    check(Array.isArray(listing.sessions));
    for (const session of listing.sessions) {
      check(validNativeId(session.sessionId) && session.projectId === projectId && session.agentId === agent.agentId);
      check(["idle", "running", "compacting"].includes(session.status));
      const background = session.backgroundTasks === undefined ? { processes: 0, subagents: 0 } : session.backgroundTasks;
      const queued = session.pendingFollowUpCount ?? 0;
      check(background && [background.processes, background.subagents, queued].every((n) => Number.isInteger(n) && n >= 0));
      sessions.push({ id: session.sessionId, status: session.status, background, queued });
    }
  }
  check(rootSessionId === "-" || sessions.some((session) => session.id === rootSessionId));
  check(new Set(sessions.map((session) => session.id)).size === sessions.length);
  return sessions;
}

async function quiesce(root, projectId, rootSessionId, waitSeconds) {
  const started = Date.now();
  const deadline = started + Number(waitSeconds) * 1000;
  const result = { native_sessions_idle: false, had_active: false, sessions_checked: 0, abort_requests: 0, waited_ms: 0 };
  const validId = (value) => typeof value === "string" && /^[A-Za-z0-9_-]+$/.test(value);
  const requireValue = (value) => { if (!value) throw new Error("invalid_quiescence_response"); };
  const remaining = () => {
    const ms = deadline - Date.now();
    if (!(ms > 0)) throw new Error("quiescence_deadline");
    return Math.max(1, Math.min(3000, Math.ceil(ms)));
  };
  async function request(method, route) {
    const response = await apiOnce(root, method, route, undefined, remaining());
    if (response === null || !response.ok) {
      await response?.body?.cancel();
      throw new Error("quiescence_api_failed");
    }
    return response;
  }
  async function snapshot() {
    const sessions = await nativeTrialSessions(async (route) => (await request("GET", route)).json(), projectId, rootSessionId);
    result.sessions_checked = sessions.length;
    return sessions;
  }
  async function liveState(id) {
    // Child Session list rows may say idle while their parent's runtime is executing
    // them. The native stream's initial task_state is authoritative for live children.
    const response = await request("GET", `/api/sessions/${encodeURIComponent(id)}/stream`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let text = "";
    try {
      while (text.length < 256 * 1024) {
        const chunk = await reader.read();
        if (chunk.done) break;
        text += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, "\n");
        for (let boundary; (boundary = text.indexOf("\n\n")) >= 0;) {
          const frame = text.slice(0, boundary);
          text = text.slice(boundary + 2);
          const data = frame.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
          if (!data) continue;
          const event = JSON.parse(data);
          if (event.type === "task_state") return event;
        }
      }
      throw new Error("missing_native_task_state");
    } finally { await reader.cancel(); }
  }
  const sent = new Set();
  async function stopOnce(method, route) {
    if (sent.has(route)) return;
    const response = await request(method, route);
    await response.body?.cancel();
    sent.add(route);
    result.abort_requests += 1;
  }
  try {
    requireValue(validId(projectId) && (rootSessionId === "-" || validId(rootSessionId)) && Number(waitSeconds) > 0);
    while (Date.now() < deadline) {
      const sessions = await snapshot();
      const active = sessions.filter((s) => s.status !== "idle" || s.queued || s.background.subagents || s.background.processes);
      if (active.length === 0) { result.native_sessions_idle = true; break; }
      result.had_active = true;
      for (const session of active) {
        const base = `/api/sessions/${encodeURIComponent(session.id)}`;
        if (session.background.subagents || session.queued) {
          const live = await liveState(session.id);
          for (const follow of live.pendingFollowUps ?? []) {
            requireValue(validId(follow.id));
            await stopOnce("DELETE", `${base}/follow-ups/${encodeURIComponent(follow.id)}`);
          }
          for (const child of live.subagents ?? []) {
            requireValue(validId(child.sessionId) && typeof child.running === "boolean");
            if (child.running) await stopOnce("POST", `${base}/subagents/${encodeURIComponent(child.sessionId)}/abort`);
          }
        }
        if (session.status !== "idle") await stopOnce("POST", `${base}/abort`);
        if (session.background.processes) {
          const info = await (await request("GET", `${base}/processes`)).json();
          requireValue(Array.isArray(info.processes));
          for (const process of info.processes) {
            requireValue(validId(process.processId) && typeof process.running === "boolean");
            if (process.running) await stopOnce("POST", `${base}/processes/${encodeURIComponent(process.processId)}/kill`);
          }
        }
      }
      await sleep(Math.max(0, Math.min(250, deadline - Date.now())));
    }
  } catch {
    // Unknown status remains an unpriced liability. Do not leak API bodies or tokens.
    result.error = "could_not_verify_all_trial_work_idle";
  }
  result.waited_ms = Date.now() - started;
  out(result);
  process.exitCode = !result.native_sessions_idle ? 6 : result.had_active ? 5 : 0;
}

// The config is written by PenguinAgent with basic strings only (\" \\ \uXXXX escapes), a
// subset of JSON string syntax, so JSON.parse decodes the value exactly.
function apiKeys(configFile) {
  const text = fs.readFileSync(configFile, "utf8");
  const keys = [];
  for (const match of text.matchAll(/^\s*api_key\s*=\s*"((?:[^"\\]|\\.)*)"\s*$/gm)) {
    const value = JSON.parse(`"${match[1]}"`);
    if (value.length >= 8) keys.push(value);
  }
  return keys;
}

function walk(dir, visit) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, visit);
    else if (entry.isFile()) visit(full);
  }
}

function redact(configFile, dir) {
  const result = { keys: 0, files_scanned: 0, files_redacted: 0 };
  let keys;
  try {
    keys = apiKeys(configFile).map((k) => Buffer.from(k, "utf8"));
  } catch (err) {
    result.error = `cannot read config: ${err instanceof Error ? err.message : String(err)}`;
    out(result);
    return;
  }
  result.keys = keys.length;
  const replacement = Buffer.from("[REDACTED]", "utf8");
  walk(dir, (file) => {
    let data;
    try {
      if (fs.statSync(file).size > 256 * 1024 * 1024) return;
      data = fs.readFileSync(file);
    } catch {
      return;
    }
    result.files_scanned += 1;
    let changed = false;
    for (const key of keys) {
      let at = data.indexOf(key);
      if (at === -1) continue;
      const parts = [];
      let from = 0;
      while (at !== -1) {
        parts.push(data.subarray(from, at), replacement);
        from = at + key.length;
        at = data.indexOf(key, from);
      }
      parts.push(data.subarray(from));
      data = Buffer.concat(parts);
      changed = true;
    }
    if (changed) {
      fs.writeFileSync(file, data);
      result.files_redacted += 1;
    }
  });
  out(result);
}

const [command, ...args] = process.argv.slice(2);
switch (command) {
  case "state-files":
    stateFiles(args[0]);
    break;
  case "now":
    process.stdout.write(`${Date.now()}\n`);
    break;
  case "run-status":
    runStatus(args[0]);
    break;
  case "prepare-evo-wait":
    prepareEvoWait(args[0], args[1], args[2], args[3], args[4]);
    break;
  case "evo-wait-window":
    evoWaitWindow(args[0]);
    break;
  case "wait-evo":
    await waitEvo(args[0], args[1], args[2], args[3], args[4]);
    break;
  case "ready":
    await ready(args[0], args[1], args[2], args[3]);
    break;
  case "abort":
    await abort(args[0], args[1], args[2] ?? "60");
    break;
  case "quiesce":
    await quiesce(args[0], args[1], args[2], args[3] ?? "60");
    break;
  case "verify-cost":
    verifyCost(args);
    break;
  case "verify-stop":
    verifyStop(args[0]);
    break;
  case "set-max-turns":
    await setMaxTurns(args[0], args[1], args[2], args[3]);
    break;
  case "redact":
    redact(args[0], args[1]);
    break;
  case "outcome":
    fs.writeFileSync(args[0], `${JSON.stringify(JSON.parse(args[1]), null, 2)}\n`);
    break;
  default:
    process.stderr.write(`helper.mjs: unknown command ${command}\n`);
    process.exitCode = 2;
}
