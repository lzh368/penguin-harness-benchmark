// Real compiled SDK + native Environment, synthetic content, no model call.
import fs from "node:fs/promises";
import path from "node:path";
import { execFileSync, spawn } from "node:child_process";
import { pathToFileURL, fileURLToPath } from "node:url";
if (process.argv.length !== 4)
  throw new Error(
    "usage: node test_native_runtime.mjs <built-penguin-checkout> <report.json>",
  );
const source = path.resolve(process.argv[2]);
const built = path.resolve(process.env.EVO_NATIVE_CHECKOUT || source);
const reportPath = path.resolve(process.argv[3]);
const here = path.dirname(fileURLToPath(import.meta.url));
const benchmark = path.resolve(here, "../../../..");
const core = await import(
  pathToFileURL(path.join(built, "packages/core/dist/index.js"))
);
let modelCalls = 0, networkCalls = 0, localApiCalls = 0;
const originalFetch = globalThis.fetch;
globalThis.fetch = async (url, options = {}) => {
  const target = new URL(url);
  if (!["127.0.0.1", "localhost"].includes(target.hostname) || !["GET", "PUT"].includes(options.method || "GET")) {
    networkCalls++;
    throw new Error("model-free probe forbids nonlocal or task fetch");
  }
  localApiCalls++;
  return originalFetch(url, options);
};
core.GenerativeModel.prototype.streamGenerate = function () {
  modelCalls++;
  throw new Error("model-free probe forbids model");
};
const tmp = await fs.mkdtemp("/tmp/evo-native-state-roundtrip-");
const modelConfig = {
  providers: {},
  models: [
    {
      provider: "deepseek",
      model_id: "deepseek-flash",
      context_window: 1000000,
    },
  ],
  default_model: { provider: "deepseek", model_id: "deepseek-flash" },
};
const firstHome = path.join(tmp, "original");
await core.saveProjectConfig(firstHome, "default_project", modelConfig);
await core.createAgent({
  root: firstHome,
  projectId: "default_project",
  agentId: "target",
});
const original = path.join(firstHome, "default_project/agents/target/agent_state");
for (const name of ["skills", "hooks", "memory", "schedule", ".vault.toml"])
  await fs.rm(path.join(original, name), { recursive: true, force: true });
