#!/bin/bash
# Runs one PenguinHarness Task headless inside a Harbor task container and records the
# outcome under $PB_LOGS (Harbor's /logs/agent, synced to the trial directory).
#
# Started by PenguinAgent.run() as the agent user, from the task's working directory, which
# becomes the Workspace. Inputs (environment, none of them secret):
#   PENGUIN_HOME   throw-away data root, outside /logs (holds the copied model entry)
#   PB_PREFIX      install prefix (node/, bin/penguin, helper.mjs, instruction.md)
#   PB_LOGS        output directory
#   PB_PROJECT PB_AGENT PB_PROVIDER PB_MODEL PB_THINKING PB_TIMEOUT PB_ABORT_WAIT
#   PB_TIMEOUT    empty = wait for completion without a time limit
#   PB_MAX_TURNS   optional: the Agent's max_turns for this run (empty = the stock value)
#
# Outputs in $PB_LOGS: penguin-run.json (the CLI's --json output), penguin-run.stderr,
# penguin-cost-by-session.json, penguin-cost-by-model.json, penguin-outcome.json,
# penguin/{server.log,server-ready.json,max-turns.json,server-stop.json,abort.json,redaction.json,
# quiescence.json,traces/<agent>/,logs/}.
#
# Exit status: that of `penguin run` (0 = completed or soft timeout), after the cleanup
# below has run in every case; 3 when the server never came up, 4 when the requested
# turn cap could not be set, 5 for work left running after root completion, 6 when
# native trial work could not be confirmed idle, 7 when
# graceful process cleanup could not be confirmed.
set -u

WORKSPACE="$PWD"
NODE="$PB_PREFIX/node/bin/node"
PENGUIN="$PB_PREFIX/bin/penguin"
HELPER="$PB_PREFIX/helper.mjs"
OUT="$PB_LOGS"
mkdir -p "$OUT/penguin"

now_ms() { "$NODE" "$HELPER" now; }

# Every penguin call runs from the prefix, never from the Workspace: the CLI loads a .env
# file from its working directory, and a task's .env must not reach PenguinHarness.
cd "$PB_PREFIX" || exit 3

# 1. Start the server on the throw-away root. PORT=0 picks a free port; the CLI finds the
#    server through $PENGUIN_HOME/server.lock. A live TCP lock precedes App startup;
#    wait for the authenticated HTTP API, not `server status`'s process check.
SERVER_DEADLINE_MS=$(( $(now_ms) + 60000 ))
# Native first-boot notices can contain a claim token. Keep the original private
# log for diagnosis, but never copy it to the adapter's public failure output.
(umask 077; : >"$OUT/penguin/server.log")
chmod 600 "$OUT/penguin/server.log"
PORT=0 HOST=127.0.0.1 "$PENGUIN" server >"$OUT/penguin/server.log" 2>&1 &
SERVER_PID=$!
SERVER_READY=false
# Bound the whole wait, including HTTP timeouts and sleeps, to the existing 60s
# startup allowance. Passing the deadline also caps the final HTTP attempt.
while [ "$(now_ms)" -lt "$SERVER_DEADLINE_MS" ]; do
  if "$NODE" "$HELPER" ready "$PENGUIN_HOME" "$SERVER_DEADLINE_MS" "$PB_PROJECT" "$PB_AGENT" >"$OUT/penguin/server-ready.json" 2>/dev/null; then
    SERVER_READY=true
    break
  else
    READY_RC=$?
    # Target/auth/config errors are not startup transients. No mutation or Task
    # is permitted before the exact Project and Agent State become readable.
    [ "$READY_RC" -eq 1 ] || break
  fi
  kill -0 "$SERVER_PID" 2>/dev/null || break
  REMAINING_MS=$(( SERVER_DEADLINE_MS - $(now_ms) ))
  [ "$REMAINING_MS" -gt 0 ] || break
  [ "$REMAINING_MS" -le 1000 ] || REMAINING_MS=1000
  sleep "$(printf '%d.%03d' "$((REMAINING_MS / 1000))" "$((REMAINING_MS % 1000))")"
