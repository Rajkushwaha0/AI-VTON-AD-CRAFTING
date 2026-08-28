"""Submit, poll, and download Kling Omni videos."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import requests

from .auth import api_base_url, api_headers, kling_configured
from .images import encode_paths
from .prompt import (
    _short_garment,
    build_multi_shot_prompts,
    build_single_omni_prompt,
    metadata_to_context,
)

logger = logging.getLogger(__name__)

KLING_RESULT_BASENAME = "kling_omni_result.json"
KLING_REQUEST_BASENAME = "kling_omni_request.json"
KLING_PROMPTS_TXT_BASENAME = "kling_omni_prompts.txt"
TASK_TYPE_OMNI = "omni-video"


def _extract_task_id(result: dict) -> str:
    data = result.get("data")
    if isinstance(data, dict) and data.get("task_id"):
        return str(data["task_id"])
    for key in ("task_id", "request_id", "id"):
        if result.get(key):
            return str(result[key])
    raise ValueError(f"No task_id in Kling response: {result}")


def _extract_status(result: dict) -> str:
    data = result.get("data")
    if isinstance(data, dict):
        status = data.get("task_status") or data.get("status") or data.get("state") or ""
    else:
        status = result.get("task_status") or result.get("status") or result.get("state") or ""
    return str(status).lower().strip()


def _extract_video_url(result: dict) -> Optional[str]:
    data = result.get("data")
    video_url = None
    if isinstance(data, dict):
        video_url = data.get("video_url") or data.get("video") or data.get("url")
        task_result = data.get("task_result")
        if not video_url and isinstance(task_result, dict):
            videos = task_result.get("videos") or []
            if videos and isinstance(videos[0], dict):
                video_url = videos[0].get("url") or videos[0].get("video_url")
    if not video_url:
        video_url = result.get("video_url") or result.get("url")
    if isinstance(video_url, dict):
        video_url = video_url.get("url")
    return video_url


def _prompt_entries_with_lengths(payload: dict[str, Any]) -> tuple[list[dict], Optional[str]]:
    """Return multi_prompt shots (+ lengths) and single prompt for debug output."""
    multi = payload.get("multi_prompt")
    if isinstance(multi, list) and multi:
        shots = []
        for shot in multi:
            if not isinstance(shot, dict):
                continue
            text = str(shot.get("prompt") or "")
            shots.append({
                **shot,
                "prompt_length": len(text),
            })
        return shots, None
    single = payload.get("prompt")
    if single:
        text = str(single)
        return [], text if len(text) <= 2000 else text[:1997] + "..."
    return [], None


def save_kling_request_debug(
    output_dir: str,
    *,
    image_paths: list[str],
    payload: dict[str, Any],
    ctx: dict[str, str],
    endpoint: str,
    task_id: Optional[str] = None,
) -> tuple[str, str]:
    """
    Write human-readable Kling prompt debug artifacts (no base64 image blobs).
    Returns (json_path, txt_path).
    """
    os.makedirs(output_dir, exist_ok=True)
    garment_full = ctx.get("garment_description") or ""
    multi_prompt, single_prompt = _prompt_entries_with_lengths(payload)
    submitted_at = time.strftime("%Y-%m-%d %H:%M:%S")
    debug_record: dict[str, Any] = {
        "submitted_at": submitted_at,
        "task_id": task_id,
        "endpoint": endpoint,
        "model_name": payload.get("model_name"),
        "multi_shot": payload.get("multi_shot"),
        "shot_type": payload.get("shot_type"),
        "aspect_ratio": payload.get("aspect_ratio"),
        "duration": payload.get("duration"),
        "mode": payload.get("mode"),
        "sound": payload.get("sound"),
        "image_sources": [os.path.basename(p) for p in image_paths if p],
        "prompt_context": {
            "style_scene_label": ctx.get("style_scene_label", ""),
            "garment_description_full": garment_full,
            "garment_short_used_in_prompt": _short_garment(garment_full),
            "user_prompt_extra": ctx.get("user_prompt", ""),
            "aspect_ratio": ctx.get("aspect_ratio", ""),
        },
        "multi_prompt": multi_prompt,
        "single_prompt": single_prompt,
        "single_prompt_length": len(single_prompt) if single_prompt else 0,
    }
    json_path = os.path.join(output_dir, KLING_REQUEST_BASENAME)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(debug_record, f, indent=2, ensure_ascii=False)

    lines = [
        f"Kling Omni request @ {submitted_at}",
        f"task_id: {task_id or '(pending)'}",
        f"endpoint: {endpoint}",
        f"model: {payload.get('model_name')}",
        f"multi_shot: {payload.get('multi_shot')}  aspect: {payload.get('aspect_ratio')}  duration: {payload.get('duration')}s",
        f"scene: {ctx.get('style_scene_label', '')}",
        f"garment (short): {_short_garment(garment_full)}",
        "",
        "Images:",
    ]
    for i, name in enumerate(debug_record["image_sources"], start=1):
        lines.append(f"  <<<image_{i}>>>  {name}")
    if ctx.get("user_prompt", "").strip():
        lines.extend(["", "user_prompt extra:", ctx.get("user_prompt", "").strip()])
    if multi_prompt:
        lines.append("")
        for shot in multi_prompt:
            idx = shot.get("index", "?")
            dur = shot.get("duration", "?")
            img_name = debug_record["image_sources"][idx - 1] if isinstance(idx, int) and 0 < idx <= len(debug_record["image_sources"]) else ""
            lines.extend([
                "",
                f"--- Shot {idx} ({dur}s) {img_name} ---",
                str(shot.get("prompt") or ""),
                f"[length: {shot.get('prompt_length', 0)} chars]",
            ])
    elif single_prompt:
        lines.extend(["", "--- Single prompt ---", single_prompt, f"[length: {len(single_prompt)} chars]"])
    txt_path = os.path.join(output_dir, KLING_PROMPTS_TXT_BASENAME)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    logger.info("[Kling] Saved prompt debug → %s", json_path)
    return json_path, txt_path


def submit_omni_video(
    image_paths: list[str],
    metadata: Optional[dict] = None,
    *,
    output_dir: Optional[str] = None,
    multi_shot: Optional[bool] = None,
    duration: int = 12,
    model_name: str | None = None,
    mode: str = "pro",
    sound: str = "off",
) -> tuple[str, dict]:
    """
    Submit Omni video job. Returns (task_id, result_record for JSON persistence).
    """
    if not kling_configured():
        raise RuntimeError("Kling API key not configured (KLING_API_KEY)")

    ctx = metadata_to_context(metadata)
    aspect_ratio = ctx.get("aspect_ratio") or "9:16"
    image_list = encode_paths(image_paths)
    if not image_list:
        raise ValueError("No valid images to send to Kling")

    resolved_model = (model_name or os.environ.get("KLING_OMNI_MODEL") or "kling-v3-omni").strip()
    use_multi = multi_shot if multi_shot is not None else len(image_list) > 1
    payload: dict[str, Any] = {
        "model_name": resolved_model,
        "multi_shot": use_multi,
        "image_list": image_list,
        "video_list": [],
        "mode": mode,
        "sound": sound,
        "aspect_ratio": aspect_ratio,
        "duration": str(duration),
        "element_list": [],
    }

    if use_multi:
        payload["shot_type"] = "customize"
        payload["multi_prompt"] = build_multi_shot_prompts(
            image_paths,
            style_scene_label=ctx.get("style_scene_label", ""),
            garment_description=ctx.get("garment_description", ""),
            user_prompt=ctx.get("user_prompt", ""),
            aspect_ratio=aspect_ratio,
        )
        payload["prompt"] = ""
    else:
        payload["shot_type"] = None
        payload["prompt"] = build_single_omni_prompt(
            image_paths,
            style_scene_label=ctx.get("style_scene_label", ""),
            garment_description=ctx.get("garment_description", ""),
            user_prompt=ctx.get("user_prompt", ""),
        )

    payload = {k: v for k, v in payload.items() if v is not None}

    endpoint = f"{api_base_url()}/videos/omni-video"
    if output_dir:
        save_kling_request_debug(
            output_dir,
            image_paths=image_paths,
            payload=payload,
            ctx=ctx,
            endpoint=endpoint,
            task_id=None,
        )
    logger.info(
        "[Kling] POST %s/videos/omni-video model=%s (%s, %d images, aspect=%s)",
        api_base_url(),
        resolved_model,
        "multi-shot" if use_multi else "single",
        len(image_list),
        aspect_ratio,
    )
    resp = requests.post(endpoint, headers=api_headers(), json=payload, timeout=120)
    if resp.status_code != 200:
        err = resp.text
        if "512" in err:
            raise ValueError(f"Kling prompt length error (0-512 chars): {err}")
        resp.raise_for_status()

    result = resp.json()
    task_id = _extract_task_id(result)
    submitted_at = time.strftime("%Y-%m-%d %H:%M:%S")
    multi_prompt, single_prompt = _prompt_entries_with_lengths(payload)
    record = {
        "task_id": task_id,
        "request_id": task_id,
        "status": "submitted",
        "method": TASK_TYPE_OMNI,
        "application": "kling-omni",
        "multi_shot": use_multi,
        "image_count": len(image_list),
        "metadata": ctx,
        "submitted_at": submitted_at,
        "request_debug_json": KLING_REQUEST_BASENAME,
        "request_debug_txt": KLING_PROMPTS_TXT_BASENAME,
        "multi_prompt": multi_prompt,
        "single_prompt": single_prompt,
    }
    if output_dir:
        save_kling_request_debug(
            output_dir,
            image_paths=image_paths,
            payload=payload,
            ctx=ctx,
            endpoint=endpoint,
            task_id=task_id,
        )
    return task_id, record


def save_result_record(output_dir: str, record: dict, *, style_task_id: Optional[str] = None) -> str:
    os.makedirs(output_dir, exist_ok=True)
    tid = record.get("request_id") or record.get("task_id") or "task"
    prefix = style_task_id or "style"
    name = f"{prefix}_{tid}_result.json"
    path = os.path.join(output_dir, name)
    record["output_dir"] = output_dir
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    # Stable alias for polling
    alias = os.path.join(output_dir, KLING_RESULT_BASENAME)
    with open(alias, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return path


def load_result_record(output_dir: str, existing_task_id: Optional[str] = None) -> Optional[dict]:
    alias = os.path.join(output_dir, KLING_RESULT_BASENAME)
    if os.path.isfile(alias):
        with open(alias, encoding="utf-8") as f:
            return json.load(f)
    if not existing_task_id:
        return None
    for name in os.listdir(output_dir):
        if name.endswith("_result.json") and existing_task_id in name:
            with open(os.path.join(output_dir, name), encoding="utf-8") as f:
                return json.load(f)
    return None


def poll_until_complete(
    task_id: str,
    *,
    poll_interval: int = 10,
    max_wait_time: int = 600,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    """Poll omni-video task until completed, failed, or timeout."""
    api_base = api_base_url()
    candidates = []
    for cid in (request_id, task_id):
        if cid and cid not in candidates:
            candidates.append(cid)

    start = time.time()
    endpoint_type = "omni-video"

    while True:
        if max_wait_time and (time.time() - start) > max_wait_time:
            return {
                "task_id": task_id,
                "status": "timeout",
                "error": f"Polling timed out after {max_wait_time}s",
            }

        for cid in candidates:
            url = f"{api_base}/videos/{endpoint_type}/{cid}"
            try:
                resp = requests.get(
                    url, headers=api_headers(), timeout=60, allow_redirects=True
                )
                if resp.status_code == 400:
                    logger.warning("[Kling] 400 polling id=%s: %s", cid, resp.text[:200])
                    continue
                if resp.status_code in (401, 403):
                    logger.error(
                        "[Kling] %s polling id=%s: %s",
                        resp.status_code,
                        cid,
                        resp.text[:300],
                    )
                    resp.raise_for_status()
                if resp.status_code == 429 or 500 <= resp.status_code <= 599:
                    time.sleep(poll_interval)
                    break
                resp.raise_for_status()
                result = resp.json()
                status = _extract_status(result)
                logger.info("[Kling] task %s status=%s", cid, status)

                if status in ("completed", "done", "success", "succeed"):
                    video_url = _extract_video_url(result)
                    return {
                        "task_id": task_id,
                        "request_id": request_id or task_id,
                        "polled_with": cid,
                        "status": "completed",
                        "result": result,
                        "data": {"video_url": video_url, "video": video_url},
                    }
                if status in ("failed", "error", "cancelled"):
                    err = "Kling task failed"
                    data = result.get("data")
                    if isinstance(data, dict):
                        err = data.get("task_status_msg") or data.get("message") or err
                    return {
                        "task_id": task_id,
                        "status": "failed",
                        "result": result,
                        "error": err,
                    }
                break
            except requests.RequestException as e:
                logger.warning("[Kling] poll error id=%s: %s", cid, e)
                continue

        time.sleep(poll_interval)


def download_video(video_url: str, dest_path: str) -> str:
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    with requests.get(video_url, stream=True, timeout=300) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    logger.info("[Kling] Downloaded video to %s", dest_path)
    return dest_path


def run_omni_pipeline(
    image_paths: list[str],
    output_dir: str,
    metadata: Optional[dict] = None,
    *,
    existing_task_id: Optional[str] = None,
    force_new_task: bool = False,
    defer_poll: bool = False,
    poll_interval: int = 10,
    max_wait_time: Optional[int] = 3600,
    output_filename: str = "reel.mp4",
    style_task_id: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Direct Kling Omni flow: POST omni-video → poll GET → download mp4 to output_dir.

    Returns (local_filename, video_url, task_id).
    If defer_poll: (None, None, task_id).
    """
    record = None if force_new_task else load_result_record(output_dir, existing_task_id)
    task_id = None if force_new_task else existing_task_id
    if not task_id and record:
        task_id = record.get("request_id") or record.get("task_id")

    # Resume in-flight task only when not forcing a fresh Kling submission.
    if (
        not force_new_task
        and task_id
        and record
        and record.get("status") == "completed"
        and record.get("generated_video_url")
    ):
        dest = os.path.join(output_dir, output_filename)
        if os.path.isfile(dest):
            logger.info("[Kling] Reusing completed task %s → %s", task_id, dest)
            return output_filename, record.get("generated_video_url"), task_id

    if not task_id or force_new_task:
        task_id, record = submit_omni_video(image_paths, metadata, output_dir=output_dir)
        save_result_record(output_dir, record, style_task_id=style_task_id)
    elif record:
        record["status"] = record.get("status") or "submitted"
    else:
        record = {
            "task_id": task_id,
            "request_id": task_id,
            "method": TASK_TYPE_OMNI,
            "status": "submitted",
        }

    if defer_poll:
        return None, None, task_id

    poll = poll_until_complete(
        task_id,
        poll_interval=poll_interval,
        max_wait_time=max_wait_time,
        request_id=record.get("request_id") if record else task_id,
    )

    record["status"] = poll.get("status")
    if poll.get("error"):
        record["error"] = poll["error"]
    save_result_record(output_dir, record, style_task_id=style_task_id)

    if poll.get("status") != "completed":
        raise RuntimeError(poll.get("error") or f"Kling video failed: {poll.get('status')}")

    data = poll.get("data") or {}
    video_url = data.get("video_url") or data.get("video")
    if not video_url:
        raise RuntimeError("Kling completed but no video URL in response")

    dest = os.path.join(output_dir, output_filename)
    download_video(video_url, dest)
    record["downloaded_files"] = [dest]
    record["generated_video_url"] = video_url
    save_result_record(output_dir, record, style_task_id=style_task_id)
    return output_filename, video_url, task_id