const helper = path.join(benchmark, "agents/penguin_agent/helper.mjs");
const cli = path.join(built, "packages/cli/dist/penguin.js");
const guard = path.join(tmp, "loopback-only.mjs");
await fs.writeFile(guard, `import fs from "node:fs";
const original = globalThis.fetch;
globalThis.fetch = async (input, options = {}) => {
  const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url);
  const method = (options.method ?? input?.method ?? "GET").toUpperCase();
  if (!["localhost", "127.0.0.1", "[::1]"].includes(url.hostname) ||
      !["GET", "PUT"].includes(method) || (method === "PUT" && !url.pathname.endsWith("/config"))) {
    fs.appendFileSync(process.env.EVO_PROBE_HTTP, JSON.stringify({ blocked: true }) + "\\n");
    throw Error("nonlocal/model/task request forbidden");
  }
  const response = await original(input, options);
  fs.appendFileSync(process.env.EVO_PROBE_HTTP, JSON.stringify({ method, path: url.pathname, status: response.status }) + "\\n");
  return response;
};
`);
const apiChecks = [];
async function nativeStateApi(root, mode) {
  const httpLog = path.join(tmp, "http-" + mode + ".jsonl");
  const env = { ...process.env, PENGUIN_HOME: root, PORT: "0", HOST: "127.0.0.1",
    EVO_PROBE_HTTP: httpLog, NODE_OPTIONS: "--import " + guard };
  for (const key of Object.keys(env)) if (/^(?:HTTP|HTTPS|ALL)_PROXY$/i.test(key)) delete env[key];
  const log = await fs.open(path.join(tmp, "server-" + mode + ".log"), "w", 0o600);
  const server = spawn(process.execPath, [cli, "server"], { cwd: tmp, env, stdio: ["ignore", log.fd, log.fd] });
  await log.close();
  const invoke = (...args) => JSON.parse(execFileSync(process.execPath, [helper, ...args],
    { cwd: tmp, env, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }));
  try {
    const deadline = Date.now() + 60000;
    let ready = false;
    while (Date.now() < deadline) {
      try { ready = invoke("ready", root, String(deadline), "default_project", "target").ready; } catch {}
      if (ready) break;
      if (server.exitCode !== null) throw Error("isolated native server exited during startup");
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    if (!ready) throw Error("isolated native API did not become ready");
    if (mode === "prepare") {
      const prepared = invoke("prepare-evo-state", root, "default_project", "target", "target");
      if (!prepared.complete || prepared.max_turns !== -1 || prepared.required_output_chars !== 104192)
        throw Error("native API fresh preparation failed");
      const again = invoke("prepare-evo-state", root, "default_project", "target", "target");
      if (!again.complete || again.changed) throw Error("prepared fresh State is not stable");
      // Reproduce the old unconditional-PUT risk with a semantically identical
      // CRLF form. This disposable diagnostic precedes the actual snapshot.
      const yaml = path.join(root, "default_project/agents/target/agent_state/system_config.yaml");
      const canonical = await fs.readFile(yaml, "utf8");
      const crlf = canonical.replace(/\n/g, "\r\n");
      await fs.writeFile(yaml, crlf);
      const lock = JSON.parse(await fs.readFile(path.join(root, "server.lock"), "utf8"));
      const token = (await fs.readFile(path.join(root, "api-token"), "utf8")).trim();
      const response = await fetch(`http://localhost:${lock.port}/api/projects/default_project/agents/target/config`, {
        method: "PUT", headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
        body: JSON.stringify({ config: { maxTurns: -1 } }),
      });
      if (!response.ok || (await response.json()).config.maxTurns !== -1) throw Error("native no-op PUT reproduction failed");
      const rewritten = await fs.readFile(yaml, "utf8");
      if (rewritten === crlf || rewritten !== canonical) throw Error("native YAML normalization evidence differs");
      apiChecks.push({ mode, prepared, repeated_preparation_changed: again.changed,
        original_noop_put_rewrites_equivalent_yaml: true });
    } else {
      const beforeEvents = (await fs.readFile(httpLog, "utf8")).trim().split("\n").filter(Boolean).length;
      let cap, rejected = false;
      try { cap = invoke("set-max-turns", root, "default_project", "target", "-1"); }
      catch (error) { rejected = true; cap = JSON.parse(error.stdout.toString()); }
      if (mode.startsWith("restore") && (rejected || cap.changed || cap.action !== "unchanged")) throw Error("restored frozen turn cap was not a read-only match");
      if (mode === "mismatch" && (!rejected || cap.changed || !String(cap.error).startsWith("frozen_state_max_turns_mismatch")))
        throw Error("frozen mismatch was not refused");
      const events = (await fs.readFile(httpLog, "utf8")).trim().split("\n").filter(Boolean).map(JSON.parse);
      if (events.some(e => e.blocked) || events.slice(beforeEvents).some(e => e.method !== "GET"))
        throw Error("frozen turn-cap verification wrote State or made an out-of-scope request");
      apiChecks.push({ mode, cap, verification_get_only: true });
    }
  } finally {
    try { execFileSync(process.execPath, [cli, "server", "stop", "--root", root], { cwd: tmp, env,
      encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 15000 }); } catch {}
    if (server.exitCode === null) await Promise.race([
      new Promise(resolve => server.once("exit", resolve)), new Promise(resolve => setTimeout(resolve, 5000))]);
    if (server.exitCode === null) {
      server.kill("SIGTERM");
      await Promise.race([new Promise(resolve => server.once("exit", resolve)),
        new Promise(resolve => setTimeout(resolve, 5000))]);
    }
    if (server.exitCode === null) throw Error("isolated native server cleanup failed");
    try { await fs.access(path.join(root,"server.lock")); throw Error("native server lock remained after cleanup"); }
    catch (error) { if (error.code !== "ENOENT") throw error; }
  }
}
await nativeStateApi(firstHome, "prepare");
const evo = path.join(
  source,
  "plugins/evo-harness-rsi-toolkit/skills/evo-harness-rsi-toolkit/scripts/evo.py",
);
// Find the largest package fitting BOTH stored bytes and actual native context
// framing/metadata. No model or task material; no guessed overhead.
execFileSync("python3", ["-B", "-c", `
import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("probe_evo",sys.argv[1])
evo=importlib.util.module_from_spec(spec);spec.loader.exec_module(evo)
fixed={"id":"boundary-general","level":"general","description":"NATIVE_GENERAL_DESCRIPTION: check stated snapshot grain.","trigger":"task states current date",
       "scope":"same account snapshot","body":"NATIVE_GENERAL_BODY: preserve account and observation-date grain before joining."}
def entry(n):
    return {"id":"boundary-topic","level":"topic","topic":"synthetic","trigger":"default_project","scope":"default_project",
            "body":("x"*79+"\\n")*(n//80)+"x"*(n%80)}
low,high,best=1,16000,None
while low<=high:
    n=(low+high)//2
    try: raw=evo.render_package([entry(n),fixed],16000)
    except ValueError: high=n-1
    else: best=entry(n);low=n+1
raw=evo.render_package([best,fixed],16000)
context=evo.model_context(raw)
stored=sum(map(len,raw.values()))
assert stored<=16000 and context['injected_bytes']<=16000
try: evo.render_package([entry(high+1),fixed],16000)
except ValueError: pass
else: raise AssertionError('next larger package must exceed a frozen budget')
Path(sys.argv[2]).with_suffix('.boundary.json').write_text(json.dumps({'stored_bytes':stored,'injected_bytes':context['injected_bytes'],'max_bytes':16000,'next_byte_rejected':True}))
Path(sys.argv[2]).write_text(json.dumps([best,fixed]))
`, evo, path.join(tmp, "library.json")]);
const skill = path.join(original, "skills/evo-harness-learned");
await fs.writeFile(
  path.join(tmp, "export.json"),
  JSON.stringify({
    op: "export",
    library: path.join(tmp, "library.json"),
    limits: { general: 5, topic: 5 },
    max_bytes: 16000,
    output: skill,
  }),
);
const inventory = (dir) =>
  JSON.parse(
    execFileSync(process.execPath, [helper, "state-files", dir], {
      encoding: "utf8",
    }),
  );
await fs.writeFile(path.join(tmp,"empty-library.json"), "[]");
await fs.writeFile(path.join(tmp,"empty-export.json"), JSON.stringify({op:"export",
  library:path.join(tmp,"empty-library.json"),limits:{general:5,topic:5},max_bytes:16000,output:skill}));
execFileSync("python3", ["-B", evo, path.join(tmp,"empty-export.json")]);
const h0Before = inventory(original);
const h0Transport = path.join(tmp,"h0-state.tar.gz");
execFileSync("tar", ["-czf",h0Transport,"-C",original,"."]);
const h0Home = path.join(tmp,"baseline");
await core.saveProjectConfig(h0Home,"default_project",modelConfig);
const h0State = path.join(h0Home,"default_project/agents/target/agent_state");
await fs.mkdir(h0State,{recursive:true});
execFileSync("tar", ["-xzf",h0Transport,"-C",h0State,"--no-same-owner"]);
await nativeStateApi(h0Home,"restore_h0");
const h0After = inventory(h0State);
if (JSON.stringify(h0Before.files) !== JSON.stringify(h0After.files)) throw Error("H0 frozen State changed after native restore");
await fs.rm(skill,{recursive:true});
execFileSync("python3", ["-B", evo, path.join(tmp,"export.json")]);
// Install the canonical sibling mapping, never leave methods as Reader support files.
execFileSync("python3", ["-B", "-c", `
import importlib.util,sys,shutil
from pathlib import Path
spec=importlib.util.spec_from_file_location("probe_evo",sys.argv[1]);evo=importlib.util.module_from_spec(spec);spec.loader.exec_module(evo)
package,state=Path(sys.argv[2]),Path(sys.argv[3])
evo.validate_package(package)
files={name:(package/name).read_bytes() for name in evo.filemap(package)}
mapped=evo.package_state_files(files);shutil.rmtree(package)
for name,raw in mapped.items():
    target=state/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
`, evo, skill, original]);
const before = inventory(original);
const transport = path.join(tmp, "state.tar.gz");
execFileSync("tar", ["-czf", transport, "-C", original, "."]);
const secondHome = path.join(tmp, "installed");
await core.saveProjectConfig(secondHome, "default_project", modelConfig);
const installed = path.join(secondHome, "default_project/agents/target/agent_state");
await fs.mkdir(installed, { recursive: true });
execFileSync("tar", ["-xzf", transport, "-C", installed, "--no-same-owner"]);
const extracted = inventory(installed);
const imported = await core.createAgent({
  root: secondHome,
  projectId: "default_project",
  agentId: "target",
});
await nativeStateApi(secondHome, "restore");
const mismatchHome = path.join(tmp, "mismatch");
await core.saveProjectConfig(mismatchHome, "default_project", modelConfig);
const mismatchState = path.join(mismatchHome, "default_project/agents/target/agent_state");
await fs.mkdir(mismatchState, { recursive: true });
execFileSync("tar", ["-xzf", transport, "-C", mismatchState, "--no-same-owner"]);
const mismatchConfig = path.join(mismatchState, "system_config.yaml");
await fs.writeFile(mismatchConfig, (await fs.readFile(mismatchConfig, "utf8")).replace(/^max_turns: -1$/m, "max_turns: 32"));
const mismatchBefore = inventory(mismatchState);
await nativeStateApi(mismatchHome, "mismatch");
const mismatchAfter = inventory(mismatchState);
if (JSON.stringify(mismatchBefore.files) !== JSON.stringify(mismatchAfter.files)) throw Error("rejected frozen State was modified");
const session = await imported.createSession({
  source: "cli",
  workspaceDir: tmp,
  provider: "deepseek",
  modelId: "deepseek-flash",
  thinkingLevel: "xhigh",
  apiKey: "SYNTHETIC_UNUSED_KEY",
});
const ready = inventory(installed);
const nativeDispatch = core.buildToolConfig(imported.state).customTools.find(
  (tool) => tool.name === "run_subagent",
);
const nativePromptFileParameters =
  nativeDispatch?.parameters.properties.prompt_file?.type === "string" &&
  nativeDispatch?.parameters.properties.prompt_sha256?.type === "string" &&
  !nativeDispatch?.parameters.required.includes("prompt");
const outputs = [];
const nativeSkills = await core.listInstalledSkills(secondHome, "default_project", "target");
const skillPaths = Object.keys(before.files).filter(name => /^skills\/(?:evo-harness-learned|evo-method-[a-f0-9]+)\/SKILL\.md$/.test(name)).sort();
const readerRaw = await fs.readFile(path.join(installed, "skills/evo-harness-learned/SKILL.md"), "utf8");
const manifest = JSON.parse(execFileSync("python3", ["-B", "-c", `
import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("probe_evo",sys.argv[1]);evo=importlib.util.module_from_spec(spec);spec.loader.exec_module(evo)
print(json.dumps(evo.parse_reader(Path(sys.argv[2]).read_bytes())))
`, evo, path.join(installed, "skills/evo-harness-learned/SKILL.md")], {encoding:"utf8"}));
const requiredPaths = new Set(["skills/evo-harness-learned/SKILL.md", ...manifest.entries.filter(e=>e.load_mode==="body").map(e=>e.state_path)]);
if (!readerRaw.includes("NATIVE_GENERAL_DESCRIPTION") || readerRaw.includes("NATIVE_GENERAL_BODY")) throw Error("incorrect general Reader loading");
const discoveredNames = nativeSkills.map(skill => skill.name).sort();
const expectedNames = skillPaths.map(name => name.split("/")[1]).sort();
if (JSON.stringify(discoveredNames) !== JSON.stringify(expectedNames) || skillPaths.length !== 3)
  throw Error("native loader did not discover Reader and two independent methods");
let packageBytes = 0;
for (const relative of skillPaths) {
const fullSkill = await fs.readFile(path.join(installed, relative), "utf8");
packageBytes += Buffer.byteLength(fullSkill);
if (!requiredPaths.has(relative)) continue;
for (const name of ["read_file", "exec_command"]) {
  const config = core.buildToolConfig(imported.state);
  const env = new core.Environment({ workspaceDir: tmp, toolConfig: config });
  const messages = [];
  const args = name === "read_file"
    ? { file_path: path.join(installed, relative), limit: 2000 }
    : { cmd: "cat SKILL.md", workdir: path.dirname(path.join(installed, relative)) };
  for await (const message of env.executeTool({
    toolCall: core.toolCall({name, arguments: JSON.stringify(args), toolCallId: "full-skill-" + name}),
  })) messages.push(message);
  const output = messages.at(-1).payload.output;
  const truncated = output.includes("[output truncated:");
  let complete;
  if (name === "exec_command") complete = output === fullSkill;
  else {
    const observed = new Map(output.split("\n").flatMap(line => {
      const match = line.match(/^\s*(\d+)\t(.*)$/);
      return match ? [[Number(match[1]),match[2]]] : [];
    }));
    const lines = fullSkill.replace(/\n$/, "").split("\n");
    complete = lines.every((line, index) => observed.get(index + 1) === line);
  }
  outputs.push({ path: relative, tool: name, truncated, complete, skill_bytes: Buffer.byteLength(fullSkill), output_chars: output.length });
  env.dispose();
}
}
session.dispose();
const after = inventory(installed);
const budgetBoundary = JSON.parse(await fs.readFile(path.join(tmp, "library.boundary.json"), "utf8"));
const equals = (a, b) => JSON.stringify(a.files) === JSON.stringify(b.files);
const report = {
  synthetic_only: true,
  model_calls: modelCalls,
  network_calls: networkCalls,
  local_api_calls: localApiCalls,
  native_api_checks: apiChecks,
  frozen_mismatch_unchanged: true,
  h0_native_restore_exact: true,
  all_native_servers_stopped: true,
  native_file_count: Object.keys(before.files).length,
  archive_exact: equals(before, extracted),
  native_load_exact: equals(before, ready),
  after_exact: equals(ready, after),
  native_prompt_file_parameters: nativePromptFileParameters,
  full_skill_output: outputs,
  native_skill_names: discoveredNames,
  loading_policy: "official-cl-context-body-general-description",
  required_skill_paths: [...requiredPaths].sort(),
  package_bytes: packageBytes,
  budget_boundary: budgetBoundary,
  method_source: source,
  compiled_native_dependencies: built,
  probe_root: tmp,
};
if (
  modelCalls ||
  networkCalls ||
  !report.archive_exact ||
  !report.native_load_exact ||
  !report.after_exact ||
  !report.native_prompt_file_parameters ||
  packageBytes !== budgetBoundary.stored_bytes || packageBytes > 16000 ||
  budgetBoundary.injected_bytes > 16000 || !budgetBoundary.next_byte_rejected ||
  outputs.some(output => output.truncated || !output.complete)
)
  throw new Error(JSON.stringify(report));
await fs.writeFile(reportPath, JSON.stringify(report, null, 2) + "\n");
console.log(JSON.stringify(report));
