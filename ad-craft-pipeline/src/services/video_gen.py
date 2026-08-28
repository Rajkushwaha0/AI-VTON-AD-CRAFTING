"""
Parallel Dual/Tri-Shot Video Generator with Visual Continuity and Standalone FFmpeg Merging.

Workflow:
1. Validates duration (10s = 5s x 2 shots; 15s = 5s x 3 shots).
2. Parallel Kling v3.0 / v1.6 Image2Video generation for each shot.
3. Enforces visual continuity:
   - Shot 1: hero.jpg (Full-body forward stride / pose dynamic).
   - Shot 2: detail.jpg (Macro fabric texture pan / slow turn).
   - Shot 3 (if 15s): back.jpg (Rear back design / turnaround).
4. Invokes standalone FFmpegService when merge_video is enabled (supporting 'smooth' and 'fast_paced' transitions).
"""

import os
import time
import json
import logging
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..core.interfaces import IVideoGenerator
from ..core.models import PipelineContext, GeneratedAsset
from .video_processing.ffmpeg_service import FFmpegService
from .kling.kling_image2video import submit_image2video, poll_image2video_task, download_kling_video
from .kling.auth import kling_configured

logger = logging.getLogger("ParallelVideoGenerator")


CONTINUITY_PROMPT_PREFIX = (
    "CINEMATIC FASHION COMMERCIAL REEL — ZERO AI ARTIFACTS:\n"
    "• Cast: Single professional fashion model only (ZERO bystanders, zero background humans).\n"
    "• Garment Lock: Maintain 100% exact fidelity on fabric, color hue, stitching, seams, and fit. No fabric melting.\n"
    "• Camera: Shot on 85mm luxury lens with subtle, high-end organic camera movement.\n"
    "• Lighting: Scene-consistent ambient lighting with natural depth and soft floor contact shadows.\n"
)


