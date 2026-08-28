"""
Direct Kling v3.0 / v1.6 Image2Video Client.
Handles:
1. Submitting 5s single-shot Image-to-Video generation requests.
2. Polling task status.
3. Downloading completed MP4 video files.
"""

from __future__ import annotations

import os
import time
import json
import base64
import logging
from typing import Optional, Tuple, Dict, Any
import requests

from .auth import api_base_url, api_headers, kling_configured

logger = logging.getLogger(__name__)

SUPPORTED_KLING_MODELS = [
    "kling-v3",
    "kling-v1-6",
    "kling-v1-5",
    "kling-v1"
]


def _encode_image(image_path: str) -> str:
    """Reads image file and returns base64 data string."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def submit_image2video(
    image_path: str,
    prompt: str,
    *,
    duration: int = 5,
    model_name: Optional[str] = None,
    aspect_ratio: str = "9:16",
    mode: str = "pro",
    sound: str = "off",
    cfg_scale: float = 0.5
) -> Tuple[str, Dict[str, Any]]:
    """
    Submits a 5-second image-to-video task to Kling.
    Returns (task_id, request_record).
    """
    if not kling_configured():
        raise RuntimeError("KLING_API_KEY is not configured in environment.")

    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found for video conditioning: {image_path}")

    image_b64 = _encode_image(image_path)
    primary_model = (model_name or os.environ.get("KLING_MODEL") or "kling-v3").strip()
    models_to_try = [primary_model] + [m for m in SUPPORTED_KLING_MODELS if m != primary_model]

    base_url = api_base_url()
    # Support both /videos/image2video and /v1/videos/image2video endpoints
    endpoint = f"{base_url}/videos/image2video"

    last_error = None
    for model in models_to_try:
        payload = {
            "model_name": model,
            "image": image_b64,
            "prompt": prompt[:1500],
            "duration": str(duration),
            "mode": mode,
            "sound": sound,
            "cfg_scale": cfg_scale,
            "aspect_ratio": aspect_ratio
        }

        try:
            logger.info(f"[Kling Image2Video] POST {endpoint} model={model} duration={duration}s aspect={aspect_ratio}")
            resp = requests.post(endpoint, headers=api_headers(), json=payload, timeout=60)
            
            if resp.status_code == 200:
                result = resp.json()
                task_id = _extract_task_id(result)
                record = {
                    "task_id": task_id,
                    "model_name": model,
                    "duration": duration,
                    "aspect_ratio": aspect_ratio,
                    "image_source": os.path.basename(image_path),
                    "prompt_preview": prompt[:150],
                    "submitted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "submitted"
                }
                return task_id, record
            else:
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                logger.warning(f"[Kling] Model '{model}' returned {resp.status_code}. Attempting fallback...")
        except Exception as e:
            last_error = str(e)
            logger.warning(f"[Kling] Request failed for model '{model}': {e}")

    raise RuntimeError(f"Kling Image2Video submission failed across all models: {last_error}")


def poll_image2video_task(
    task_id: str,
    *,
    poll_interval: int = 8,
    max_wait_time: int = 360
) -> Dict[str, Any]:
    """Polls Image2Video task status until completed, failed, or timed out."""
    base_url = api_base_url()
    endpoint = f"{base_url}/videos/image2video/{task_id}"
    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        if elapsed > max_wait_time:
            return {
                "task_id": task_id,
                "status": "timeout",
                "error": f"Polling timed out after {max_wait_time}s"
            }

        try:
            resp = requests.get(endpoint, headers=api_headers(), timeout=45)
            if resp.status_code == 200:
                data = resp.json()
                status = _extract_status(data)
                logger.info(f"[Kling Image2Video] task {task_id} status={status} (elapsed: {int(elapsed)}s)")

                if status in ("completed", "done", "success", "succeed"):
                    video_url = _extract_video_url(data)
                    return {
                        "task_id": task_id,
                        "status": "completed",
                        "video_url": video_url,
                        "data": data
                    }
                elif status in ("failed", "error", "cancelled"):
                    err_msg = "Task failed on Kling backend"
                    if isinstance(data.get("data"), dict):
                        err_msg = data["data"].get("task_status_msg") or data["data"].get("message") or err_msg
                    return {
                        "task_id": task_id,
                        "status": "failed",
                        "error": err_msg
                    }
            elif resp.status_code in (401, 403):
                resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"[Kling] Poll network warning: {e}")

        time.sleep(poll_interval)


def download_kling_video(video_url: str, destination_path: str) -> str:
    """Downloads remote MP4 video to local disk."""
    os.makedirs(os.path.dirname(destination_path) or ".", exist_ok=True)
    with requests.get(video_url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with open(destination_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    logger.info(f"[Kling] Saved video to {destination_path}")
    return destination_path


def _extract_task_id(res: dict) -> str:
    data = res.get("data")
    if isinstance(data, dict) and data.get("task_id"):
        return str(data["task_id"])
    for k in ("task_id", "request_id", "id"):
        if res.get(k):
            return str(res[k])
    raise ValueError(f"No task_id in Kling response: {res}")


def _extract_status(res: dict) -> str:
    data = res.get("data")
    if isinstance(data, dict):
        status = data.get("task_status") or data.get("status") or data.get("state") or ""
    else:
        status = res.get("task_status") or res.get("status") or res.get("state") or ""
    return str(status).lower().strip()


def _extract_video_url(res: dict) -> Optional[str]:
    data = res.get("data")
    if isinstance(data, dict):
        url = data.get("video_url") or data.get("video") or data.get("url")
        task_res = data.get("task_result")
        if not url and isinstance(task_res, dict):
            vids = task_res.get("videos") or []
            if vids and isinstance(vids[0], dict):
                url = vids[0].get("url") or vids[0].get("video_url")
        if url:
            return url
    return res.get("video_url") or res.get("url")
