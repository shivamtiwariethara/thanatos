#!/bin/bash
# Harbor/RewardKit verifier for the httpbin Flask -> FastAPI migration.
#
# The behaviour groups are measured once by test_outputs.py, which records the
# pristine original live and compares the delivery against it.  The judge
# rubric is judge.toml; the blend is reward.toml.  REWARDKIT_DISABLE_JUDGE=1
# scores behaviour only (oracle check).
#
# Exit codes: 0 = a score (including 0.0) was written; 1 = verifier error and
# no reward file.  Infrastructure faults must never become a score of 0.

set -uo pipefail

LOGS=/logs/verifier
SUITE=/tests/test_outputs.py

mkdir -p "$LOGS"
chmod 700 "$LOGS"
rm -f "$LOGS/reward.json" "$LOGS/reward-details.json"

WORK="$(mktemp -d /tmp/than-verify-XXXXXX)" || {
    echo "verifier error: cannot create a private work directory" >&2
    exit 1
}
chmod 700 "$WORK"
trap 'rm -rf "$WORK"' EXIT

# --- 1. Absent-migration gate ---------------------------------------------
# 0 = a migration is present, 3 = absent (score 0, judge skipped).
python3 -I "$SUITE" --absent-gate >"$WORK/absent.json" 2>&1
absent_status=$?
if [ "$absent_status" -eq 3 ]; then
    printf '{"reward": 0.0}\n' >"$LOGS/reward.json"
    chmod 600 "$LOGS/reward.json"
    cat "$WORK/absent.json" >&2
    exit 0
fi
if [ "$absent_status" -ne 0 ]; then
    echo "verifier error: the absent-migration gate crashed" >&2
    cat "$WORK/absent.json" >&2
    exit 1
fi

# --- 2. Measure behaviour once --------------------------------------------
python3 -I "$SUITE" --measure --out "$WORK/verdict.json"
if [ "$?" -ne 0 ] || [ ! -s "$WORK/verdict.json" ]; then
    echo "verifier error: behaviour measurement failed; no reward written" >&2
    exit 1
fi

# --- 3. Red-line gate ------------------------------------------------------
gate="$(python3 -I - "$WORK/verdict.json" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        print("1" if json.load(handle).get("gate") else "0")
except Exception:
    sys.exit(2)
PY
)"
if [ "$?" -ne 0 ]; then
    echo "verifier error: unreadable verdict" >&2
    exit 1
fi
if [ "$gate" != "1" ]; then
    printf '{"reward": 0.0}\n' >"$LOGS/reward.json"
    chmod 600 "$LOGS/reward.json"
    python3 -I - "$WORK/verdict.json" >&2 <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    for reason in json.load(handle).get("gate_reasons", []):
        print(reason)
PY
    exit 0
fi

# --- 4. Judge evidence -----------------------------------------------------
NOJUDGE=""
if [ "${REWARDKIT_DISABLE_JUDGE:-0}" = "1" ]; then
    NOJUDGE="--no-judge"
else
    rm -rf /tmp/judge
    python3 -I "$SUITE" --judge-evidence --out-dir /tmp/judge
    if [ "$?" -ne 0 ]; then
        echo "verifier error: could not prepare judge evidence" >&2
        exit 1
    fi
    chmod 700 /tmp/judge
fi

# --- 5. Score --------------------------------------------------------------
python3 -I "$SUITE" --score \
    --verdict "$WORK/verdict.json" \
    --out "$LOGS/reward.json" \
    --judge-dir /tmp/judge \
    $NOJUDGE
status=$?
if [ "$status" -ne 0 ] || [ ! -s "$LOGS/reward.json" ]; then
    echo "verifier error: scoring failed; no reward written" >&2
    rm -f "$LOGS/reward.json" "$LOGS/reward-details.json"
    exit 1
fi
chmod 600 "$LOGS/reward.json" "$LOGS/reward-details.json" 2>/dev/null

rm -rf "$WORK"
exit 0