class ParallelVideoGenerator(IVideoGenerator):
    def __init__(self):
        self.ffmpeg_service = FFmpegService()

    def generate_videos(
        self, 
        context: PipelineContext, 
        assets: List[GeneratedAsset], 
        max_parallel: int = 4,
        aspect_ratio: str = "9:16",
        duration: int = 10,
        merge_video: bool = True,
        merge_video_idea: str = "smooth",
        user_prompt: Optional[str] = None
    ) -> List[GeneratedAsset]:
        
        valid_assets = [a for a in assets if not a.error and a.image_paths]
        resolved_aspect = aspect_ratio or context.aspect_ratio or "9:16"

        # Validate duration (strictly 10 or 15 seconds)
        if duration not in (10, 15):
            context.log(f"Warning: Duration {duration}s not allowed (only 10s or 15s supported). Defaulting to 10s.")
            duration = 10

        num_shots = 2 if duration == 10 else 3
        context.log(
            f"🎬 Starting Parallel Video Generation for {len(valid_assets)} asset(s):\n"
            f"• Target Duration: {duration}s ({num_shots} parallel shots of 5s each)\n"
            f"• Merge Video: {merge_video} (Style: '{merge_video_idea}')\n"
            f"• Aspect Ratio: {resolved_aspect}"
        )

        for asset in valid_assets:
            try:
                self._generate_shots_and_merge(
                    context=context,
                    asset=asset,
                    duration=duration,
                    num_shots=num_shots,
                    merge_video=merge_video,
                    merge_video_idea=merge_video_idea,
                    aspect_ratio=resolved_aspect,
                    user_prompt=user_prompt
                )
            except Exception as e:
                context.log(f"Video generation failed for style '{asset.style_name}': {e}")
                asset.error = str(e)

        return assets

    def _generate_shots_and_merge(
        self,
        context: PipelineContext,
        asset: GeneratedAsset,
        duration: int,
        num_shots: int,
        merge_video: bool,
        merge_video_idea: str,
        aspect_ratio: str,
        user_prompt: Optional[str]
    ):
        safe_style = asset.style_name.replace(" ", "_").replace("/", "_").lower()
        style_dir = os.path.join(context.videos_dir, safe_style)
        os.makedirs(style_dir, exist_ok=True)

        images = asset.image_paths
        hero_img = images[0] if len(images) > 0 and os.path.exists(images[0]) else None
        detail_img = images[1] if len(images) > 1 and os.path.exists(images[1]) else hero_img
        back_img = images[2] if len(images) > 2 and os.path.exists(images[2]) else hero_img

        shot_plans = [
            {
                "shot_index": 1,
                "name": "shot_1.mp4",
                "image_path": hero_img,
                "prompt": (
                    f"{CONTINUITY_PROMPT_PREFIX}"
                    f"SHOT 1 (Full-Body Hero): The fashion model in {asset.style_name} setting initiates a slow, confident "
                    f"stride towards the camera, pausing with an elegant editorial posture. Gentle fabric drape flow. "
                    f"{user_prompt or ''}"
                )
            },
            {
                "shot_index": 2,
                "name": "shot_2.mp4",
                "image_path": detail_img,
                "prompt": (
                    f"{CONTINUITY_PROMPT_PREFIX}"
                    f"SHOT 2 (Detail Macro Pan): High-fashion close-up with slow tracking camera glide across the "
                    f"fabric texture, buttons, and craftsmanship, model subtly shifting angles. "
                    f"{user_prompt or ''}"
                )
            }
        ]

        if num_shots == 3:
            shot_plans.append({
                "shot_index": 3,
                "name": "shot_3.mp4",
                "image_path": back_img,
                "prompt": (
                    f"{CONTINUITY_PROMPT_PREFIX}"
                    f"SHOT 3 (Rear Back & Turnaround): The same model turns smoothly 180 degrees away from the camera, "
                    f"showcasing the rear garment structure, then looks back over the shoulder. "
                    f"{user_prompt or ''}"
                )
            })

        # Save shot plan manifest
        plan_manifest_path = os.path.join(style_dir, "video_shot_plan.json")
        with open(plan_manifest_path, "w", encoding="utf-8") as f:
            json.dump({
                "style": asset.style_name,
                "target_duration": duration,
                "num_shots": num_shots,
                "merge_video": merge_video,
                "merge_video_idea": merge_video_idea,
                "shots": shot_plans
            }, f, indent=4)

        has_kling = kling_configured()
        downloaded_shots: List[str] = [None] * num_shots

        # Generate shots in parallel
        context.log(f"⚡ Dispatching {num_shots} parallel video calls for '{asset.style_name}'...")
        with ThreadPoolExecutor(max_workers=num_shots) as executor:
            futures = {
                executor.submit(
                    self._execute_single_shot,
                    context,
                    plan,
                    style_dir,
                    aspect_ratio,
                    has_kling
                ): i for i, plan in enumerate(shot_plans)
            }

            for future in as_completed(futures):
                idx = futures[future]
                shot_file = future.result()
                downloaded_shots[idx] = shot_file
                context.log(f"✅ Completed Shot {idx + 1}/{num_shots} -> {os.path.basename(shot_file)}")

        valid_shots = [p for p in downloaded_shots if p and os.path.exists(p)]
        if not valid_shots:
            raise RuntimeError("All video shot generations failed.")

        # Standalone FFmpeg Merge Execution
        if merge_video and len(valid_shots) > 1:
            merged_filename = f"{safe_style}_final_merged_campaign_reel.mp4"
            merged_path = os.path.join(style_dir, merged_filename)
            context.log(f"🎞️ Merging {len(valid_shots)} shots using FFmpeg (Style: '{merge_video_idea}')...")
            
            final_video_path = self.ffmpeg_service.merge_videos(
                video_paths=valid_shots,
                output_path=merged_path,
                merge_style=merge_video_idea,
                crossfade_duration=0.5,
                target_aspect_ratio=aspect_ratio,
                fps=30
            )
            asset.video_path = final_video_path
            context.log(f"🎉 Merged Video Reel created: {final_video_path}")
        else:
            asset.video_path = valid_shots[0]

        asset.metadata["video_generation"] = {
            "duration": duration,
            "num_shots": num_shots,
            "individual_shots": valid_shots,
            "merged": merge_video and len(valid_shots) > 1,
            "merge_video_idea": merge_video_idea,
            "final_video": asset.video_path
        }

    def _execute_single_shot(
        self,
        context: PipelineContext,
        plan: Dict[str, Any],
        output_dir: str,
        aspect_ratio: str,
        has_kling: bool
    ) -> str:
        shot_path = os.path.join(output_dir, plan["name"])
        img_path = plan["image_path"]
        prompt = plan["prompt"]

        if not has_kling:
            context.log(f"Simulation Mode: Generating animated shot {plan['shot_index']} via FFmpeg...")
            return self.ffmpeg_service.create_simulated_video(
                image_path=img_path,
                output_path=shot_path,
                duration=5,
                fps=30,
                aspect_ratio=aspect_ratio
            )

        # Real Kling API execution
        task_id, record = submit_image2video(
            image_path=img_path,
            prompt=prompt,
            duration=5,
            aspect_ratio=aspect_ratio
        )
        context.log(f"Submitted Shot {plan['shot_index']} to Kling (Task ID: {task_id}). Polling...")
        
        poll_res = poll_image2video_task(task_id, poll_interval=8, max_wait_time=360)
        if poll_res.get("status") != "completed":
            raise RuntimeError(f"Kling shot {plan['shot_index']} failed: {poll_res.get('error')}")

        video_url = poll_res.get("video_url")
        if not video_url:
            raise RuntimeError(f"No video URL returned for shot {plan['shot_index']}")

        return download_kling_video(video_url, shot_path)
