# Evo-Harness / GDPevo v1

This experiment is driven by the main Penguin reading `evo-harness-rsi-toolkit`.
It reuses the native `run_subagent` learning/evaluation workers, Target Agent
State, versioned snapshots and `scoreboard.yaml`. Evaluation workers launch
Harbor; the host Controller alone advances the experiment. `protocol.py`
performs one model-free file operation; it has no dispatch or scheduling loop.

Method revision `2026.10.21.3` uses native plugin hooks for the continuous
training contract. They inject the full training role contract, restrict the
training coordinator to method helpers and native child delegation, and check
evidence before test dispatch or Goal completion. The main Penguin still
chooses and calls every native worker. No hook starts a model, Harbor, child or
replacement Task. Ordinary RSI methods and the shared evaluation protocol are
unchanged. Install Evo explicitly; it is not a default plugin for other Agents.

`profile.json` is a **draft without spending authorization or a selected model**.
It fixes ERP task_group_005, five train/five test, one learning pass, one attempt
per training task, one batch compilation, one test repeat, training concurrency
1 and test concurrency 5. Within each H0 or final test phase, the main Penguin
launches all five independent test workers concurrently, with a total test
concurrency limit of 5; the two State phases do not overlap. Training remains
one continuous group with its five members solved sequentially. This is a
development pilot, not unbiased coverage of
all groups. Fix the actual provider/model_id/thinking, current USD budget, host
and container commits/bundle hash and successful isolation probes before paid
work. Do not import old WikiSkill results, budgets or learned content.

The user froze `test_phase_order` as `train_compile_freeze → h0_test → final_test`
for this ERP experiment, overriding the method's generic H0-first timing.
Create the empty-library H0 snapshot before training, then complete all five
training members, compilation, export verification and package freezing before
executing any held-out test. Measure the frozen H0 State on all five tests at
once; after that matrix and its workers have finished, measure the frozen final
State on the same five tests at once. If the frozen package equals H0, run only
the H0 test matrix and explicitly reuse it for final (`reused_equivalent_h0`).
This timing changes no learning rule: neither matrix can affect the compiled
package. The main Penguin follows this frozen order through native workers;
`protocol.py` validates files and does not schedule stages.

Sources verified 2026-10-21:

