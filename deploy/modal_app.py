"""Modal.com serverless GPU deployment for Openjutsu (scale-to-zero).

Designed for low-volume daily marketing video generation (e.g. 1 video/day up to 30s).
The container scales to zero automatically after `scaledown_window` seconds of idle time,
keeping monthly costs inside Modal's $30/mo Starter free credits (~$5/mo storage + GPU on-demand).

Quick start:
  1. pip install modal && modal setup
  2. modal secret create openjutsu-auth OPENJUTSU_PASSWORD="your-strong-password-12plus-chars"
  3. modal run deploy/modal_app.py        # downloads ~51 GB models to persistent Modal Volume (CPU-only)
  4. modal deploy deploy/modal_app.py     # deploys serverless web UI + ComfyUI GPU backend
"""
from __future__ import annotations

import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import modal

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_NAME = "openjutsu-cloud"
VOLUME_NAME = "openjutsu-data"
SECRET_NAME = "openjutsu-auth"

# Default to Nvidia L4 (24 GB VRAM, ~$0.80/hr) — cheapest 24 GB GPU on Modal.
# Override with e.g. OPENJUTSU_MODAL_GPU=L40S for 48 GB VRAM (~2x faster).
GPU_TYPE = os.environ.get("OPENJUTSU_MODAL_GPU", "L4")
SCALEDOWN_SECONDS = int(os.environ.get("OPENJUTSU_SCALEDOWN_SECONDS", "600"))

CUSTOM_NODE_REPOS = [
    "https://github.com/ethanfel/ComfyUI-MiniMaxH3-Context-Loop.git",
    "https://github.com/Adudeguyman/ComfyUI-Fantastic-MiniMaxH3-PromptBuilder.git",
    "https://github.com/kijai/ComfyUI-KJNodes.git",
    "https://github.com/Fannovel16/comfyui_controlnet_aux.git",
    "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git",
    "https://github.com/D3voz/comfyui-h3-oneclick-swap.git",
    "https://github.com/D3voz/comfyui-image-detail-contrast.git",
    "https://github.com/orion4d/Orion4D_FXMax.git",
    "https://github.com/D3voz/comfyui-websocket-reconnect-fix.git",
    "https://github.com/Enderfga/SolarRenderer.git",
]

OPTIONAL_FACE_FIX_REPOS = [
    "https://github.com/Carasibana/ComfyUI-H3-FaceRefine.git",
    "https://github.com/ltdrdata/ComfyUI-Impact-Pack.git",
]


def _build_image() -> modal.Image:
    include_face_fix = os.environ.get("OPENJUTSU_WITH_FACE_FIX", "0") == "1"
    repos = CUSTOM_NODE_REPOS + (OPTIONAL_FACE_FIX_REPOS if include_face_fix else [])
    clone_cmds = " && ".join(f"git clone --depth 1 {url}" for url in repos)

    return (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install(
            "git",
            "ffmpeg",
            "curl",
            "ca-certificates",
            "libgl1",
            "libglib2.0-0",
            "build-essential",
        )
        .run_commands(
            "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -",
            "apt-get install -y nodejs",
            "rm -rf /var/lib/apt/lists/*",
        )
        # CUDA 13.0 PyTorch wheels are required for ComfyUI's int8_convrot / comfy-kitchen kernels.
        .run_commands(
            "pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130",
            "git clone --depth 1 https://github.com/Comfy-Org/ComfyUI.git /opt/ComfyUI",
            "pip install --no-cache-dir -r /opt/ComfyUI/requirements.txt --extra-index-url https://download.pytorch.org/whl/cu130",
            "pip install --no-cache-dir --upgrade --force-reinstall torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130",
        )
        .run_commands(
            f"mkdir -p /opt/ComfyUI/custom_nodes && cd /opt/ComfyUI/custom_nodes && {clone_cmds}",
            "for req in /opt/ComfyUI/custom_nodes/*/requirements.txt; do "
            "if [ -f \"$req\" ]; then pip install --no-cache-dir -r \"$req\" || true; fi; done",
            "pip install --no-cache-dir comfy-api-proxy==0.1.6 huggingface_hub hf_transfer imageio-ffmpeg",
        )
        .add_local_dir(
            str(REPO_ROOT),
            remote_path="/opt/openjutsu",
            copy=True,
            ignore=[
                ".git",
                "node_modules",
                ".next",
                ".venv-proxy",
                ".env.local",
                "public/_test",
                "public/_montage_test",
            ],
        )
        .run_commands(
            "git -C /opt/ComfyUI/custom_nodes/SolarRenderer apply /opt/openjutsu/patches/solarrenderer-long-clips.patch",
            "python3 /opt/openjutsu/patch_proxy.py",
            "cd /opt/openjutsu && npm ci && (npm run build || npm run build)",
        )
        .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "NEXT_TELEMETRY_DISABLED": "1"})
    )