done

STARTED_MS="$(now_ms)"
RUN_RC=3
STATUS="server_failed"
SESSION_ID="-"
TIMED_OUT=false
QUIESCENT=false
COST_STATUS="unavailable"
PROCESS_CLEANUP="pending"
CONFIG_READY="$SERVER_READY"
EVO_TRAINING=false
# Verify the requested native turn cap without rewriting an already matching
# config. Frozen Evo State must match; only an ordinary stock State may be set
# through one native API PUT. A mismatch/failure skips the Task.
if [ "$SERVER_READY" = true ] && [ -n "${PB_MAX_TURNS:-}" ]; then
  if ! "$NODE" "$HELPER" set-max-turns "$PENGUIN_HOME" "$PB_PROJECT" "$PB_AGENT" "$PB_MAX_TURNS" \
    >"$OUT/penguin/max-turns.json" 2>&1; then
    CONFIG_READY=false
    RUN_RC=4
    STATUS="config_failed"
  fi
fi
# Only the installed guarded continuous-training profile spans several native
# Tasks. Freeze its original timeout before the first CLI call; test Targets and
# other methods keep the existing one-Task adapter behavior.
EVO_PROFILE="$PENGUIN_HOME/$PB_PROJECT/agents/$PB_AGENT/agent_state/skills/evo-harness-rsi-toolkit/training.json"
if [ "$CONFIG_READY" = true ] && { [ -e "$EVO_PROFILE" ] || [ -L "$EVO_PROFILE" ]; }; then
  if command -v timeout >/dev/null && timeout --version 2>/dev/null | grep -q 'GNU coreutils' && \
    "$NODE" "$HELPER" prepare-evo-wait "$PENGUIN_HOME" "$PB_PROJECT" "$PB_AGENT" "$(now_ms)" "${PB_TIMEOUT:-}" \
    >"$OUT/penguin/evo-wait-plan.json" 2>/dev/null; then
    EVO_TRAINING=true
  else
    CONFIG_READY=false
    RUN_RC=4
    STATUS="config_failed"
  fi
fi
if [ "$CONFIG_READY" = true ]; then
  "$NODE" "$HELPER" state-files "$PENGUIN_HOME/$PB_PROJECT/agents/$PB_AGENT/agent_state" \
    >"$OUT/penguin-agent-state-ready.json"
  # 2. The Task itself.
  # --source is hidden and ignored in current 0.2.16. Probe the installed CLI
  # without a model call; advertised legacy options keep benchmark provenance.
  SOURCE_ARGS=()
  if "$PENGUIN" run --help 2>/dev/null | grep -q -- '--source'; then
    SOURCE_ARGS=(--source benchmark)
  fi
  TIMEOUT_ARGS=()
  if [ -n "${PB_TIMEOUT:-}" ]; then
    TIMEOUT_ARGS=(--timeout "$PB_TIMEOUT")
  fi
  RUN_WRAPPER=()
  WINDOW_READY=true
  if [ "$EVO_TRAINING" = true ]; then
    WINDOW="$("$NODE" "$HELPER" evo-wait-window "$OUT/penguin/evo-wait-plan.json")"
    if [ "$?" -eq 0 ]; then
      read -r HARD_WINDOW CLI_WINDOW <<<"$WINDOW"
      RUN_WRAPPER=(timeout --signal=TERM --kill-after=5 "$HARD_WINDOW")
      TIMEOUT_ARGS=(--timeout "$CLI_WINDOW")
    else
      WINDOW_READY=false
      RUN_RC=124
    fi
  fi
  if [ "$WINDOW_READY" = true ]; then
  "${RUN_WRAPPER[@]}" "$PENGUIN" run -m "$(cat "$PB_PREFIX/instruction.md")" \
    --workspace "$WORKSPACE" \
    --project-id "$PB_PROJECT" --agent-id "$PB_AGENT" \
    --provider "$PB_PROVIDER" --model-id "$PB_MODEL" \
    --thinking "$PB_THINKING" --approve allow-all "${SOURCE_ARGS[@]}" \
    "${TIMEOUT_ARGS[@]}" --json \
    >"$OUT/penguin-run.json" 2>"$OUT/penguin-run.stderr"
  RUN_RC=$?
  read -r STATUS SESSION_ID <<EOF
