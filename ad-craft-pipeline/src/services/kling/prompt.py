"""
Build Kling Omni prompts that showcase the primary garment and read like a real fashion film shoot.

Priority order inside the per-shot character budget:
  1. Hero garment (what to showcase)              -> most important
  2. Shot role (pose / detail / back direction)
  3. Empty background + single model (hard rules)
  4. Face identity lock
  5. Cinematic realism + quality
  6. Run user_prompt extras                        -> trimmed/dropped first
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

# Kling Omni enforces ~512 chars per shot prompt; keep headroom.
_MAX_SHOT_PROMPT_CHARS = 500
_MAX_SINGLE_PROMPT_CHARS = 510

# Short, high-signal constants (every char counts against the 500 limit).
_REALISM = (
    "Real fashion-film look, natural light, sharp; skin on face, neck, arms and hands keeps real texture "
    "and pores—NOT plastic/over-smoothed AI skin, no CGI gloss."
)
_EMPTY_BG = "ONLY this one model in frame; empty location, zero other people/crowds/reflections."
_FACE = (
    "Keep her exact face, skin tone, hair and expression from the reference; do NOT smooth or beautify "
    "into an AI face—no face change. Real hands: correct fingers and nails, never warped/fused/melted."
)
# Garment is the product: every minute detail is locked across the whole video.
_GARMENT_LOCK = (
    "Garment stays IDENTICAL—same zips, buttons, pockets, print, seams, trims; change/add/remove nothing."
)
_NO_AUDIO = "Silent, no audio."


def _scene_label(style_scene_label: str) -> str:
    """Human-readable scene for the video prompt (drop the internal '[folder: ...]' slug hint)."""
    s = re.sub(r"\[folder:[^\]]*\]", "", style_scene_label or "")
    s = s.replace("_", " ").strip(" .;,")
    return s or "lifestyle"


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip(" ,.;") + "..."


def _short_garment(garment_description: str, limit: int = 160) -> str:
    """Condense the (often long) garment analysis into a hero-garment phrase for the video prompt."""
    g = re.sub(r"\s+", " ", (garment_description or "").strip())
    if not g:
        return "the featured outfit"
    # Prefer the explicit HERO GARMENT clause if present in the analysis string.
    m = re.search(r"HERO GARMENT:\s*(.+?)(?:\.|$)", g, re.IGNORECASE)
    if m:
        g = m.group(1).strip()
    else:
        # Drop our boilerplate prefix if the raw analysis prompt was passed in.
        g = re.sub(r"^GARMENT ANALYSIS.*?:\s*", "", g, flags=re.IGNORECASE)
    if len(g) > limit:
        g = g[: limit - 3].rstrip(" ,.;") + "..."
    return g


def _shot_direction(path: str, index: int) -> str:
    """Role-specific direction: how the model presents the garment for this frame."""
    name = os.path.basename(path).lower()
    if name.endswith("_swapped_garment_detail.png"):
        return (
            "Tight beauty close-up on the garment: slow push-in across fabric, texture, print, neckline, "
            "buttons and seams so the product detail reads clearly."
        )
    if name.endswith("_swapped_back.png"):
        return (
            "Model slowly turns to show the BACK of the garment: reveal the back design, hem and fit; "
            "graceful turn, she stays fully in frame."
        )
    if name.endswith("_swapped.png"):
        return (
            "Full-body front hero: confident editorial pose, slight body turn and a small step, hands clear "
            "of the garment so its silhouette and front design stay fully visible."
        )
    return "Showcase the garment with a confident editorial pose; keep the product fully visible."


def _build_shot_prompt(
    path: str,
    index: int,
    *,
    ref: str,
    scene: str,
    garment: str,
    extra: str,
    limit: int,
) -> str:
    """Compose one shot prompt, garment-first, trimming the optional run extra to fit the budget."""
    direction = _shot_direction(path, index)
    core = (
        f"HERO PRODUCT (showcase this): {garment}. {_GARMENT_LOCK} "
        f"{ref}: {direction} "
        f"Setting: {scene}; slow luxury pacing, gentle camera move, model centered and never cut off. "
        f"{_FACE} {_EMPTY_BG} {_REALISM} {_NO_AUDIO}"
    )
    core = re.sub(r"\s+", " ", core).strip()
    if extra:
        candidate = f"{core} {extra.strip()}"
        if len(candidate) <= limit:
            return candidate
        # Extra doesn't fit — keep the garment-first core intact.
    return _clip(core, limit)


def build_multi_shot_prompts(
    image_paths: list[str],
    *,
    style_scene_label: str = "",
    garment_description: str = "",
    user_prompt: str = "",
    aspect_ratio: str = "9:16",
) -> list[dict[str, Any]]:
    """One Kling multi_prompt entry per composite image, hero -> detail -> back order."""
    scene = _scene_label(style_scene_label)
    garment = _short_garment(garment_description)
    extra = re.sub(r"\s+", " ", (user_prompt or "").strip())

    durations = _durations_for_shot_count(len(image_paths))
    shots: list[dict[str, Any]] = []
    for i, path in enumerate(image_paths):
        ref = f"<<<image_{i + 1}>>>"
        prompt_text = _build_shot_prompt(
            path, i + 1, ref=ref, scene=scene, garment=garment, extra=extra,
            limit=_MAX_SHOT_PROMPT_CHARS,
        )
        shots.append({
            "index": i + 1,
            "prompt": prompt_text,
            "duration": str(durations[i] if i < len(durations) else durations[-1]),
        })
    return shots


def build_single_omni_prompt(
    image_paths: list[str],
    *,
    style_scene_label: str = "",
    garment_description: str = "",
    user_prompt: str = "",
) -> str:
    """Single continuous omni prompt referencing <<<image_1>>> ... <<<image_n>>>."""
    scene = _scene_label(style_scene_label)
    garment = _short_garment(garment_description)
    extra = re.sub(r"\s+", " ", (user_prompt or "").strip())

    beats = []
    for i, path in enumerate(image_paths):
        beats.append(f"<<<image_{i + 1}>>> {_shot_direction(path, i + 1)}")
    sequence = " ".join(beats) if beats else "Showcase the garment front then back."

    core = (
        f"HERO PRODUCT (showcase this): {garment}. {_GARMENT_LOCK} {sequence} "
        f"Setting: {scene}; slow luxury pacing, gentle camera moves, model centered, no walking out of frame. "
        f"{_FACE} {_EMPTY_BG} {_REALISM} {_NO_AUDIO}"
    )
    core = re.sub(r"\s+", " ", core).strip()
    if extra:
        candidate = f"{core} {extra}"
        if len(candidate) <= _MAX_SINGLE_PROMPT_CHARS:
            return candidate
    return _clip(core, _MAX_SINGLE_PROMPT_CHARS)


def _durations_for_shot_count(n: int) -> list[int]:
    """Split ~12s total across shots."""
    if n <= 1:
        return [12]
    if n == 2:
        return [6, 6]
    if n == 3:
        return [4, 4, 4]
    per = max(3, 12 // n)
    return [per] * n


def metadata_to_context(metadata: Optional[dict]) -> dict[str, str]:
    if not metadata:
        return {}
    garment_desc = str(metadata.get("garment_description") or metadata.get("garmentDescription") or "")
    if not garment_desc and metadata.get("garment_analysis"):
        g_analysis = metadata["garment_analysis"]
        if isinstance(g_analysis, dict):
            master = g_analysis.get("master_garment")
            if isinstance(master, str) and master.strip():
                g_type = master.strip()
            elif isinstance(master, dict):
                g_type = master.get("garment_type") or master.get("category") or ""
            else:
                g_type = ""
            color_obj = g_analysis.get("color_story")
            color = color_obj.get("primary_color") or "" if isinstance(color_obj, dict) else ""
            pattern_obj = g_analysis.get("pattern_and_surface_design")
            pattern = pattern_obj.get("pattern_type") or "" if isinstance(pattern_obj, dict) else ""
            garment_desc = f"{color} {pattern} {g_type}".strip()
    if not garment_desc:
        garment_desc = str(metadata.get("product_title") or "")

    return {
        "style_scene_label": str(metadata.get("style_scene_label") or metadata.get("backgroundPrompt") or ""),
        "garment_description": garment_desc,
        "user_prompt": str(metadata.get("user_prompt") or metadata.get("userPrompt") or ""),
        "aspect_ratio": str(metadata.get("aspect_ratio") or metadata.get("aspectRatio") or "9:16"),
    }
