"""
Standalone, Reusable FFmpeg Video Processing Service.

Capabilities:
1. Seamless video merging with 'smooth' (xfade crossfade) or 'fast_paced' (direct concatenation) transitions.
2. Video normalization (aspect ratio enforcement, 1080x1920 9:16, fps normalization, H.264 encoding).
3. Extract video metadata (duration, resolution, fps, bitrate).
4. Synthetic video generation for simulation / testing when external AI APIs are offline.
"""

import os
import re
import json
import shutil
import logging
import subprocess
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("FFmpegService")


class FFmpegService:
    """Standalone video manipulation service using FFmpeg CLI."""

    def __init__(self, ffmpeg_bin: Optional[str] = None, ffprobe_bin: Optional[str] = None):
        self.ffmpeg = ffmpeg_bin or shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe = ffprobe_bin or shutil.which("ffprobe") or "ffprobe"

    def is_available(self) -> bool:
        """Checks if ffmpeg binary is executable."""
        try:
            res = subprocess.run([self.ffmpeg, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return res.returncode == 0
        except Exception:
            return False

    def get_video_info(self, video_path: str) -> Dict[str, Any]:
        """Extracts video metadata via ffprobe."""
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        try:
            cmd = [
                self.ffprobe,
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                video_path
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            data = json.loads(res.stdout)
            
            duration = float(data.get("format", {}).get("duration", 0.0))
            video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
            width = int(video_stream.get("width", 0))
            height = int(video_stream.get("height", 0))
            
            return {
                "duration": duration,
                "width": width,
                "height": height,
                "aspect_ratio": f"{width}:{height}" if height else "unknown",
                "codec": video_stream.get("codec_name", ""),
                "fps": eval(video_stream.get("r_frame_rate", "30/1")) if "/" in video_stream.get("r_frame_rate", "") else 30.0
            }
        except Exception as e:
            logger.warning(f"Failed to probe video {video_path}: {e}")
            return {"duration": 5.0, "width": 1080, "height": 1920, "aspect_ratio": "9:16", "fps": 30.0}

    def merge_videos(
        self,
        video_paths: List[str],
        output_path: str,
        merge_style: str = "smooth",  # "smooth" (crossfade) or "fast_paced" (direct cut)
        crossfade_duration: float = 0.5,
        target_aspect_ratio: str = "9:16",
        fps: int = 30
    ) -> str:
        """
        Merges multiple video clips into a single cohesive campaign reel.
        Supports 'smooth' (0.5s xfade crossfade) or 'fast_paced' (crisp direct cut).
        """
        valid_paths = [p for p in video_paths if os.path.exists(p) and os.path.getsize(p) > 1000]
        if not valid_paths:
            raise ValueError("No valid video files provided to merge.")

        if len(valid_paths) == 1:
            shutil.copy(valid_paths[0], output_path)
            return output_path

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        if not self.is_available():
            logger.warning("FFmpeg binary not found on system. Copying first video as fallback.")
            shutil.copy(valid_paths[0], output_path)
            return output_path

        normalized_style = merge_style.lower().strip()
        if normalized_style in ("smooth", "crossfade", "fade"):
            return self._merge_with_crossfade(valid_paths, output_path, crossfade_duration, fps)
        else:
            return self._merge_with_concat(valid_paths, output_path, fps)

    def _merge_with_crossfade(
        self,
        video_paths: List[str],
        output_path: str,
        crossfade_dur: float,
        fps: int
    ) -> str:
        """Builds dynamic multi-input FFmpeg xfade filter graph."""
        n = len(video_paths)
        durations = []
        for p in video_paths:
            info = self.get_video_info(p)
            durations.append(max(2.0, info.get("duration", 5.0)))

        # Build filter_complex string
        # Normalize all inputs to same resolution (1080x1920) and fps first
        input_args = []
        filter_parts = []
        for i, p in enumerate(video_paths):
            input_args.extend(["-i", p])
            filter_parts.append(
                f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v{i}];"
            )

        current_offset = durations[0] - crossfade_dur
        prev_label = "v0"

        for i in range(1, n):
            next_label = f"v{i}"
            out_label = f"xfade{i}" if i < n - 1 else "v_out"
            filter_parts.append(
                f"[{prev_label}][{next_label}]xfade=transition=fade:duration={crossfade_dur}:offset={max(0.1, current_offset):.2f}[{out_label}];"
            )
            prev_label = out_label
            if i < n - 1:
                current_offset += (durations[i] - crossfade_dur)

        filter_str = "".join(filter_parts).rstrip(";")

        cmd = [
            self.ffmpeg,
            "-y",
            *input_args,
            "-filter_complex", filter_str,
            "-map", "[v_out]",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            "-crf", "22",
            "-r", str(fps),
            output_path
        ]

        try:
            logger.info(f"Running FFmpeg smooth crossfade merge on {len(video_paths)} clips...")
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            return output_path
        except subprocess.CalledProcessError as e:
            logger.warning(f"FFmpeg xfade failed: {e.stderr.decode('utf-8', errors='ignore')[:300]}. Falling back to concat.")
            return self._merge_with_concat(video_paths, output_path, fps)

    def _merge_with_concat(
        self,
        video_paths: List[str],
        output_path: str,
        fps: int
    ) -> str:
        """Concatenates videos with direct fast-paced cuts."""
        temp_list_file = output_path + ".concat.txt"
        with open(temp_list_file, "w", encoding="utf-8") as f:
            for p in video_paths:
                abs_p = os.path.abspath(p)
                f.write(f"file '{abs_p}'\n")

        cmd = [
            self.ffmpeg,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", temp_list_file,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            "-crf", "22",
            "-r", str(fps),
            output_path
        ]

        try:
            logger.info(f"Running FFmpeg fast-paced concat merge on {len(video_paths)} clips...")
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        finally:
            if os.path.exists(temp_list_file):
                os.remove(temp_list_file)

        return output_path

    def create_simulated_video(
        self,
        image_path: str,
        output_path: str,
        duration: int = 5,
        fps: int = 30,
        aspect_ratio: str = "9:16"
    ) -> str:
        """Creates a smooth animated panning video from a static image for testing/simulation."""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        w, h = (1080, 1920) if aspect_ratio == "9:16" else (1024, 1024)

        if self.is_available() and os.path.exists(image_path):
            total_frames = duration * fps
            # Subtle zoom-in pan filter
            vf = (
                f"scale={w*2}:{h*2},"
                f"zoompan=z='min(zoom+0.0015,1.15)':d={total_frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps},"
                f"format=yuv420p"
            )
            cmd = [
                self.ffmpeg,
                "-y",
                "-loop", "1",
                "-i", image_path,
                "-t", str(duration),
                "-vf", vf,
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-r", str(fps),
                output_path
            ]
            try:
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
                return output_path
            except Exception as e:
                logger.warning(f"Simulated video creation via ffmpeg failed: {e}")

        # Fallback dummy file
        with open(output_path, "wb") as f:
            f.write(b"SIMULATED_MP4_VIDEO_HEADER" + b"\x00" * 4096)
        return output_path
