#!/usr/bin/env python3
"""Download ComfyUI models required by Openjutsu into a target models directory.

By default, downloads the `core` set (~51 GB) needed for:
- Simple motion transfer (single character swap)
- Advanced motion transfer & Montage (multi-character swap)
- Object swap (Change / Remove / Background / Add)
- Fix colours (post-processing)

Optional flags add SolarRenderer weights (`--with-picture`) or Fix faces weights
(`--with-face-fix`, note non-commercial InsightFace license in README.md).
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys
import urllib.request
from dataclasses import dataclass

os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")


@dataclass(frozen=True)
class ModelSpec:
    folder: str
    filename: str
    repo_id: str | None = None
    repo_path: str | None = None
    repo_type: str = "model"
    direct_url: str | None = None
    size_hint: str = ""


CORE_MODELS: list[ModelSpec] = [
    ModelSpec(
        folder="diffusion_models",
        filename="minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        repo_id="Comfy-Org/MiniMax-H3",
        repo_path="diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        size_hint="21.0 GB",
    ),
    ModelSpec(
        folder="loras",
        filename="minimax_h3_DMAD_4step_full_lora_avg_rank_39_bf16.safetensors",
        repo_id="Kijai/MiniMax-H3-experimental",
        repo_path="loras/minimax_h3_DMAD_4step_full_lora_avg_rank_39_bf16.safetensors",
        size_hint="0.37 GB",
    ),
    ModelSpec(
        folder="text_encoders",
        filename="qwen3vl_32b_minimax_h3_int4_convrot.safetensors",
        repo_id="Merserk/MiniMax-H3-INT4-ConvRot",
        repo_path="qwen3vl_32b_minimax_h3_int4_convrot.safetensors",
        size_hint="15.0 GB",
    ),
    ModelSpec(
        folder="vae",
        filename="minimax_h3_video_vae_int8_convrot.safetensors",
        repo_id="Comfy-Org/MiniMax-H3",
        repo_path="vae/minimax_h3_video_vae_int8_convrot.safetensors",
        size_hint="2.81 GB",
    ),
    ModelSpec(
        folder="vae",
        filename="minimax_h3_audio_vae_fp32.safetensors",
        repo_id="Comfy-Org/MiniMax-H3",
        repo_path="vae/minimax_h3_audio_vae_fp32.safetensors",
        size_hint="0.61 GB",
    ),
    ModelSpec(
        folder="checkpoints",
        filename="sam3.1_multiplex_fp16.safetensors",
        repo_id="Comfy-Org/sam3.1",
        repo_path="checkpoints/sam3.1_multiplex_fp16.safetensors",
        size_hint="1.75 GB",
    ),
]

DESCRIBER_8B = ModelSpec(
    folder="text_encoders",
    filename="qwen3vl_8b_int8_convrot.safetensors",
    repo_id="Comfy-Org/Qwen3-VL",
    repo_path="text_encoders/qwen3vl_8b_int8_convrot.safetensors",
    size_hint="9.35 GB",
)

DESCRIBER_4B = ModelSpec(
    folder="text_encoders",
    filename="qwen3vl_4b_fp8_scaled.safetensors",
    repo_id="Comfy-Org/Qwen3-VL",
    repo_path="text_encoders/qwen3vl_4b_fp8_scaled.safetensors",
    size_hint="5.24 GB",
)

PICTURE_MODELS: list[ModelSpec] = [
    ModelSpec(
        folder="loras",
        filename="solarrenderer_4step_lora.safetensors",
        repo_id="Enderfga/SolarRenderer",
        repo_path="solarrenderer_4step_lora.safetensors",
        size_hint="SolarRenderer 4-step LoRA",
    ),
    ModelSpec(
        folder="loras",
        filename="solarrenderer_control_lora.safetensors",
        repo_id="Enderfga/SolarRenderer",
        repo_path="solarrenderer_control_lora.safetensors",
        size_hint="SolarRenderer control LoRA",
    ),
]

FACE_FIX_MODELS: list[ModelSpec] = [
    ModelSpec(
        folder="ultralytics/bbox",
        filename="face_yolov8m.pt",
        repo_id="Bingsu/adetailer",
        repo_path="face_yolov8m.pt",
        size_hint="YOLO face detector",
    ),
    ModelSpec(
        folder="ultralytics/segm",
        filename="person_yolov8m-seg.pt",
        repo_id="Bingsu/adetailer",
        repo_path="person_yolov8m-seg.pt",
        size_hint="YOLO person seg",
    ),
    ModelSpec(
        folder="sams",
        filename="sam_vit_b_01ec64.pth",
        direct_url="https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth",
        size_hint="0.38 GB",
    ),
]


def download_file(spec: ModelSpec, models_dir: pathlib.Path) -> None:
    target_dir = models_dir / spec.folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / spec.filename
    if target.exists() and target.stat().st_size > 0:
        print(f"[skip] {spec.folder}/{spec.filename} already exists ({target.stat().st_size / 1e9:.2f} GB)")
        return

    print(f"[download] {spec.folder}/{spec.filename} ({spec.size_hint})...")
    if spec.repo_id and spec.repo_path:
        from huggingface_hub import hf_hub_download

        cached_path = hf_hub_download(
            repo_id=spec.repo_id,
            filename=spec.repo_path,
            repo_type=spec.repo_type,
        )
        tmp_target = target.with_suffix(target.suffix + ".part")
        shutil.copyfile(cached_path, tmp_target)
        tmp_target.replace(target)
        return

    if spec.direct_url:
        tmp_target = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(spec.direct_url) as resp, open(tmp_target, "wb") as out:
            shutil.copyfileobj(resp, out, length=1024 * 1024)
        tmp_target.replace(target)
        return

    raise ValueError(f"No download source configured for {spec.filename}")


def build_plan(fast_describer: bool, with_picture: bool, with_face_fix: bool) -> list[ModelSpec]:
    plan = list(CORE_MODELS)
    plan.append(DESCRIBER_4B if fast_describer else DESCRIBER_8B)
    if with_picture:
        plan.extend(PICTURE_MODELS)
    if with_face_fix:
        print(
            "[notice] Fix faces uses InsightFace buffalo_l (non-commercial research license). "
            "Do not use Fix faces for paid commercial campaigns without verifying licensing.",
            file=sys.stderr,
        )
        plan.extend(FACE_FIX_MODELS)
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download Openjutsu ComfyUI models.")
    parser.add_argument(
        "--models-dir",
        type=pathlib.Path,
        default=pathlib.Path("/workspace/ComfyUI/models"),
        help="Path to ComfyUI/models directory",
    )
    parser.add_argument(
        "--fast-describer",
        action="store_true",
        help="Use Qwen3-VL 4B FP8 (5.2 GB) instead of 8B INT8 (9.4 GB) to save disk/VRAM",
    )
    parser.add_argument(
        "--with-picture",
        action="store_true",
        help="Also download SolarRenderer LoRAs for From picture mode",
    )
    parser.add_argument(
        "--with-face-fix",
        action="store_true",
        help="Also download Fix faces weights (YOLO + SAM ViT-B)",
    )
    args = parser.parse_args(argv)

    args.models_dir.mkdir(parents=True, exist_ok=True)
    for spec in build_plan(args.fast_describer, args.with_picture, args.with_face_fix):
        download_file(spec, args.models_dir)
    print(f"[done] All selected models are ready in {args.models_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