$("$NODE" "$HELPER" run-status "$OUT/penguin-run.json")
EOF
  fi
  if [ "$EVO_TRAINING" = true ] && [ "$RUN_RC" -eq 124 ]; then
    STATUS="running"
    RUN_RC=0
    TIMED_OUT=true
  elif [ "$EVO_TRAINING" = true ] && [ "$STATUS" = "completed" ] && [ "$SESSION_ID" != "-" ]; then
    "$NODE" "$HELPER" wait-evo "$PENGUIN_HOME" "$PB_PROJECT" "$PB_AGENT" "$SESSION_ID" "$OUT/penguin/evo-wait-plan.json" \
      >"$OUT/penguin/evo-native-wait.json" 2>/dev/null
    EVO_WAIT_RC=$?
    if [ "$EVO_WAIT_RC" -eq 124 ]; then
      STATUS="running"
      RUN_RC=0
      TIMED_OUT=true
    elif [ "$EVO_WAIT_RC" -eq 5 ]; then
      STATUS="method_failed"
      RUN_RC=5
    elif [ "$EVO_WAIT_RC" -ne 0 ]; then
      STATUS="method_failed_unsettled"
      RUN_RC=6
    fi
  fi
  # 3. A soft timeout leaves the Task running. Root completion can also leave native
  #    background children running: ordinary child Session rows do not expose this.
  #    One bounded cleanup checks parent background counts and uses native stop APIs.
  #    Never submit another Task, and never read a partial cost as final completion.
  if [ "$STATUS" = "running" ] && [ "$SESSION_ID" != "-" ]; then
    TIMED_OUT=true
  fi
  "$NODE" "$HELPER" quiesce "$PENGUIN_HOME" "$PB_PROJECT" "$SESSION_ID" "$PB_ABORT_WAIT" \
    >"$OUT/penguin/quiescence.json" 2>&1
  QUIESCE_RC=$?
  if [ "$QUIESCE_RC" -eq 0 ] || [ "$QUIESCE_RC" -eq 5 ]; then
    QUIESCENT=true
    if [ "$QUIESCE_RC" -eq 5 ] && [ "$TIMED_OUT" = false ]; then
      STATUS="method_failed_active_work"
      RUN_RC=5
    fi
  else
    STATUS="method_failed_unsettled"
    RUN_RC=6
    COST_STATUS="unsettled"
  fi
fi
FINISHED_MS="$(now_ms)"

# 4. Usage and cost as the product prices them (every session in this root is this trial's,
#    subagents included).
if [ "$SERVER_READY" = true ] && [ "$QUIESCENT" = true ]; then
  "$PENGUIN" cost --by session --days 7 --project-id "$PB_PROJECT" --json \
    >"$OUT/penguin-cost-by-session.json" 2>>"$OUT/penguin-run.stderr"
  SESSION_COST_RC=$?
  "$PENGUIN" cost --by model --days 7 --project-id "$PB_PROJECT" --json \
    >"$OUT/penguin-cost-by-model.json" 2>>"$OUT/penguin-run.stderr"
  MODEL_COST_RC=$?
  if "$NODE" "$HELPER" verify-cost "$OUT/penguin-cost-by-session.json" "$OUT/penguin-cost-by-model.json" \
    >"$OUT/penguin/cost-validity.json" 2>&1 && [ "$SESSION_COST_RC" -eq 0 ] && [ "$MODEL_COST_RC" -eq 0 ]; then
    COST_STATUS="observed_after_native_idle"
  else
    COST_STATUS="unsettled"
    # Pricing/accounting failure does not undo a completed scored execution.
    # Keep its status and reward; the adapter exposes null cost and the host
    # retains the reservation until a later read-only accounting pass settles it.
  fi
