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
# The modes run CONCURRENTLY. nttd allocates a port pair per session and was hardened for
# exactly this. Sequential was right while the system was unproven, because a failure was
# then attributable; now that a mode completes cleanly, concurrency gives the same early
# evidence from all four within minutes instead of one at a time, and finishes in roughly
# the time one run takes.
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

pids=()
for mode in "${MODES[@]}"; do
  sid=$(start_session "$mode") || { echo "$mode: could not start a session"; continue; }
  token=$(cd "$NTTD" && uv run python -c \
    "import json; print(json.load(open('logs/sessions/$sid/participants.json'))['0'])")
  echo "$mode  session=$sid"
  echo "$sid" > "logs/$mode.session"

  (
    uv run python examples/langgraph_runner.py \
        --session "$sid" --token "$token" --mode "$mode" --url "$URL" \
        > "logs/$mode.log" 2>&1
    echo "FINISHED $mode at $(date +%H:%M:%S)" >> "logs/$mode.log"
  ) &
  pids+=($!)
done

echo "Running ${#pids[@]} mode(s) concurrently. Watch: tail -f logs/*.log"
for pid in "${pids[@]}"; do wait "$pid"; done

# Sessions are stopped only after every mode has finished, so a slow mode is never cut
# short by a fast one completing.
for mode in "${MODES[@]}"; do
  sid=$(cat "logs/$mode.session" 2>/dev/null) || continue
  (cd "$NTTD" && uv run nttd session stop -s "$sid" >/dev/null 2>&1) || true
done

echo
for mode in "${MODES[@]}"; do
  sid=$(cat "logs/$mode.session" 2>/dev/null || echo "?")
  built=$(grep -c "refused" "logs/$mode.log" 2>/dev/null || echo 0)
  echo "$mode: session $sid, $built refusal(s). Log: logs/$mode.log"
  echo "   package it with: cd $NTTD && uv run nttd submit --session $sid"
done
