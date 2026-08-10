#!/usr/bin/env bash
# Run one T1 session per transport mode, then package each for submission.
#
#   ANTHROPIC_API_KEY must be set. It is never read here; the model client reads it.
#
#   ./scripts/run_four_modes.sh                  # all four
#   ./scripts/run_four_modes.sh rail road        # a subset
#
# Each mode gets its OWN session, because a session holds one contestant company and one
# scored result. Four modes in one session would be one company doing everything, which
# is the combined system, not four entries.
#
# The modes run ONE AFTER ANOTHER, not together. Concurrency would finish sooner, and
# nttd supports it, but a sequential run keeps one OpenTTD process and one model in
# flight at a time, so a failure is attributable and the logs read in order.
#
# Logs land in logs/<mode>.log. Nothing here submits: `nttd submit` is a separate,
# deliberate step, and a run worth submitting is worth looking at first.

set -uo pipefail

NTTD="${NTTD_DIR:-$HOME/exp/nttd}"
SCENARIO="config/benchmark/t1_256_flat_1001_stepped.conf"
URL="${NTTD_BASE_URL:-http://127.0.0.1:8000}"
MODES=("${@:-}")
[ -z "${MODES[0]}" ] && MODES=(rail road water air)

mkdir -p logs

if ! curl -sf -o /dev/null "$URL/openapi.json"; then
  echo "No nttd at $URL. Start one with: cd $NTTD && uv run nttd server"
  exit 1
fi

start_session () {
  local mode="$1"
  local sid
  sid=$(cd "$NTTD" && uv run nttd session create --config "$SCENARIO" --name "$mode-t1" 2>&1 \
        | grep -oE "ses_[0-9_a-f]+" | head -1)
  [ -z "$sid" ] && return 1
  (cd "$NTTD" && uv run nttd session start -s "$sid" --agent-companies 1 >/dev/null 2>&1) || return 1
  echo "$sid"
}

for mode in "${MODES[@]}"; do
  sid=$(start_session "$mode") || { echo "$mode: could not start a session"; continue; }
  token=$(cd "$NTTD" && uv run python -c \
    "import json; print(json.load(open('logs/sessions/$sid/participants.json'))['0'])")
  echo "$mode  session=$sid"
  echo "$sid" > "logs/$mode.session"

  echo "  running $mode ..."
  uv run python examples/langgraph_runner.py \
      --session "$sid" --token "$token" --mode "$mode" --url "$URL" \
      > "logs/$mode.log" 2>&1
  echo "  $mode finished at $(date +%H:%M:%S)"
  (cd "$NTTD" && uv run nttd session stop -s "$sid" >/dev/null 2>&1) || true
done

echo
for mode in "${MODES[@]}"; do
  sid=$(cat "logs/$mode.session" 2>/dev/null || echo "?")
  built=$(grep -c "refused" "logs/$mode.log" 2>/dev/null || echo 0)
  echo "$mode: session $sid, $built refusal(s). Log: logs/$mode.log"
  echo "   package it with: cd $NTTD && uv run nttd submit --session $sid"
done