fi

# 5. Native idle is a model-run boundary, not proof that a completed leaf left no
#    background OS process. Graceful server shutdown disposes that whole runtime
#    tree before Harbor's verifier. A forced stop is not a successful cleanup receipt.
"$PENGUIN" server stop --root "$PENGUIN_HOME" >"$OUT/penguin/server-stop.json" 2>&1
SERVER_STOP_RC=$?
for _ in $(seq 1 30); do
  kill -0 "$SERVER_PID" 2>/dev/null || break
  sleep 1
done
if [ "$SERVER_STOP_RC" -eq 0 ] && ! kill -0 "$SERVER_PID" 2>/dev/null && \
  "$NODE" "$HELPER" verify-stop "$OUT/penguin/server-stop.json" >"$OUT/penguin/server-stop-validity.json" 2>&1; then
  PROCESS_CLEANUP="server_shutdown"
else
  PROCESS_CLEANUP="unconfirmed"
  COST_STATUS="unsettled"
  STATUS="method_failed_cleanup"
  RUN_RC=7
  kill -9 "$SERVER_PID" 2>/dev/null
fi
wait "$SERVER_PID" 2>/dev/null

# 6. Keep the Traces and server logs, never the config: the data root stays outside /logs.
PROJECT_DIR="$PENGUIN_HOME/$PB_PROJECT"
"$NODE" "$HELPER" state-files "$PROJECT_DIR/agents/$PB_AGENT/agent_state" \
  >"$OUT/penguin-agent-state-after.json"
# Evidence failure is retained; method acceptance must not claim verified State
# injection from a missing/partial record. The original verifier still scores.
for TRACES in "$PROJECT_DIR"/agents/*/traces; do
  [ -d "$TRACES" ] || continue
  AGENT_NAME="$(basename "$(dirname "$TRACES")")"
  mkdir -p "$OUT/penguin/traces/$AGENT_NAME"
  cp -R "$TRACES/." "$OUT/penguin/traces/$AGENT_NAME/"
done
if [ -d "$PENGUIN_HOME/logs" ]; then
  cp -R "$PENGUIN_HOME/logs" "$OUT/penguin/logs"
fi

# 7. Defence in depth: scrub the configured key from everything under $OUT.
"$NODE" "$HELPER" redact "$PROJECT_DIR/.project_config.toml" "$OUT" >"$OUT/penguin/redaction.json" 2>&1

"$NODE" "$HELPER" outcome "$OUT/penguin-outcome.json" "$(printf \
  '{"exit_code":%d,"status":"%s","session_id":"%s","timed_out":%s,"server_ready":%s,"native_sessions_idle":%s,"cost_status":"%s","process_cleanup":"%s","started_at_ms":%s,"finished_at_ms":%s}' \
  "$RUN_RC" "$STATUS" "$SESSION_ID" "$TIMED_OUT" "$SERVER_READY" "$QUIESCENT" "$COST_STATUS" "$PROCESS_CLEANUP" "$STARTED_MS" "$FINISHED_MS")"

if [ "$RUN_RC" -ne 0 ]; then
  echo "penguin run exited with status $RUN_RC ($STATUS); last lines of its stderr:" >&2
  tail -n 40 "$OUT/penguin-run.stderr" >&2 2>/dev/null
  [ "$SERVER_READY" = true ] || echo "Penguin target readiness failed; see the sanitized server-ready.json receipt." >&2
  [ "$STATUS" = "config_failed" ] && cat "$OUT/penguin/max-turns.json" >&2 2>/dev/null
fi
exit "$RUN_RC"
