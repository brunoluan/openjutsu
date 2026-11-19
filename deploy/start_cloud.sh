#!/usr/bin/env bash
set -euo pipefail

# Start ComfyUI, comfy-api-proxy, Openjutsu Next.js server, and Cloudflare HTTPS Tunnel.
# Includes an idle watchdog (IDLE_STOP_MINUTES=20 by default) that shuts down or stops
# the RunPod/Vast.ai instance after inactivity so you never pay for overnight idle GPU time.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PATH="$REPO_ROOT/.bin:$PATH"

if [[ -d "/workspace" && -w "/workspace" ]]; then
  WORKSPACE="/workspace"
else
  WORKSPACE="$HOME"
fi

COMFYUI_DIR="${COMFYUI_DIR:-$WORKSPACE/ComfyUI}"
COMFY_PYTHON="${COMFY_PYTHON:-}"
if [[ -z "$COMFY_PYTHON" ]]; then
  if [[ -x "$COMFYUI_DIR/.venv/bin/python" ]]; then
    COMFY_PYTHON="$COMFYUI_DIR/.venv/bin/python"
  elif [[ -x "/opt/conda/bin/python" ]]; then
    COMFY_PYTHON="/opt/conda/bin/python"
  else
    COMFY_PYTHON="python3"
  fi
fi

IDLE_STOP_MINUTES="${IDLE_STOP_MINUTES:-20}"
LOG_DIR="${LOG_DIR:-/tmp/openjutsu-logs}"
mkdir -p "$LOG_DIR"

cd "$REPO_ROOT"

if [[ ! -f ".env.local" ]]; then
  cp ".env.example" ".env.local"
fi

# Load .env.local safely without overriding explicit environment variables.
set -a
# shellcheck disable=SC1091
source ".env.local"
set +a

if [[ -z "${OPENJUTSU_PASSWORD:-}" || "${#OPENJUTSU_PASSWORD}" -lt 12 ]]; then
  OPENJUTSU_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(15))')"
  # Update or append OPENJUTSU_PASSWORD in .env.local
  if grep -q '^OPENJUTSU_PASSWORD=' .env.local; then
    sed -i "s|^OPENJUTSU_PASSWORD=.*|OPENJUTSU_PASSWORD=${OPENJUTSU_PASSWORD}|" .env.local
  else
    printf '\nOPENJUTSU_PASSWORD=%s\n' "$OPENJUTSU_PASSWORD" >> .env.local
  fi
  export OPENJUTSU_PASSWORD
fi

export COMFY_BASE_URL="${COMFY_BASE_URL:-http://127.0.0.1:8189}"
export COMFYUI_URL="${COMFYUI_URL:-http://127.0.0.1:8188}"
export NEXT_TELEMETRY_DISABLED=1

PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM

echo "==> Starting ComfyUI on 127.0.0.1:8188..."
(cd "$COMFYUI_DIR" && exec "$COMFY_PYTHON" main.py --listen 127.0.0.1 --port 8188 --preview-method auto) \
  >"$LOG_DIR/comfyui.log" 2>&1 &
PIDS+=($!)

for _ in $(seq 1 120); do
  if curl -fsS -m 2 http://127.0.0.1:8188/queue >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS -m 2 http://127.0.0.1:8188/queue >/dev/null || {
  echo "ComfyUI failed to start. Check $LOG_DIR/comfyui.log" >&2
  tail -n 40 "$LOG_DIR/comfyui.log" >&2 || true
  exit 1
}

echo "==> Starting comfy-api-proxy on 127.0.0.1:8189..."
"$REPO_ROOT/.venv-proxy/bin/comfy-api-proxy" run --max-upload-mb 500 \
  >"$LOG_DIR/proxy.log" 2>&1 &
PIDS+=($!)

sleep 2

echo "==> Starting Openjutsu Next.js server on 0.0.0.0:3000..."
npx next start --hostname 0.0.0.0 --port 3000 \
  >"$LOG_DIR/next.log" 2>&1 &
PIDS+=($!)

for _ in $(seq 1 30); do
  if curl -fsS -m 2 -o /dev/null http://127.0.0.1:3000/ 2>/dev/null || [[ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/)" == "401" ]]; then
    break
  fi
  sleep 1
done

CLOUDFLARED_BIN="$(command -v cloudflared || echo "$REPO_ROOT/.bin/cloudflared")"
echo "==> Starting Cloudflare HTTPS Tunnel..."
"$CLOUDFLARED_BIN" tunnel --no-autoupdate --url http://127.0.0.1:3000 \
  >"$LOG_DIR/tunnel.log" 2>&1 &
PIDS+=($!)

TUNNEL_URL=""
for _ in $(seq 1 30); do
  TUNNEL_URL="$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$LOG_DIR/tunnel.log" | head -n1 || true)"
  if [[ -n "$TUNNEL_URL" ]]; then
    break
  fi
  sleep 1
done

echo ""
echo "================================================================="
echo "  Openjutsu rodando na nuvem!"
echo "  URL HTTPS : ${TUNNEL_URL:-http://<IP_DO_SERVIDOR>:3000}"
echo "  Usuário   : openjutsu"
echo "  Senha     : ${OPENJUTSU_PASSWORD}"
echo "  Logs      : $LOG_DIR"
if [[ "$IDLE_STOP_MINUTES" -gt 0 ]]; then
  echo "  Auto-stop : após ${IDLE_STOP_MINUTES} min sem jobs na fila"
fi
echo "================================================================="
echo ""

# Idle watchdog: stops the pod/instance (or exits) after IDLE_STOP_MINUTES of empty ComfyUI queue.
if [[ "$IDLE_STOP_MINUTES" -gt 0 ]]; then
  idle_ticks=0
  saw_job=0
  while true; do
    sleep 60
    queue_json="$(curl -fsS -m 3 http://127.0.0.1:8188/queue 2>/dev/null || echo '{}')"
    busy="$(python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(len(d.get("queue_running", [])) + len(d.get("queue_pending", [])))
except Exception:
    print(0)
' <<<"$queue_json")"

    if [[ "$busy" -gt 0 ]]; then
      saw_job=1
      idle_ticks=0
    elif [[ "$saw_job" -eq 1 ]]; then
      idle_ticks=$((idle_ticks + 1))
      if [[ "$idle_ticks" -ge "$IDLE_STOP_MINUTES" ]]; then
        echo "[watchdog] ComfyUI idle for ${IDLE_STOP_MINUTES} minutes after job completion. Stopping..."
        if [[ -n "${RUNPOD_POD_ID:-}" ]] && command -v runpodctl >/dev/null 2>&1; then
          runpodctl stop pod "$RUNPOD_POD_ID" || true
        elif [[ -n "${CONTAINER_ID:-}" ]] && command -v vastai >/dev/null 2>&1; then
          vastai stop instance "$CONTAINER_ID" || true
        fi
        exit 0
      fi
    fi
  done
else
  wait
fi
