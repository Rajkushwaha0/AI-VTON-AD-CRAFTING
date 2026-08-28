"""Resize / encode images for Kling Omni (base64, no data-URL prefix)."""

from __future__ import annotations

import base64
import io
import os

from PIL import Image, ImageOps

logger_name = __name__


def encode_image_with_resize(image_path: str, max_dim: int = 1536) -> str:
    """
    Kling requirements: min 300px, aspect ratio in [0.4, 2.5], dims multiple of 32.
    """
    img = Image.open(image_path)
    img = img.convert("RGB")
    w, h = img.size

    ratio = w / h
    if ratio < 0.4:
        target_h = int(w / 0.4)
        img = ImageOps.fit(img, (w, target_h), Image.Resampling.LANCZOS)
        w, h = img.size
    elif ratio > 2.5:
        target_w = int(h * 2.5)
        img = ImageOps.fit(img, (target_w, h), Image.Resampling.LANCZOS)
        w, h = img.size

    min_curr = min(w, h)
    if min_curr < 300:
        scale = 300 / min_curr
        w, h = int(w * scale), int(h * scale)

    max_curr = max(w, h)
    if max_curr > max_dim:
        scale = max_dim / max_curr
        w, h = int(w * scale), int(h * scale)

    final_w = max((w // 32) * 32, 320)
    final_h = max((h // 32) * 32, 320)

    if (final_w, final_h) != img.size:
        img = img.resize((final_w, final_h), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def encode_paths(paths: list[str], max_dim: int = 1536) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        out.append({"image_url": encode_image_with_resize(p, max_dim=max_dim)})
    return out