- Evo-Harness [paper v2](https://arxiv.org/html/2608.15071v2), official
  [3c7c7b8](https://github.com/A-EVO-Lab/a-evolve/tree/3c7c7b8c62a3f07da1d4407aa03f0ca079954bcf).
- GDPevo [paper v1](https://arxiv.org/html/2608.03764v1), original task data
  `56d60ae4ae5e067d1ec0ee1f850622e69f422179`, as pinned in SOURCE.md.
- Benchmark main `893563466fd1a860da4e42270b8e30dfd67f2d40` contains
  [continuous training #5](https://github.com/Prism-Shadow/penguin-harness-benchmark/pull/5).
  [Accounting #6](https://github.com/Prism-Shadow/penguin-harness-benchmark/pull/6)
  is still open at `de52109d849d7db9618846e8cd9120f3f78630ae`; do not claim it
  merged. This experiment checks fixed cell identities and complete matrices
  itself; it does not import historical aggregation or alter graders.

## Model-free preparation

Use `python3 -B protocol.py REQUEST.json`. All operations return JSON; no model
API or Harbor run is called. Provide absolute paths. Operations:

| op | Required fields |
|---|---|
| preflight | config, repo |
| prepare-benchmarks | config, config_sha256, repo, output (the existing native Project's benchmarks directory) |
| verify-benchmarks | same as prepare-benchmarks; checks files without changing them |
| prepare-trainer | config, repo, evo_tool, base_state (sanitized initialized H0), method_skill, training_budget_usd, host_operation_id, config_sha256, output (new Trainer agent_state) |
| pack-state | evo_tool, state, output (new tar.gz; archives native State contents) |
| verify-export | evo_tool, submission, training_export, train_ids, private_learning, trace_root, runtime |
| verify-freeze | evo_tool, export_request (verify-export JSON), h0_snapshot, final_snapshot (native agent_state/ tar.gz), h0_version, final_version, target_state, test_case_ids, runtime, active_state (h0/final; default final) |
| verify-injection-matrix | config, config_sha256, evo_tool, matrix_request, snapshot (frozen native archive), runtime (complete frozen object), expected_version, benchmark (absolute native test Benchmark directory) |
| matrix | evo_tool, cells (JSON array of unchanged native cell records), case_ids, runs, identity, time, summary_title, summary |

`identity` is agent_id, expected_version, provider, model_id and thinking_level.
`matrix` produces an Evaluation Center record only for complete valid cells.
The Controller appends that record to the native scoreboard and re-parses it.
Unknown costs stay null and have an explicit completeness flag; native known-
cost means are not total experiment spend. Missing cells never become zero.
Keep original verifier-defined missing/unparseable-answer zeroes unchanged.

Initialize the H0 Target using Penguin's native CLI and the method's State
reference. Before its first snapshot/Session, run the fixed native API preparation:

```bash
node agents/penguin_agent/helper.mjs prepare-evo-state "$PENGUIN_HOME" "$PROJECT" "$TARGET" target
```

This reads the complete native tool configuration, preserves every unrelated
field, sets unlimited turns and disabled memory/hooks, and raises only the two
command-output limits to at least 104192 characters. It refuses existing
Sessions, snapshots, noninitial versions or incomplete tool tables; an already
prepared fresh State requires no PUT. `trainer` mode is available only for a
separately initialized fresh native Trainer; normal `prepare-trainer` copies
the prepared H0 and installs its specified training hooks before snapshot.
Copy the sanitized H0 State into a separately created Trainer Agent
using prepare-trainer; snapshot both. The Trainer includes the method and the
fixed training configuration. Test Target carries only the learned slot.
The source method must already be natively installed in the Controller State;
`prepare-trainer` copies its installed `hooks/evo-harness-rsi-toolkit` package
(or explicit `method_hooks`) and enables only that package in Trainer. H0/final
Target retain disabled hooks. Native children retain Penguin's normal leaf
semantics; their evidence is checked by the training root and host.
Call `prepare-benchmarks` once to register frozen native Benchmarks from the
complete frozen configuration. It only writes manifests, statements and rubrics;
the main Penguin still dispatches every worker. The helper checks the matching
six-task preflight pins and deployment hashes. Existing packages are verified,
never overwritten, including on a retried preparation command. An actual
material difference requires a newly versioned configuration and Benchmark id.
Use `verify-benchmarks` again before delegation to detect material drift.

Use its returned `training.case_ids` and `test.case_ids` in worker tickets,
freeze requests, matrices and scoreboards. Native Case ids require `CASE-`:
`CASE-001-tg003-train` and `CASE-001-tg003-test-001` are examples. The separate
`case_to_task` mapping retains the original Harbor task names; never rename
the source tasks or confuse `config.test_ids` with native Case ids. The public
statement and adjacent `harbor-launch.json` include all frozen adapter options,
especially `node_version`, bundle, host Project and complete runtime. Workers
replace only the State-archive/job placeholders and follow the unchanged shared
evaluation protocol. Do not hand-write statements or omit pinned adapter flags.
The task instructions/graders/split remain unchanged. Do not use a mixed
job.yaml expecting automatic Skill transfer.

The group is `benchmarks/gdpevo/tasks/task_group_005/tg005-train`.
Tests are `.../task_group_005/test_tasks/tg005-test-001` through `005`.
Task TOML grants training 21,600 seconds and test 14,400 seconds of solving,
plus 210 seconds for abort/cleanup. Use `run_timeout=task` and `max_turns=-1`,
reserve 3,600 seconds *within* training time for reflection/compilation/export.
Do not tell the model it has more time than the effective adapter limit.

## One top-level invocation

Install the method plugin in a dedicated Project/Controller, initialize the
Target, register the matching frozen training/test Benchmarks, and configure
one authenticated provider/model in private configuration. Read-only model
metadata can verify identity/capabilities; never print/copy keys into artifacts.
Build an install bundle from the reviewed Penguin checkout (0.2.16), inspect
`--version`/`run --help`, record architecture/hash, and use it for both States.
Follow [the source bundle build and verification steps](SOURCE_BUNDLE.md);
`make_source_bundle.sh` packages the exact clean commit and native plugin,
with no registry CLI fallback. Freeze the resulting archive path and hash.
The adapter detects whether the installed CLI advertises `--source`; current
0.2.16 keeps a hidden ignored compatibility option and records source `cli`.
Do not silently use its default 0.2.13 bundle.

After the user authorizes the current amount, launch a single native root:

```bash
penguin run --project-id <dedicated-project> --agent-id <controller> \
  --provider <confirmed-provider> --model-id <confirmed-model> --thinking <confirmed-level> \
  --workspace <experiment-workspace> --approve allow-all --json \
  --message "使用 Evo-Harness，读取已冻结的实验配置，在 GDPevo task_group_005 的原始 5 train/5 test 上做一次批次学习和冻结技能对比，按已给的模型与本次预算完成并汇报。"
```

No `--source` on current Penguin. The Controller reads the method Skill and this
frozen order, snapshots H0, invokes one group training worker, and verifies and
freezes the exported package. It then invokes the five H0 evaluation workers
concurrently, waits for their complete matrix and termination, publishes the
exact frozen package into a new Target State, and invokes the five final test
workers concurrently.
It records the whole complete matrix on the test Benchmark scoreboard.
The container's training root delegates only its batch; it cannot run baseline
or held-out test. Each native Solver answers and reflects in its original
conversation after fully observing its one authorized judge response; the
independent Evolver compiles the whole batch once. No separate Reflector Session
is created, and Codex never writes lessons. Codex only starts the root, observes, and addresses explicit infra
failures. It does not ask a script to choose the next experiment phase.

Follow the installed `references/control.md` for the request schemas. Host
configuration requires native `read_file` evidence of the full method Skill
and control reference. Register a worker's exact native call, reserve its
returned slot/hash in the journal, then invoke `run_subagent` yourself. Keep
controller overhead reserved. For each held-out phase register and dispatch
all five workers together. The gates reject unregistered calls, repeated slots,
runtime changes and tests before verified training/freezing. Missing or failed
training is `method_failed`; it can never become an empty-library reuse.

## Export and evidence

Training answers are `/app/submission/answers/train_NNN.json`, learned package
`/app/submission/skill/`; Harbor exports `artifacts/submission`. Private context,
candidate, decision and role audit records live under `/logs/agent/evo-harness`
and export as agent logs. The training root invokes the method helper's
`seal-training` operation after all native role traces have completed. Supply
all five contexts, concatenated candidates, Evolver decisions, pre-batch library,
frozen runtime and the complete-Skill byte budget, final package, and each role's original
trace/binding/material/output paths. Store audited material copies in the private
learning directory before role delegation. Do not manually author a complete flag.
The sealer verifies full chunk reads, role output hashes, and reconstructs the
compiled package for an exact comparison. The host `verify-export` repeats these
checks using the exported logs and traces, even after container paths disappear.
Trace basenames must be unambiguous; missing evidence blocks publication.
Each training member uses the immutable attempt identity in installed
`training.json`. The canonical request/feedback paths are derived from that
profile, not selected by the caller. Each task gets a fresh native Solver child of the same training root. Its
reflection audit reuses that exact Session, runtime and complete terminal trace;
only the batch Evolver is another fresh child. Negative feedback requires a
sealed reflection output, including when justified candidates are empty.
Success does not require reflection, and judge errors are not negative feedback.
The full answer-and-reflection Session is captured only after it finishes;
subsequent trace mutation remains an error. Its cumulative native usage is
counted once by Session, including reflection, not once per audit role.
Unknown usage remains pending and keeps its budget reservation. Capture rejects visible extra judge probes,
repeated judging and substituted receipts. This is a cooperative method
boundary, not a security sandbox against arbitrary hidden code/network calls.

Verify export, install the exact allowed package bytes into native Target State, snapshot,
pack through agent_state_tar, and compare actual runtime injection receipts.
Train answers, raw trace, judge output and private scratch never enter test.
Before declaring method completion, `verify-injection-matrix` binds each native
cell to its unique original Harbor trial, task, model, verifier reward, duration
and cost. It checks the actual installed State against the native snapshot;
the adapter records file hashes after extraction, after native startup/turn-cap
verification and after shutdown. Freeze the native-API-prepared `max_turns: -1`
and full command-output limits before transport. Matching startup is GET-only;
a frozen Evo mismatch stops before a Task and cannot silently rewrite YAML.
An unnecessary native PUT can reserialize equivalent YAML, so it is not used
as a readiness check. All three complete State maps must equal the frozen
snapshot; there is no blanket startup-rewrite exemption. Vault, memory and
schedules are not read or retained by this inventory.
Method revision `2026.10.22.6` exports a fixed Reader/manifest at `SKILL.md`
and one complete procedure per `methods/evo-method-<identity>/SKILL.md`.
For native State installation, the manifest-bound mapper puts the Reader at
`skills/evo-harness-learned/SKILL.md` and every method at its own sibling
`skills/evo-method-<identity>/SKILL.md`. This matters because the native loader
recognizes only first-level Skill directories. General/topic identity, evidence
conditions and complete procedure bodies are retained in the package; runtime
loading follows the official CL distinction below. No selector, shortlist
or runtime `max_entries` is introduced. Private compilation keeps the same
ADD/MERGE/REVISE/SKIP operations and general/topic slot limits.

The complete package — Reader, manifest, method metadata and all bodies — must
fit **16000 UTF-8 bytes**. Preparation, freeze and injection acceptance reject
any other `max_bytes`; enlarging native output capacity does not change this
budget. No line may exceed native read_file's 2000 UTF-16 unit limit. Oversize
drafts must be revised before sealing and are never truncated during injection.

The reference CL path at official commit
`3c7c7b8c62a3f07da1d4407aa03f0ca079954bcf` continues the original Solver's
message history and model for feedback-based proposals. It loads every current
context skill body, but only general skill descriptions; its defined
`_select_skills` is not called on that path. The paper's budgeted Select is a
separate description. Runtime loading follows that distinction: every current-group topic body and
every general description are observed, with no per-task selector or other-group
skills. General descriptions are explicit Evolver outputs preserved in the Reader
and native metadata; general bodies are retained for provenance but are not read
by the doing-task Agent. Do not relabel general rules as topic to force injection,
or hide full general procedures inside their descriptions. Independent native
Skill packaging is this GDPevo adaptation, not a claim of identical storage layout.

The audit requires the original test Session to observe the entire Reader and
every topic method before business actions. The Reader carries all general
descriptions and an explicit `load_mode` for each entry. Native read_file pages
may be combined in actual observation order; a foreground single-file cat whose
stdout equals that file's frozen bytes also proves a complete file read. Missing
or truncated pages must be read before proceeding. The manifest, native Skill
metadata and a topic method's title do not substitute for its body. General
body reads via native read_file or literal cat/head are rejected at every point,
including after the required observations are complete. Only public
input/statement/environment-document reads and the bounded inspection grammar
may precede full-package observation. Concurrent business calls issued before
the final required observation are rejected. Empty H0 still reads its complete
fixed Reader. All five trials bind the same frozen package, State file map and required
loading set (Reader plus all topic files).

Each training Solver likewise reads the complete cold H0 Reader before task
actions. Capture and compilation repeat that check against the hash-bound
training profile and trace. The test auditor reuses the reviewed method's
`scripts/inspection.py` grammar, granting exact frozen sibling Skill paths;
training still permits only that task's granted public files and input directory,
not other members or private feedback.

The new `evo-injection-matrix-v3` receipt retains `injection_mode: full_skill`
for loading the entire fixed official-CL set without selection. `skill_sha256` now identifies the whole
export-package file-map fingerprint, `skill_bytes` counts the entire package,
and `skill_files` binds each native State path to its SHA. `loaded_skill_files` and `loaded_skill_bytes` identify only the actual required
Reader/topic set, and `loading_policy` fixes context-body/general-description.
Cells include those same fields plus each required file's completed-read position.
A general body's presence in the frozen file map is not an injection claim. This is a new
schema, distinct from the previous raw single-file SHA receipt; old evidence is
preserved under its original version. Original trial, model, scoring, timing,
cleanup and cost checks remain unchanged. Missing method evidence blocks
completion without changing verifier scores. Unknown cost stays null/pending
and does not justify another trial. Receipts contain no answers or feedback.
This cooperative trace audit is not a sandbox against obfuscated arbitrary code.

The offline revision 12 audit policy additionally accepts a bare stdin `sort`
immediately after literal public file-name enumeration, for example
`find /app/input -type f | sort`. The preceding `find` must have exactly one
canonical root in the task workspace's `input` directory, explicitly use
`-type f`, and use only the existing type/depth/name/print predicates, without
repeated predicates or `-print0`; optional `-print` must be last, after all filters.
Quoted literal names and normalized relative
paths are supported; parent traversal remains forbidden. The existing bounded
stdin `head` can follow the sort. Every command in a combination must still
pass its own inspection rule. No sort options, operand files, output redirects,
arbitrary program, or content stream (including `cat` or `head`) feeding sort
is accepted. Only the existing literal stderr suppression to `/dev/null` is
allowed. Directory enumeration output is filename discovery, never proof of
a complete Skill read. Full-file observation ordering, exact bytes and the
whole-file budget remain required.

This is an explicit audit-policy expansion. Frozen revision `2026.10.21.11`
allowed only six inspection programs and only a bounded stdin `head` as a pipe
sink, so it did **not** permit `find /app/input -type f | sort`. Its recorded
ERP method failure remains unchanged. Any offline re-audit must name the new
validator/source hash and retain the original verdict; it is neither a new
experiment nor a retrospective claim that the old execution obeyed every
frozen instruction. This change does not authorize deployment, paid calls or
retries, and does not change tasks, scores or learned guidance.

H0-equivalent compilation runs the H0 matrix after training and freezing, then
reuses it with an explicit reuse label; there is no second measurement or
claimed improvement.
Both empty and nonempty packages must pass `verify-freeze`, which repeats the
complete export audit and compares native snapshots and the live Target.
Only the learned slot and its native version may differ; the fixed Reader
and whole-Skill byte budget stay identical between H0 and final. A legal empty batch still needs all five
Solver contexts and one Evolver with `[]` before compilation/export/sealing.

For an installed guarded Evo group-training profile, the adapter submits the
initial CLI Task once and then waits read-only across native background-child
and notice-triggered Tasks. The first CLI idle is not training completion.
Success requires the training root's profile-bound strict `evo-acceptance.json`
and all native Sessions/background work idle. Missing acceptance, including
the gap between child settlement and native notice delivery, keeps waiting;
the adapter never submits another Task or chooses a training phase. A failed
acceptance/guard receipt, or a current structural native abort after the root
becomes idle with no queued follow-up, enters normal cleanup. A root abort may
leave independent children alive; cleanup stops them without waiting for their
completion. Unknown evidence can never become success.

This training-only wait and the initial CLI share one absolute `PB_TIMEOUT`
deadline, fixed before the first run. GNU `timeout` must be available before
dispatch; its precise remaining hard window bounds the CLI's integer-second
soft window. The original separate cleanup allowance remains unchanged.
`penguin/evo-wait-plan.json` and `evo-native-wait.json` record the deadline and
terminal status without model messages, prompts, credentials or training data.
Test Targets and other methods retain their existing one-Task lifecycle.

Before collecting trial cost, the adapter checks all Sessions and native
background children/processes in that trial's private data root. Work remaining
at a real terminal boundary is recorded as failure and aborted;
unconfirmed shutdown/accounting stays unsettled. The host's own final request
cannot be included in its report before it finishes, so label that liability
`pending_final_request` and settle it in a later read-only accounting pass.

Model-free environment probes must check unprivileged uid, inaccessible private
grader/service files, business loopback, model host reachability, nonallowed
external host failure, no published business ports, and test judge disabled.
Never infer isolation from `network_mode` alone. Production preflight remains
unverified until probes run on the actual frozen image/bundle/host. Shared
server fixes must be limited to this job; no Docker prune/daemon restart.

## Acceptance without paid calls

Run the plugin's Python tests, `test_protocol.py`, adapter compatibility tests,
native plugin/subagent tests, Web routing tests and Penguin build. Fixtures
must say synthetic. Real learned content, scores, injection and total spend
remain unmeasured until the separately authorized experiment. A paid trial
must collect actual read/feedback/injection/cleanup evidence; an offline fixture
cannot establish that a real model followed a Skill.

After building Penguin, run the actual native State/output regression without
calling a model:

```bash
node experiments/gdpevo/evo-harness/v1/test_native_runtime.mjs \
  /absolute/path/to/built-penguin-checkout /absolute/path/to/native-state-output-probe.json
```

It runs `prepare-evo-state` against a real compiled native HTTP server in a
private temporary data root, with external/model/Task requests prohibited.
It proves that an unnecessary native PUT can normalize equivalent YAML,
that the prepared State needs no repeated PUT, and that restored matching
States are GET-only while frozen mismatches fail without changing bytes.
It then transports the State through the real tar format and loads a native
Session with model traps. A synthetic complete 16000-byte Skill is read
through actual native read_file and foreground cat, proving its full observation
and unchanged State across transport. The retained 104192-character command
output cap is conservative; the whole Skill budget remains 16000 UTF-8 bytes.
For offline checks against existing compiled dependencies, set
`EVO_NATIVE_CHECKOUT` to that built checkout and pass the new method source
checkout as the first argument. This does not substitute the new method source
or install dependencies. Python controller fixtures use the same explicit
variable solely to resolve the existing native YAML parser dependency.


Revision `2026.10.22.5` keeps the R14 algorithm and dataset protocol. It moves
already-required material observation checks before the actual training judge
request, so an unread public input can be reported and read while the answer is
still open, without issuing a score request. A recoverable, unsealed candidate
format error returns a normal structured incomplete receipt; executed boundary
violations remain failures. R14 trial evidence and original verdicts are retained.
The training/test split, once-only actual judge, frozen answer, same-Solver
reflection, one batch compilation and full-file held-out injection are unchanged.


Revision `2026.10.22.7` repairs full-file observation proof for the native
read-only command combinations seen in the preserved 010 failures. Training and
test injection now call the same `inspection.complete_file_stdout` implementation.
A standalone literal `cat` still needs exact stdout equality. A bounded literal
`pwd`/`ls`/short separator `echo` sequence joined by `&&`, ending in exactly one
`cat` of the required file, may prove its read only when the complete frozen
bytes appear as a unique exact output tail. Dynamic syntax, pipes, redirections,
other content reads, forged echo text, missing/truncated output and business
calls before the complete observation remain invalid. The general-description /
topic-body loading policy, 16000-byte package budget and raw-trial checks remain
unchanged. This is a new verifier version; old runs and old audit conclusions
are retained rather than rewritten as originally passing.

Case statements also remind evaluation workers to preserve the exact native
`test_agent_id` case when resolving `STATE` through the shared evaluation
protocol. No new archive builder or experiment scheduler is introduced.

Revision `2026.10.22.8` preserves native orchestration and Evo learning, while
correcting the delivered Evolver description contract: general descriptions
are self-contained method summaries; only topic bodies are fully loaded.
The fixed GDPevo Reader generates exact native read_file calls for every topic
page, included in the same 16,000-byte package budget. They are a read plan,
not a selector or executable loader. The native AGENTS.md is an explicitly
allowed State instruction; one initial literal public/workspace cd with &&
and the fixed public JSON display loop are preparation operations. Business
APIs and writes still require all required Skill observations first. These
are recorded protocol extensions, not retroactive passes for .6/.7 evidence.
Old018's business call before both topic observations remains invalid.


R19 offline engineering follow-up (not deployed): each registered launch now
records its exact `state_archive_template`. Every authorized pre-start retry
chooses a new job and matching `<job>.agent-state.tar.gz` archive from the same
frozen State. The original own-job archive assertion remains enforced; neither
renaming a started job nor reusing an earlier attempt's archive path satisfies
it. This metadata does not authorize retries, and score/State/trace validation
is unchanged. Original runs and audit verdicts are never overwritten.

Snapshot examples, State packing, live freeze comparison and the native container
inventory now share the top-level transport exclusions `.vault.toml`, `memory`
and `schedule`. All retained configuration and Skill bytes remain exact; nested
Skill files with those names are retained. A snapshot containing excluded roots
is rejected explicitly, not silently filtered or repaired. The live State
comparison ignores only the native runtime roots omitted from transport. This
is a new acceptance scope, not a claim that an old unfiltered snapshot passed.

## Explicit historical replay (`2026.10.22.9`)

`offline_replay.py` implements `evo-harness-offline-replay-v1` separately from the
full training/H0 protocol above. The main Penguin reads the method's replay
reference, runs five fresh learning children on hash-bound historical training
bundles, then one Evolver. This is new learning from retained evidence, not
continuation of the original Solver Session or a new valid full-RSI run. The
source manifest records its original method/configuration and original-version
training validation. Test answers, old candidates and generated Skills are not
learning inputs; existing source runs, costs and audit conclusions stay intact.

For the currently authorized scope, `test_repeats: 1`,
`test_phase_order: ["final_test"]`, and `test_concurrency: 5` mean exactly five
new Target trials after learning and freezing. There is no new training trial,
baseline or automatic second/third round. General descriptions and this group's
topic bodies retain the same loading policy and whole-package 16,000-byte cap.
A valid empty compilation still runs these five final tests; this protocol does
not invent/reuse a new H0 measurement. Reused historical baseline comparisons
must identify their original version and independent acceptance status.

The frozen config keeps the full runtime, task ids, adapter, source commits,
bundle, public task/preflight and native deployment pins. Add `source_manifest`
and its `source_manifest_sha256`, `limits: {"general": 5, "topic": 5}`,
`max_bytes: 16000`, explicit paid authorization and a per-run budget no greater
than $10. `test_agent_id` is the exact native Target id (optionally prefixed by
`create:`). `test_benchmark_id` names this new test Benchmark; the distinct
`benchmark_id` is an unused compatibility identity and is never registered or
executed. Replay requires its explicit test-only zero-model deployment preflight:
exactly the five test task ids and the replay receipt schema, with the same
pinned artifacts, isolation checks and five-way overlap. The full RSI protocol
continues to require all six environments, including training; neither scope
accepts the other scope's roster or fabricates a missing probe.

Every request carries `config` and `config_sha256`. These operations prepare or
validate only; none calls Harbor, spawns a role or advances the experiment:

- `verify-source`: checks the frozen historical source through the same pinned
  Penguin `replay.py`. Supply `evo_tool`, or the frozen `deployment.penguin_checkout`.
- `prepare-benchmarks` / `verify-benchmarks`: create/check only the five original
  test Cases, reusing native registration and exact launch/archive parameters.
- `verify-freeze`: takes `evo_tool`, `learning_seal`, `initial_snapshot`,
  `final_snapshot` (both native archive path strings) and `target_state`. It
  revalidates the six original native learning-role traces and compilation,
  binds the compiled package to the final native snapshot, and checks unchanged
  runtime/configuration outside the learned Skill paths and version. The initial
  empty snapshot is a configuration reference, not a measured baseline.
- `verify-round` / `verify-test-matrix`: take `evo_tool`, `freeze_request` (the
  prior request file), `round: 1` and `cells` (five native worker records). They
  revalidate the freeze and bind every record to its original Harbor trial,
  installed/before/ready/after State, exact case mapping, full loading trace,
  runtime, reward, cleanup and cost. Missing cells remain incomplete. A valid
  score with `cost: null` stays valid and pending; the native replay ledger
  retains the reserve and also accounts for main/learning/worker costs.
