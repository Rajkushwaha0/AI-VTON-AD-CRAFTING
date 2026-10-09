import io
import math
from typing import Optional, Tuple, Dict, Any, List
from PIL import Image

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from .models import Tier1Result, QCDefect, DefectSeverity

class Tier1DeterministicQC:
    """
    Tier 1 Fast Deterministic Vision QC.
    Performs local CPU checks on image validity, resolution, aspect ratio,
    exposure/contrast histograms, and regional Laplacian blur.
    Designed to fail fast on corrupted or clearly defective images.
    """

    def __init__(
        self,
        min_width: int = 720,
        min_height: int = 720,
        blur_threshold_full: float = 45.0,
        blur_threshold_region: float = 35.0,
        max_highlight_clip_pct: float = 12.0,  # Max % of pixels allowed above 250
        max_shadow_clip_pct: float = 15.0,     # Max % of pixels allowed below 5
        min_contrast_std: float = 18.0         # Min grayscale std dev (flat gray check)
    ):
        self.min_width = min_width
        self.min_height = min_height
        self.blur_threshold_full = blur_threshold_full
        self.blur_threshold_region = blur_threshold_region
        self.max_highlight_clip_pct = max_highlight_clip_pct
        self.max_shadow_clip_pct = max_shadow_clip_pct
        self.min_contrast_std = min_contrast_std

    def evaluate(
        self,
        image_bytes: bytes,
        target_aspect_ratio: str = "9:16"
    ) -> Tier1Result:
        defects: List[QCDefect] = []

        if not image_bytes or len(image_bytes) < 1000:
            return Tier1Result(
                passed=False,
                score=0.0,
                defects=[QCDefect(
                    category="image_integrity",
                    severity=DefectSeverity.CRITICAL,
                    description="Empty or corrupted image byte stream.",
                    suggested_fix="Regenerate image from base pipeline."
                )]
            )

        # 1. Decode Image via OpenCV or PIL
        img_bgr = None
        pil_img = None

        if HAS_CV2:
            try:
                nparr = np.frombuffer(image_bytes, np.uint8)
                img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            except Exception:
                img_bgr = None

        if img_bgr is None:
            try:
                pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                if HAS_CV2:
                    img_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
            except Exception as e:
                return Tier1Result(
                    passed=False,
                    score=0.0,
                    defects=[QCDefect(
                        category="image_integrity",
                        severity=DefectSeverity.CRITICAL,
                        description=f"Failed to decode image data: {str(e)}",
                        suggested_fix="Regenerate image from scratch."
                    )]
                )

        if HAS_CV2 and img_bgr is not None:
            h, w = img_bgr.shape[:2]
            gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        else:
            if pil_img is None:
                pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            w, h = pil_img.size
            gray = None

        # 2. Resolution Check
        if w < self.min_width or h < self.min_height:
            defects.append(QCDefect(
                category="resolution",
                severity=DefectSeverity.HIGH,
                description=f"Resolution ({w}x{h}) is below required minimum ({self.min_width}x{self.min_height}).",
                suggested_fix="Ensure high-resolution generation upscaling."
            ))

        # 3. Aspect Ratio Check
        target_w, target_h = 9, 16
        if ":" in target_aspect_ratio:
            try:
                p = target_aspect_ratio.split(":")
                target_w, target_h = int(p[0]), int(p[1])
            except Exception:
                pass
        target_ratio_val = target_w / max(1, target_h)
        actual_ratio_val = w / max(1, h)
        aspect_diff = abs(actual_ratio_val - target_ratio_val)

        if aspect_diff > 0.05:
            defects.append(QCDefect(
                category="aspect_ratio",
                severity=DefectSeverity.MEDIUM,
                description=f"Aspect ratio {w}:{h} deviates from target {target_aspect_ratio} by {aspect_diff:.3f}.",
                suggested_fix="Apply strict aspect-ratio bounding crop."
            ))

        # 4. Exposure, Contrast & Histogram Analysis
        highlight_clip_pct = 0.0
        shadow_clip_pct = 0.0
        contrast_std = 50.0

        if HAS_CV2 and gray is not None:
            total_pixels = float(h * w)
            high_count = float(np.sum(gray >= 252))
            low_count = float(np.sum(gray <= 4))
            highlight_clip_pct = round((high_count / total_pixels) * 100.0, 2)
            shadow_clip_pct = round((low_count / total_pixels) * 100.0, 2)
            contrast_std = round(float(np.std(gray)), 2)

            if highlight_clip_pct > self.max_highlight_clip_pct:
                defects.append(QCDefect(
                    category="exposure",
                    severity=DefectSeverity.HIGH,
                    description=f"Severe highlight blowout ({highlight_clip_pct}% clipped bright pixels).",
                    suggested_fix="Reduce exposure; avoid blown-out white flash lighting."
                ))
            if shadow_clip_pct > self.max_shadow_clip_pct:
                defects.append(QCDefect(
                    category="exposure",
                    severity=DefectSeverity.HIGH,
                    description=f"Severe underexposure / crushed blacks ({shadow_clip_pct}% crushed black pixels).",
                    suggested_fix="Increase ambient lighting and shadow fill."
                ))
            if contrast_std < self.min_contrast_std:
                defects.append(QCDefect(
                    category="contrast",
                    severity=DefectSeverity.HIGH,
                    description=f"Low image dynamic range / flat contrast (std: {contrast_std}).",
                    suggested_fix="Enhance dynamic range and depth separation."
                ))

        # 5. Regional Laplacian Blur Sharpness
        full_blur = 100.0
        face_blur = None
        garment_blur = None

        if HAS_CV2 and gray is not None:
            full_blur = round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2)
            
            # Face region heuristic crop (top 12% to 42% height, center 55% width)
            f_top = int(h * 0.10)
            f_bottom = int(h * 0.42)
            f_left = int(w * 0.22)
            f_right = int(w * 0.78)
            face_region = gray[f_top:f_bottom, f_left:f_right]
            if face_region.size > 0:
                face_blur = round(float(cv2.Laplacian(face_region, cv2.CV_64F).var()), 2)

            # Garment region heuristic crop (mid 28% to 75% height)
            g_top = int(h * 0.28)
            g_bottom = int(h * 0.75)
            g_left = int(w * 0.15)
            g_right = int(w * 0.85)
            garment_region = gray[g_top:g_bottom, g_left:g_right]
            if garment_region.size > 0:
                garment_blur = round(float(cv2.Laplacian(garment_region, cv2.CV_64F).var()), 2)

            # Check blur thresholds
            if full_blur < self.blur_threshold_full:
                defects.append(QCDefect(
                    category="blur",
                    severity=DefectSeverity.CRITICAL if full_blur < 25.0 else DefectSeverity.HIGH,
                    description=f"Canvas is blurry (Laplacian variance: {full_blur:.1f} < threshold {self.blur_threshold_full}).",
                    suggested_fix="Regenerate with high-frequency texture sharpness, eliminating motion blur."
                ))
            if face_blur is not None and face_blur < self.blur_threshold_region:
                defects.append(QCDefect(
                    category="blur",
                    severity=DefectSeverity.HIGH,
                    description=f"Face region lacks sharp edge details (Laplacian variance: {face_blur:.1f}).",
                    suggested_fix="Enhance facial crispness and sharp eye/lip contour definition."
                ))
            if garment_blur is not None and garment_blur < self.blur_threshold_region:
                defects.append(QCDefect(
                    category="blur",
                    severity=DefectSeverity.HIGH,
                    description=f"Garment region lacks fabric texture sharpness (Laplacian variance: {garment_blur:.1f}).",
                    suggested_fix="Preserve high-definition weave texture and crisp seamlines."
                ))

        # Calculate Tier 1 Score
        penalty = 0.0
        for d in defects:
            if d.severity == DefectSeverity.CRITICAL:
                penalty += 0.50
            elif d.severity == DefectSeverity.HIGH:
                penalty += 0.20
            elif d.severity == DefectSeverity.MEDIUM:
                penalty += 0.10
            elif d.severity == DefectSeverity.LOW:
                penalty += 0.05

        score = max(0.0, round(1.0 - penalty, 3))
        has_critical = any(d.severity == DefectSeverity.CRITICAL for d in defects)
        passed = (not has_critical) and (score >= 0.70)

        return Tier1Result(
            passed=passed,
            score=score,
            width=w,
            height=h,
            aspect_ratio_str=f"{w}:{h}",
            aspect_ratio_diff=round(aspect_diff, 4),
            full_laplacian_sharpness=full_blur,
            face_laplacian_sharpness=face_blur,
            garment_laplacian_sharpness=garment_blur,
            highlight_clipping_pct=highlight_clip_pct,
            shadow_clipping_pct=shadow_clip_pct,
            contrast_std=contrast_std,
            defects=defects,
            details={
                "w": w, "h": h,
                "full_blur": full_blur,
                "face_blur": face_blur,
                "garment_blur": garment_blur,
                "contrast_std": contrast_std,
                "highlight_clip_pct": highlight_clip_pct,
                "shadow_clip_pct": shadow_clip_pct
            }
        )
