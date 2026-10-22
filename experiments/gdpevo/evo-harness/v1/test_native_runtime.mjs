// Real compiled SDK + native Environment, synthetic content, no model call.
import fs from "node:fs/promises";
import path from "node:path";
import { execFileSync } from "node:child_process";
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
const { parseDocument } = await import(
  pathToFileURL(
    path.join(built, "packages/server/node_modules/yaml/dist/index.js"),
  )
);
let modelCalls = 0,
  networkCalls = 0;
globalThis.fetch = async () => {
  networkCalls++;
  throw new Error("model-free probe forbids fetch");
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
await core.saveProjectConfig(firstHome, "synthetic", modelConfig);
await core.createAgent({
  root: firstHome,
  projectId: "synthetic",
  agentId: "target",
});
const original = path.join(firstHome, "synthetic/agents/target/agent_state");
for (const name of ["skills", "hooks", "memory", "schedule", ".vault.toml"])
  await fs.rm(path.join(original, name), { recursive: true, force: true });
const configPath = path.join(original, "system_config.yaml");
const doc = parseDocument(await fs.readFile(configPath, "utf8"));
doc.setIn(["memory", "enabled"], false);
doc.setIn(["hooks", "enabled"], false);
await fs.writeFile(configPath, doc.toString());
const stateGuide = await fs.readFile(
  path.join(
    source,
    "plugins/evo-harness-rsi-toolkit/skills/evo-harness-rsi-toolkit/references/state.md",
  ),
  "utf8",
);
const documented = stateGuide.match(/```python\n([\s\S]*?)\n```/);
if (!documented) throw new Error("native State budget setup snippet missing");
execFileSync("python3", ["-", configPath], {
  input:
    "import sys\n" +
    documented[1].replace(
      'Path("<STATE>/system_config.yaml")',
      "Path(sys.argv[1])",
    ),
});
const evo = path.join(
  source,
  "plugins/evo-harness-rsi-toolkit/skills/evo-harness-rsi-toolkit/scripts/evo.py",
);
// Build exactly 16000 total bytes, including Reader/metadata, without relying
// on a guessed framing size or overlong lines. No model or task material.
execFileSync("python3", ["-B", "-c", `
import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("probe_evo",sys.argv[1])
evo=importlib.util.module_from_spec(spec);spec.loader.exec_module(evo)
def entry(n):
    return {"id":"boundary-guide","level":"general","trigger":"synthetic","scope":"synthetic",
            "body":("x"*79+"\\n")*(n//80)+"x"*(n%80)}
low,high,best=1,16000,None
while low<=high:
    n=(low+high)//2
    try: raw=evo.render_skill([entry(n)],16000)
    except ValueError: high=n-1
    else: best=entry(n);low=n+1
assert len(evo.render_skill([best],16000))==16000
Path(sys.argv[2]).write_text(json.dumps([best]))
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
execFileSync("python3", ["-B", evo, path.join(tmp, "export.json")]);
const helper = path.join(benchmark, "agents/penguin_agent/helper.mjs");
const inventory = (dir) =>
  JSON.parse(
    execFileSync(process.execPath, [helper, "state-files", dir], {
      encoding: "utf8",
    }),
  );
const before = inventory(original);
const transport = path.join(tmp, "state.tar.gz");
execFileSync("tar", ["-czf", transport, "-C", original, "."]);
const secondHome = path.join(tmp, "installed");
await core.saveProjectConfig(secondHome, "synthetic", modelConfig);
const installed = path.join(secondHome, "synthetic/agents/target/agent_state");
await fs.mkdir(installed, { recursive: true });
execFileSync("tar", ["-xzf", transport, "-C", installed, "--no-same-owner"]);
const extracted = inventory(installed);
const imported = await core.createAgent({
  root: secondHome,
  projectId: "synthetic",
  agentId: "target",
});
// Same parseDocument/setIn/toString path as native AgentConfigService maxTurns.
const installedConfig = path.join(installed, "system_config.yaml");
const normalized = parseDocument(await fs.readFile(installedConfig, "utf8"));
normalized.setIn(["max_turns"], -1);
await fs.writeFile(installedConfig, normalized.toString());
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
const fullSkill = await fs.readFile(path.join(installed, "skills/evo-harness-learned/SKILL.md"), "utf8");
for (const name of ["read_file", "exec_command"]) {
  const config = core.buildToolConfig(imported.state);
  const env = new core.Environment({ workspaceDir: tmp, toolConfig: config });
  const messages = [];
  const args = name === "read_file"
    ? { file_path: path.join(installed, "skills/evo-harness-learned/SKILL.md"), limit: 2000 }
    : { cmd: "cat SKILL.md", workdir: path.join(installed, "skills/evo-harness-learned") };
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
  outputs.push({ tool: name, truncated, complete, skill_bytes: Buffer.byteLength(fullSkill), output_chars: output.length });
  env.dispose();
}
session.dispose();
const after = inventory(installed);
const equals = (a, b) => JSON.stringify(a.files) === JSON.stringify(b.files);
const report = {
  synthetic_only: true,
  model_calls: modelCalls,
  network_calls: networkCalls,
  native_file_count: Object.keys(before.files).length,
  archive_exact: equals(before, extracted),
  native_load_exact: equals(before, ready),
  after_exact: equals(ready, after),
  native_prompt_file_parameters: nativePromptFileParameters,
  full_skill_output: outputs,
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
  outputs.some(output => output.truncated || !output.complete || output.skill_bytes !== 16000)
)
  throw new Error(JSON.stringify(report));
await fs.writeFile(reportPath, JSON.stringify(report, null, 2) + "\n");
console.log(JSON.stringify(report));
