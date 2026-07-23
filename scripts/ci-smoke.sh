#!/usr/bin/env bash
set -euo pipefail

api_base="${SYNAPSE_SMOKE_BASE_URL:-http://127.0.0.1:8080}"
python_bin="${PYTHON:-python3}"
tmp_dir="$(mktemp -d)"
cookie_jar="${tmp_dir}/cookies.txt"

cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

json_payload() {
  "$python_bin" -c 'import json, sys; print(json.dumps(json.loads(sys.argv[1])))' "$1"
}

require_json_field() {
  local file="$1"
  local expression="$2"
  "$python_bin" - "$file" "$expression" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)

value = data
for part in sys.argv[2].split("."):
    if isinstance(value, dict) and part in value:
        value = value[part]
    else:
        raise SystemExit(f"missing JSON field: {sys.argv[2]}")

print(value)
PY
}

wait_for_gateway() {
  local deadline=$((SECONDS + 120))
  local health_file="${tmp_dir}/health.json"

  until curl -fsS "${api_base}/healthz" -o "$health_file"; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "Gateway did not become ready before timeout."
      return 1
    fi
    sleep 1
  done

  "$python_bin" - "$health_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)

if payload.get("status") != "ok":
    raise SystemExit(f"unexpected health status: {payload}")
if payload.get("model_provider") != "mock":
    raise SystemExit(f"smoke test requires mock provider: {payload}")
PY
}

wait_for_terminal_task() {
  local task_id="$1"
  local final_file="${tmp_dir}/task-final.json"
  local deadline=$((SECONDS + 90))

  while true; do
    curl -fsS -b "$cookie_jar" "${api_base}/v1/tasks/${task_id}" -o "$final_file"
    local status
    status="$(require_json_field "$final_file" status)"
    case "$status" in
      completed|failed|canceled|paused)
        if [ "$status" != "completed" ]; then
          echo "Task reached non-success terminal state: ${status}"
          cat "$final_file"
          return 1
        fi
        return 0
        ;;
    esac

    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "Task did not reach a terminal state before timeout."
      cat "$final_file"
      return 1
    fi
    sleep 1
  done
}

assert_sse_contains_terminal_events() {
  local events_file="$1"

  "$python_bin" - "$events_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    raw = handle.read()

has_token = False
has_completed = False
terminal_completed = False

for frame in raw.split("\n\n"):
    event_name = ""
    data_lines = []
    for line in frame.splitlines():
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())

    payload = {}
    if data_lines:
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            payload = {}

    event_type = payload.get("type") or event_name
    if event_type == "token" and payload.get("token"):
        has_token = True
    if event_type == "completed" or event_name == "completed":
        has_completed = True
    if event_name == "terminal" and payload.get("status") == "completed":
        terminal_completed = True

missing = []
if not has_token:
    missing.append("token")
if not has_completed:
    missing.append("completed")
if not terminal_completed:
    missing.append("terminal completed")
if missing:
    raise SystemExit(f"SSE stream missing: {', '.join(missing)}\n{raw}")
PY
}

wait_for_gateway

username="ci-smoke-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
password="ci-smoke-password"

auth_payload="$(json_payload "{\"username\":\"${username}\",\"password\":\"${password}\"}")"
register_code="$(
  curl -sS -o "${tmp_dir}/register.json" -w "%{http_code}" \
    -H "Content-Type: application/json" \
    -d "$auth_payload" \
    "${api_base}/v1/auth/register"
)"
if [ "$register_code" != "201" ] && [ "$register_code" != "409" ]; then
  echo "Register failed with HTTP ${register_code}"
  cat "${tmp_dir}/register.json"
  exit 1
fi

login_code="$(
  curl -sS -o "${tmp_dir}/login.json" -w "%{http_code}" \
    -c "$cookie_jar" -b "$cookie_jar" \
    -H "Content-Type: application/json" \
    -d "$auth_payload" \
    "${api_base}/v1/auth/login"
)"
if [ "$login_code" != "200" ]; then
  echo "Login failed with HTTP ${login_code}"
  cat "${tmp_dir}/login.json"
  exit 1
fi

task_payload="$(json_payload '{"prompt":"Summarize this short CI smoke note in one sentence.","metadata":{"agent_enabled":"true","memory_write_enabled":"false"}}')"
create_code="$(
  curl -sS -o "${tmp_dir}/task.json" -w "%{http_code}" \
    -b "$cookie_jar" \
    -H "Content-Type: application/json" \
    -d "$task_payload" \
    "${api_base}/v1/tasks"
)"
if [ "$create_code" != "201" ]; then
  echo "Task creation failed with HTTP ${create_code}"
  cat "${tmp_dir}/task.json"
  exit 1
fi

task_id="$(require_json_field "${tmp_dir}/task.json" id)"
events_file="${tmp_dir}/events.sse"

curl -fsS -N --max-time 90 -b "$cookie_jar" \
  "${api_base}/v1/tasks/${task_id}/events" > "$events_file"

wait_for_terminal_task "$task_id"
assert_sse_contains_terminal_events "$events_file"

echo "Docker smoke test completed for task ${task_id}."
