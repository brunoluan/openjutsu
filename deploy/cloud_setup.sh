#!/usr/bin/env bash
set -euo pipefail

# Turnkey setup for Openjutsu + ComfyUI on Vast.ai, RunPod, or any Linux NVIDIA GPU VM.
# Usage:
#   bash deploy/cloud_setup.sh [--fast-describer] [--with-picture] [--with-face-fix]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -d "/workspace" && -w "/workspace" ]]; then
  WORKSPACE="/workspace"
else
  WORKSPACE="$HOME"
fi

COMFYUI_DIR="${COMFYUI_DIR:-$WORKSPACE/ComfyUI}"
COMFY_PYTHON="${COMFY_PYTHON:-}"
WITH_FACE_FIX=0
MODEL_ARGS=()

for arg in "$@"; do
  case "$arg" in
    --fast-describer|--with-picture)
      MODEL_ARGS+=("$arg")
      ;;
    --with-face-fix)
      WITH_FACE_FIX=1
      MODEL_ARGS+=("$arg")
      ;;
    -h|--help)
      echo "Usage: bash deploy/cloud_setup.sh [--fast-describer] [--with-picture] [--with-face-fix]"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

echo "==> Repo root: $REPO_ROOT"
echo "==> ComfyUI dir: $COMFYUI_DIR"

ensure_node() {
  if command -v node >/dev/null 2>&1; then
    local major
    major="$(node -p 'process.versions.node.split(".")[0]')"
    if [[ "$major" -ge 22 ]]; then
      echo "[ok] Node.js $(node -v)"
      return
    fi
  fi
  echo "==> Installing Node.js 22 LTS into $REPO_ROOT/.bin/node..."
  local arch="x64"
  [[ "$(uname -m)" == "aarch64" ]] && arch="arm64"
  local ver="v22.14.0"
  local dist="node-${ver}-linux-${arch}"
  mkdir -p "$REPO_ROOT/.bin"
  curl -fsSL "https://nodejs.org/dist/${ver}/${dist}.tar.xz" -o "/tmp/${dist}.tar.xz"
  tar -xf "/tmp/${dist}.tar.xz" -C "$REPO_ROOT/.bin"
  rm -f "/tmp/${dist}.tar.xz"
  ln -sf "$REPO_ROOT/.bin/${dist}/bin/node" "$REPO_ROOT/.bin/node"
  ln -sf "$REPO_ROOT/.bin/${dist}/bin/npm" "$REPO_ROOT/.bin/npm"
  ln -sf "$REPO_ROOT/.bin/${dist}/bin/npx" "$REPO_ROOT/.bin/npx"
  export PATH="$REPO_ROOT/.bin:$PATH"
  echo "[ok] Installed Node.js $(node -v)"
}

ensure_cloudflared() {
  if command -v cloudflared >/dev/null 2>&1 || [[ -x "$REPO_ROOT/.bin/cloudflared" ]]; then
    echo "[ok] cloudflared ready"
    return
  fi
  echo "==> Downloading cloudflared binary for HTTPS tunnel..."
  mkdir -p "$REPO_ROOT/.bin"
  local arch="amd64"
  [[ "$(uname -m)" == "aarch64" ]] && arch="arm64"
  curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}" \
    -o "$REPO_ROOT/.bin/cloudflared"
  chmod +x "$REPO_ROOT/.bin/cloudflared"
}

ensure_ffmpeg() {
  if command -v ffmpeg >/dev/null 2>&1; then
    echo "[ok] ffmpeg $(ffmpeg -version | head -n1)"
    return
  fi
  if command -v apt-get >/dev/null 2>&1 && [[ "$(id -u)" -eq 0 ]]; then
    apt-get update -qq && apt-get install -y -qq ffmpeg git curl libgl1 libglib2.0-0
  else
    echo "[warn] ffmpeg not found in PATH; installing imageio-ffmpeg fallback."
  fi
}

ensure_node
ensure_cloudflared
ensure_ffmpeg

if [[ ! -d "$COMFYUI_DIR/.git" ]]; then
  echo "==> Cloning ComfyUI..."
  git clone --depth 1 https://github.com/Comfy-Org/ComfyUI.git "$COMFYUI_DIR"
fi

if [[ -z "$COMFY_PYTHON" ]]; then
  if [[ -x "$COMFYUI_DIR/.venv/bin/python" ]]; then
    COMFY_PYTHON="$COMFYUI_DIR/.venv/bin/python"
  elif [[ -x "/opt/conda/bin/python" ]]; then
    COMFY_PYTHON="/opt/conda/bin/python"
  else
    echo "==> Creating ComfyUI virtualenv at $COMFYUI_DIR/.venv..."
    python3 -m venv "$COMFYUI_DIR/.venv"
    COMFY_PYTHON="$COMFYUI_DIR/.venv/bin/python"
  fi