app = modal.App(APP_NAME)
vol = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True, version=2)
image = _build_image()


@app.function(
    image=image,
    volumes={"/vol": vol},
    cpu=4.0,
    memory=8192,
    timeout=7200,
)
def download_models(
    fast_describer: bool = False,
    with_picture: bool = False,
    with_face_fix: bool = False,
) -> None:
    """Download required model weights into the persistent Modal Volume (runs on cheap CPU)."""
    models_dir = pathlib.Path("/vol/models")
    models_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "/opt/openjutsu/deploy/download_models.py", "--models-dir", str(models_dir)]
    if fast_describer:
        cmd.append("--fast-describer")
    if with_picture:
        cmd.append("--with-picture")
    if with_face_fix:
        cmd.append("--with-face-fix")
    subprocess.run(cmd, check=True)
    vol.commit()


def _link_volume_dirs() -> None:
    for sub in ("models", "output", "input", "temp", "hf_cache"):
        vol_sub = pathlib.Path("/vol") / sub
        vol_sub.mkdir(parents=True, exist_ok=True)
        if sub == "hf_cache":
            continue
        target = pathlib.Path("/opt/ComfyUI") / sub
        if target.is_symlink():
            continue
        if target.exists():
            for item in target.iterdir():
                dest = vol_sub / item.name
                if not dest.exists():
                    shutil.move(str(item), str(dest))
            shutil.rmtree(target)
        target.symlink_to(vol_sub)

    for folder in (
        "diffusion_models",
        "loras",
        "text_encoders",
        "vae",
        "checkpoints",
        "sams",
        "ultralytics/bbox",
        "ultralytics/segm",
        "insightface/models",
    ):
        (pathlib.Path("/vol/models") / folder).mkdir(parents=True, exist_ok=True)


def _wait_for_http(url: str, timeout_s: float = 240.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                if resp.status < 500:
                    return
        except Exception:
            time.sleep(1.5)
    raise TimeoutError(f"Service did not become ready at {url} within {timeout_s}s")


def _wait_for_port(port: int, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.5)
    raise TimeoutError(f"Port {port} did not open within {timeout_s}s")


@app.cls(
    image=image,
    gpu=GPU_TYPE,
    cpu=4.0,
    memory=65536,
    volumes={"/vol": vol},
    secrets=[modal.Secret.from_name(SECRET_NAME)],
    max_containers=1,
    scaledown_window=SCALEDOWN_SECONDS,
    timeout=3600,
)
@modal.concurrent(max_inputs=100)
class OpenjutsuServer:
    @modal.enter()
    def start_backends(self) -> None:
        password = os.environ.get("OPENJUTSU_PASSWORD", "")
        if len(password) < 12:
            raise RuntimeError(
                f"Modal secret '{SECRET_NAME}' must define OPENJUTSU_PASSWORD with at least 12 characters."
            )

        _link_volume_dirs()
        os.environ["HF_HOME"] = "/vol/hf_cache"

        self.comfy_proc = subprocess.Popen(
            [
                sys.executable,
                "/opt/ComfyUI/main.py",
                "--listen",
                "127.0.0.1",
                "--port",
                "8188",
                "--preview-method",
                "auto",
            ],
            cwd="/opt/ComfyUI",
        )
        _wait_for_http("http://127.0.0.1:8188/queue", timeout_s=240.0)

        self.proxy_proc = subprocess.Popen(
            ["comfy-api-proxy", "run", "--max-upload-mb", "500"],
            cwd="/opt/openjutsu",
        )
        _wait_for_port(8189, timeout_s=60.0)

    @modal.exit()
    def stop_backends(self) -> None:
        for proc in (getattr(self, "next_proc", None), getattr(self, "proxy_proc", None), getattr(self, "comfy_proc", None)):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        vol.commit()

    @modal.web_server(3000, startup_timeout=300)
    def serve(self) -> None:
        env = {
            **os.environ,
            "COMFY_BASE_URL": "http://127.0.0.1:8189",
            "COMFYUI_URL": "http://127.0.0.1:8188",
            "OPENJUTSU_TRUST_PROXY": "1",
            "NEXT_TELEMETRY_DISABLED": "1",
        }
        self.next_proc = subprocess.Popen(
            ["/opt/openjutsu/node_modules/.bin/next", "start", "--hostname", "0.0.0.0", "--port", "3000"],
            cwd="/opt/openjutsu",
            env=env,
        )


@app.local_entrypoint()
def main(
    fast_describer: bool = False,
    with_picture: bool = False,
    with_face_fix: bool = False,
) -> None:
    """Download models into the persistent Volume. Run `modal deploy deploy/modal_app.py` after."""
    download_models.remote(
        fast_describer=fast_describer,
        with_picture=with_picture,
        with_face_fix=with_face_fix,
    )
    print("\nModelos prontos! Agora rode:\n  modal deploy deploy/modal_app.py")
