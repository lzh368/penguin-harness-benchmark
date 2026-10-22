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
reference. Copy its sanitized State into a separately created Trainer Agent
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
or held-out test. Reflector/Evolver are native role Sessions, never Codex-written
lessons. Codex only starts the root, observes, and addresses explicit infra
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
frozen runtime and selection budgets, final package, and each role's original
trace/binding/material/output paths. Store audited material copies in the private
learning directory before role delegation. Do not manually author a complete flag.
The sealer verifies full chunk reads, role output hashes, and reconstructs the
compiled package for an exact comparison. The host `verify-export` repeats these
checks using the exported logs and traces, even after container paths disappear.
Trace basenames must be unambiguous; missing evidence blocks publication.
Each training member uses the immutable attempt identity in installed
`training.json`. The canonical request/feedback paths are derived from that
profile, not selected by the caller. Every learning role must be a fresh native
child of the same training root. Capture rejects visible extra judge probes,
repeated judging and substituted receipts. This is a cooperative method
boundary, not a security sandbox against arbitrary hidden code/network calls.

Verify export, install the exact allowed package bytes into native Target State, snapshot,
pack through agent_state_tar, and compare actual runtime injection receipts.
Train answers, raw trace, judge output and private scratch never enter test.
Before declaring method completion, `verify-injection-matrix` binds each native
cell to its unique original Harbor trial, task, model, verifier reward, duration
and cost. It checks the actual installed State against the native snapshot;
the adapter records file hashes after extraction, after native startup/turn-cap
configuration and after shutdown. Freeze `max_turns: -1` and the full native
command-output limits before transport: supported native loading/turn-cap setup
is then byte-idempotent. All three complete State maps must equal the frozen
snapshot; there is no blanket startup-rewrite exemption. Vault, memory and
schedules are not read or retained by this inventory.
The audit then checks the original test Session's full reader/index reads and
one canonical selector stdout against the frozen package. Complete native
read_file observations and a foreground single-file cat whose original stdout
exactly matches the frozen bytes both prove a reader/index read. Before selection,
only a conservatively parsed subset of read-only public-file/directory inspection
is accepted; arbitrary code, APIs, task execution, file writes and entry-body
reads remain forbidden. Shell syntax is never treated as proof of arbitrary code
being harmless. Empty H0 and a
nonempty library with no relevant entries both permit `selected: []`; neither
permits skipping the reader/selector. Missing method evidence blocks completion
without deleting or changing the original verifier's score. Unknown cost stays
null/pending and does not justify rerunning a scored task. The audit emits only
selection ids/hashes/byte counts and original public cell metrics, never test
answers or per-item error feedback for learning. This cooperative trace audit
is not a sandbox against obfuscated arbitrary code.
H0-equivalent compilation runs the H0 matrix after training and freezing, then
reuses it with an explicit reuse label; there is no second measurement or
claimed improvement.
Both empty and nonempty packages must pass `verify-freeze`, which repeats the
complete export audit and compares native snapshots and the live Target.
Only the learned slot and its native version may differ; the reader, selector
and selection budgets stay fixed. A legal empty batch still needs all five
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

It executes the method's documented field-only configuration recipe against a
fresh native State, transports it through the real tar format and loads a native
Session with network/model traps. A synthetic 16000-byte procedure demonstrates
that the old native 16000-character output cap truncates its selector receipt,
while the frozen 104192-character cap preserves it in full. This changes only
output transport capacity; the learned-guidance budget remains 16000 bytes.
