
#!/usr/bin/env bash
# Start a Semaphore template run via the API, wait for completion,
# and retrieve the actual execution output.

# Usage:
#   SEMAPHORE_URL=http://10.0.0.23:3000 \
#   SEMAPHORE_USER=admin \
#   SEMAPHORE_PASS=secret \
#   ./scripts/semaphore-run.sh [project_id] [template_id] [stack_name]

set -Eeuo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ID="${1:-${SEMAPHORE_PROJECT_ID:-1}}"
TEMPLATE_ID="${2:-${SEMAPHORE_TEMPLATE_ID:-41}}"
STACK_NAME="${3:-${SEMAPHORE_STACK_NAME:-traefik}}"

: "${SEMAPHORE_URL:?SEMAPHORE_URL is not set}"
: "${SEMAPHORE_USER:?SEMAPHORE_USER is not set}"
: "${SEMAPHORE_PASS:?SEMAPHORE_PASS is not set}"

POLL_INTERVAL="${SEMAPHORE_POLL_INTERVAL:-3}"
MAX_WAIT="${SEMAPHORE_MAX_WAIT:-1800}"

BASE="${SEMAPHORE_URL%/}"

for cmd in curl jq; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "Missing required command: $cmd" >&2
        exit 2
    }
done

# Temporary files
COOKIE_JAR="$(mktemp)"
LOGIN_FILE="$(mktemp)"
START_FILE="$(mktemp)"
TASK_FILE="$(mktemp)"
OUTPUT_FILE="$(mktemp)"
RAW_OUTPUT_FILE="$(mktemp)"

cleanup() {
    rm -f "$COOKIE_JAR" "$LOGIN_FILE" "$START_FILE" \
          "$TASK_FILE" "$OUTPUT_FILE" "$RAW_OUTPUT_FILE"
}
trap cleanup EXIT

log() {
    printf '%s\n' "$*" >&2
}

# ---------------------------------------------------------------------------
# 1. Authenticate
# ---------------------------------------------------------------------------

log "→ Logging in to $BASE as $SEMAPHORE_USER"

LOGIN_BODY=$(jq -n \
    --arg u "$SEMAPHORE_USER" \
    --arg p "$SEMAPHORE_PASS" \
    '{auth: $u, password: $p}')

LOGIN_CODE=$(curl -sS -o "$LOGIN_FILE" -w '%{http_code}' \
    -c "$COOKIE_JAR" \
    -H 'Content-Type: application/json' \
    -X POST \
    --data "$LOGIN_BODY" \
    "$BASE/api/auth/login" || true)

if [[ "$LOGIN_CODE" != "200" && "$LOGIN_CODE" != "204" ]]; then
    log "✖ Login failed (HTTP $LOGIN_CODE)"
    cat "$LOGIN_FILE" >&2
    exit 3
fi

log "✓ Authenticated"

# ---------------------------------------------------------------------------
# 2. Start task with survey variables
# ---------------------------------------------------------------------------

log "→ Starting template $TEMPLATE_ID in project $PROJECT_ID"
log "→ STACK_NAME=$STACK_NAME"

# Semaphore expects environment as a JSON-encoded STRING.
# This supplies the value to the existing survey variable.
TASK_BODY=$(jq -n \
    --argjson tpl "$TEMPLATE_ID" \
    --arg stack "$STACK_NAME" \
    '{
        template_id: $tpl,
        environment: ({stack_name: $stack} | tojson)
    }')

log "→ Request payload:"
jq . <<< "$TASK_BODY" >&2

START_CODE=$(curl -sS -o "$START_FILE" -w '%{http_code}' \
    -b "$COOKIE_JAR" \
    -H 'Content-Type: application/json' \
    -X POST \
    --data "$TASK_BODY" \
    "$BASE/api/project/$PROJECT_ID/tasks" || true)

if [[ "$START_CODE" != "200" && "$START_CODE" != "201" ]]; then
    log "✖ Failed to start task (HTTP $START_CODE)"
    cat "$START_FILE" >&2
    exit 3
fi

TASK_ID=$(jq -r '.id // empty' "$START_FILE")

if [[ -z "$TASK_ID" ]]; then
    log "✖ Could not parse task ID"
    cat "$START_FILE" >&2
    exit 3
fi

log "✓ Task started: id=$TASK_ID"

TASK_URL="$BASE/api/project/$PROJECT_ID/tasks/$TASK_ID"
OUTPUT_URL="$TASK_URL/output"
RAW_OUTPUT_URL="$TASK_URL/raw_output"

# ---------------------------------------------------------------------------
# 3. Poll task until completion
# ---------------------------------------------------------------------------

START_TIME=$SECONDS

while :; do

    if (( SECONDS - START_TIME >= MAX_WAIT )); then
        log "✖ Task timed out after ${MAX_WAIT}s"
        exit 1
    fi

    if ! curl -fsS -b "$COOKIE_JAR" "$TASK_URL" -o "$TASK_FILE"; then
        log "✖ Failed to retrieve task status"
        sleep "$POLL_INTERVAL"
        continue
    fi

    STATUS=$(jq -r '.status // empty' "$TASK_FILE")

    case "$STATUS" in
        success|failed|error|stopped)
            break
            ;;

        waiting|running|starting|queued|preparing|"")
            printf '… status=%s\n' "${STATUS:-unknown}" >&2
            sleep "$POLL_INTERVAL"
            ;;

        *)
            printf '… status=%s (waiting)\n' "$STATUS" >&2
            sleep "$POLL_INTERVAL"
            ;;
    esac
done

# ---------------------------------------------------------------------------
# 4. Retrieve actual task output
# ---------------------------------------------------------------------------

echo
echo "=== Semaphore task $TASK_ID finished: $STATUS ==="
echo

OUTPUT_CODE=$(curl -sS -o "$OUTPUT_FILE" -w '%{http_code}' \
    -b "$COOKIE_JAR" "$OUTPUT_URL" || true)

if [[ "$OUTPUT_CODE" == "200" ]]; then
    jq -r 'if type == "array"
        then .[] | .output // empty
        else .output // empty
        end' "$OUTPUT_FILE"
else
    log "⚠ Could not retrieve formatted output (HTTP $OUTPUT_CODE)"
fi

# Try raw output if formatted output is empty
if [[ ! -s "$OUTPUT_FILE" ]] ||
   ! jq -e 'if type == "array"
       then any(.[]; ((.output // "") != ""))
       else ((.output // "") != "")
       end' "$OUTPUT_FILE" >/dev/null 2>&1; then

    echo
    log "--- Trying raw task output ---"

    RAW_CODE=$(curl -sS -o "$RAW_OUTPUT_FILE" -w '%{http_code}' \
        -b "$COOKIE_JAR" "$RAW_OUTPUT_URL" || true)

    if [[ "$RAW_CODE" == "200" ]]; then
        cat "$RAW_OUTPUT_FILE"
    else
        log "⚠ Raw output unavailable (HTTP $RAW_CODE)"
    fi
fi

# ---------------------------------------------------------------------------
# 5. Print task metadata for diagnostics
# ---------------------------------------------------------------------------

echo
echo "--- Task metadata ---"

jq '{
    id,
    template_id,
    status,
    start,
    end,
    message,
    build_task_id
}' "$TASK_FILE"

if [[ "$STATUS" != "success" ]]; then
    echo
    log "✖ Semaphore task did not finish successfully."
    log "Task URL: $BASE/project/$PROJECT_ID/history?t=$TASK_ID"
    exit 1
fi

log "✓ Task completed successfully"
exit 0