fi

echo "==> Using ComfyUI Python: $COMFY_PYTHON"

# Detect NVIDIA driver version to select cu130 (preferred for int8_convrot) vs cu128 fallback.
TORCH_INDEX="https://download.pytorch.org/whl/cu130"
if command -v nvidia-smi >/dev/null 2>&1; then
  DRIVER_MAJOR="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1 | cut -d. -f1 || echo 0)"
  if [[ "${DRIVER_MAJOR:-0}" -lt 580 ]]; then
    echo "[warn] NVIDIA driver ${DRIVER_MAJOR} < 580; using PyTorch cu128 (int8_convrot will run slower without cu130)."
    TORCH_INDEX="https://download.pytorch.org/whl/cu128"
  fi
fi

"$COMFY_PYTHON" -m pip install --upgrade pip
"$COMFY_PYTHON" -m pip install torch torchvision torchaudio --index-url "$TORCH_INDEX"
"$COMFY_PYTHON" -m pip install -r "$COMFYUI_DIR/requirements.txt" --extra-index-url "$TORCH_INDEX"
"$COMFY_PYTHON" -m pip install --upgrade --force-reinstall torch torchvision torchaudio --index-url "$TORCH_INDEX"
"$COMFY_PYTHON" -m pip install "huggingface_hub[hf_transfer]" imageio-ffmpeg

CUSTOM_NODES=(
  "https://github.com/ethanfel/ComfyUI-MiniMaxH3-Context-Loop.git"
  "https://github.com/Adudeguyman/ComfyUI-Fantastic-MiniMaxH3-PromptBuilder.git"
  "https://github.com/kijai/ComfyUI-KJNodes.git"
  "https://github.com/Fannovel16/comfyui_controlnet_aux.git"
  "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git"
  "https://github.com/D3voz/comfyui-h3-oneclick-swap.git"
  "https://github.com/D3voz/comfyui-image-detail-contrast.git"
  "https://github.com/orion4d/Orion4D_FXMax.git"
  "https://github.com/D3voz/comfyui-websocket-reconnect-fix.git"
  "https://github.com/Enderfga/SolarRenderer.git"
)

if [[ "$WITH_FACE_FIX" -eq 1 ]]; then
  CUSTOM_NODES+=(
    "https://github.com/Carasibana/ComfyUI-H3-FaceRefine.git"
    "https://github.com/ltdrdata/ComfyUI-Impact-Pack.git"
  )
fi

mkdir -p "$COMFYUI_DIR/custom_nodes"
for url in "${CUSTOM_NODES[@]}"; do
  name="$(basename "$url" .git)"
  dest="$COMFYUI_DIR/custom_nodes/$name"
  if [[ -d "$dest/.git" ]]; then
    echo "[skip] custom_nodes/$name already present"
  else
    echo "==> Cloning custom node: $name"
    git clone --depth 1 "$url" "$dest"
  fi
  if [[ -f "$dest/requirements.txt" ]]; then
    "$COMFY_PYTHON" -m pip install -r "$dest/requirements.txt" || echo "[warn] Some requirements failed in $name"
  fi
done

if git -C "$COMFYUI_DIR/custom_nodes/SolarRenderer" apply --check "$REPO_ROOT/patches/solarrenderer-long-clips.patch" 2>/dev/null; then
  git -C "$COMFYUI_DIR/custom_nodes/SolarRenderer" apply "$REPO_ROOT/patches/solarrenderer-long-clips.patch"
  echo "[ok] Applied SolarRenderer long-clips patch"
fi

echo "==> Downloading Openjutsu models..."
HF_HUB_ENABLE_HF_TRANSFER=1 "$COMFY_PYTHON" "$REPO_ROOT/deploy/download_models.py" \
  --models-dir "$COMFYUI_DIR/models" \
  "${MODEL_ARGS[@]}"

echo "==> Setting up comfy-api-proxy (.venv-proxy)..."
if [[ ! -x "$REPO_ROOT/.venv-proxy/bin/python" ]]; then
  python3 -m venv "$REPO_ROOT/.venv-proxy"
fi
"$REPO_ROOT/.venv-proxy/bin/pip" install -q comfy-api-proxy==0.1.6
"$REPO_ROOT/.venv-proxy/bin/python" "$REPO_ROOT/patch_proxy.py"

echo "==> Installing and building Openjutsu..."
cd "$REPO_ROOT"
npm ci
npm run build || npm run build

echo ""
echo "================================================================="
echo "Setup completo! Para iniciar o Openjutsu com link HTTPS seguro:"
echo "  COMFYUI_DIR=$COMFYUI_DIR bash $REPO_ROOT/deploy/start_cloud.sh"
echo "================================================================="
