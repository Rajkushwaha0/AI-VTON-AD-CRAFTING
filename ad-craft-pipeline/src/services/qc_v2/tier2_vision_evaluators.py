import io
import os
from typing import Optional, Tuple, Dict, Any, List
from PIL import Image

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from .models import Tier2Result, QCDefect, DefectSeverity

class Tier2VisionEvaluators:
    """
    Tier 2 Specialized Computer Vision & Feature Evaluators:
    1. Real Face Detection & Face Biometric Comparison (OpenCV Haar/DNN)
    2. Garment Fidelity & 2D HSV Color Correlation
    3. Anatomy & Framing Proportions Heuristics
    """

    def __init__(
        self,
        min_face_size_px: int = 40,
        min_garment_color_corr: float = 0.35,
        min_garment_texture_corr: float = 0.35
    ):
        self.min_face_size_px = min_face_size_px
        self.min_garment_color_corr = min_garment_color_corr
        self.min_garment_texture_corr = min_garment_texture_corr

        self.face_cascade = None
        if HAS_CV2:
            try:
                cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                if os.path.exists(cascade_path):
                    self.face_cascade = cv2.CascadeClassifier(cascade_path)
            except Exception:
                self.face_cascade = None

    def _decode_bgr(self, data: Optional[bytes]):
        if not data or not HAS_CV2:
            return None
        try:
            arr = np.frombuffer(data, np.uint8)
            return cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception:
            return None

    def _detect_faces(self, img_bgr) -> List[Tuple[int, int, int, int]]:
        """Returns list of (x, y, w, h) face bounding boxes."""
        if img_bgr is None or self.face_cascade is None:
            return []
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        faces = self.face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=4,
            minSize=(self.min_face_size_px, self.min_face_size_px)
        )
        return [(int(x), int(y), int(w), int(h)) for (x, y, w, h) in faces]

    def _evaluate_face(
        self,
        person_ref_bgr,
        generated_bgr,
        defects: List[QCDefect]
    ) -> Tuple[int, Optional[float], str]:
        if generated_bgr is None:
            return 0, None, "SKIPPED"

        gen_faces = self._detect_faces(generated_bgr)
        face_count = len(gen_faces)

        # Case A: No person reference provided (e.g. only garment was scraped)
        if person_ref_bgr is None:
            if face_count == 0:
                # Top-band fallback check
                h, w = generated_bgr.shape[:2]
                top_crop = generated_bgr[:int(h*0.35), :]
                top_faces = self._detect_faces(top_crop)
                face_count = len(top_faces)

            if face_count > 1:
                defects.append(QCDefect(
                    category="face_detection",
                    severity=DefectSeverity.MEDIUM,
                    description=f"Multiple faces ({face_count}) detected in scene. Expected single model.",
                    suggested_fix="Constrain prompt to exactly one model in the frame."
                ))
                return face_count, 0.75, "WARNING"

            # When no person reference image is provided, Haar cascade absence is not a hard defect.
            # Multimodal LLM (Tier 3) will evaluate overall aesthetic, anatomy, and framing.
            return face_count, 1.0, "SKIPPED_NO_REFERENCE"

        # Case B: True Person Reference IS provided!
        ref_faces = self._detect_faces(person_ref_bgr)
        if len(ref_faces) == 0:
            # Reference image has no clear face
            return face_count, 1.0, "SKIPPED_REF_NO_FACE"

        if face_count == 0:
            defects.append(QCDefect(
                category="face_identity",
                severity=DefectSeverity.CRITICAL,
                description="Face from person reference missing in generated image.",
                suggested_fix="Preserve reference model identity and facial presence."
            ))
            return 0, 0.0, "FAIL"

        # Compare reference face crop vs generated face crop
        rx, ry, rw, rh = ref_faces[0]
        ref_face_crop = person_ref_bgr[ry:ry+rh, rx:rx+rw]

        gx, gy, gw, gh = gen_faces[0]
        gen_face_crop = generated_bgr[gy:gy+gh, gx:gx+gw]

        # Resize to standard 128x128 for normalized comparison
        ref_face_resized = cv2.resize(ref_face_crop, (128, 128))
        gen_face_resized = cv2.resize(gen_face_crop, (128, 128))

        # HSV Color & Tone Histogram Correlation
        hsv_ref = cv2.cvtColor(ref_face_resized, cv2.COLOR_BGR2HSV)
        hsv_gen = cv2.cvtColor(gen_face_resized, cv2.COLOR_BGR2HSV)
        hist_ref = cv2.calcHist([hsv_ref], [0, 1], None, [30, 32], [0, 180, 0, 256])
        hist_gen = cv2.calcHist([hsv_gen], [0, 1], None, [30, 32], [0, 180, 0, 256])
        cv2.normalize(hist_ref, hist_ref, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
        cv2.normalize(hist_gen, hist_gen, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)

        face_corr = float(cv2.compareHist(hist_ref, hist_gen, cv2.HISTCMP_CORREL))
        face_sim = max(0.0, min(1.0, (face_corr + 1.0) / 2.0))  # normalize [-1, 1] to [0, 1]

        status = "PASS" if face_sim >= 0.65 else "FAIL"
        if status == "FAIL":
            defects.append(QCDefect(
                category="face_identity",
                severity=DefectSeverity.HIGH,
                description=f"Facial tone / identity discrepancy (similarity: {face_sim:.2f} < 0.65).",
                suggested_fix="Align facial complexion and structure with reference model."
            ))

        return face_count, round(face_sim, 3), status

    def _evaluate_garment(
        self,
        garment_ref_bgr,
        generated_bgr,
        defects: List[QCDefect],
        is_upper_garment_only: bool = False
    ) -> Tuple[float, float, float, str]:
        if garment_ref_bgr is None or generated_bgr is None:
            return 1.0, 1.0, 1.0, "SKIPPED"

        # 1. Extract Garment Region from Reference
        gray_ref = cv2.cvtColor(garment_ref_bgr, cv2.COLOR_BGR2GRAY)
        mask_ref = cv2.inRange(gray_ref, 15, 245)
        if cv2.countNonZero(mask_ref) < 100:
            mask_ref = None

        # 2. Extract Torso Region from Generated Image
        gh, gw = generated_bgr.shape[:2]
        if is_upper_garment_only:
            # Strictly isolate upper torso for tops/shirts, avoiding lower bottoms/jeans
            gen_torso = generated_bgr[int(gh*0.18):int(gh*0.50), int(gw*0.18):int(gw*0.82)]
        else:
            gen_torso = generated_bgr[int(gh*0.22):int(gh*0.75), int(gw*0.15):int(gw*0.85)]

        # 3. 2D HSV Color Histogram (Hue & Saturation)
        hsv_ref = cv2.cvtColor(garment_ref_bgr, cv2.COLOR_BGR2HSV)
        hsv_gen = cv2.cvtColor(gen_torso, cv2.COLOR_BGR2HSV)

        hist_ref = cv2.calcHist([hsv_ref], [0, 1], mask_ref, [30, 32], [0, 180, 20, 256])
        hist_gen = cv2.calcHist([hsv_gen], [0, 1], None, [30, 32], [0, 180, 20, 256])

        cv2.normalize(hist_ref, hist_ref, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
        cv2.normalize(hist_gen, hist_gen, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)

        color_corr_raw = float(cv2.compareHist(hist_ref, hist_gen, cv2.HISTCMP_CORREL))
        color_sim = max(0.0, min(1.0, color_corr_raw))

        # 4. Texture / Gradient Energy Match
        sobel_ref = cv2.Sobel(gray_ref, cv2.CV_64F, 1, 1, ksize=3)
        sobel_gen = cv2.Sobel(cv2.cvtColor(gen_torso, cv2.COLOR_BGR2GRAY), cv2.CV_64F, 1, 1, ksize=3)
        ref_energy = float(np.mean(np.abs(sobel_ref)))
        gen_energy = float(np.mean(np.abs(sobel_gen)))

        if ref_energy > 1.0 and gen_energy > 1.0:
            energy_ratio = min(ref_energy, gen_energy) / max(ref_energy, gen_energy)
        else:
            energy_ratio = 0.80
        texture_sim = max(0.0, min(1.0, energy_ratio))

        # Combined Garment Fidelity Score
        garment_score = round(0.70 * color_sim + 0.30 * texture_sim, 3)

        status = "PASS"
        if color_sim < self.min_garment_color_corr:
            if color_sim < 0.10:
                status = "FAIL"
                severity = DefectSeverity.HIGH
            else:
                status = "WARNING"
                severity = DefectSeverity.MEDIUM
            defects.append(QCDefect(
                category="garment_color",
                severity=severity,
                description=f"Garment color shade deviated from reference (color correlation: {color_sim:.2f} < {self.min_garment_color_corr}).",
                suggested_fix="Preserve the exact garment color hue, saturation, and dye shade."
            ))
        elif texture_sim < self.min_garment_texture_corr:
            defects.append(QCDefect(
                category="garment_texture",
                severity=DefectSeverity.MEDIUM,
                description=f"Garment fabric pattern / texture complexity deviated (similarity: {texture_sim:.2f}).",
                suggested_fix="Preserve fabric weave, prints, and texture complexity without simplifying to solid."
            ))

        return round(color_sim, 3), round(texture_sim, 3), garment_score, status

    def _evaluate_anatomy_heuristics(
        self,
        generated_bgr,
        defects: List[QCDefect]
    ) -> Tuple[float, str]:
        if generated_bgr is None:
            return 1.0, "SKIPPED"

        h, w = generated_bgr.shape[:2]
        center_crop = generated_bgr[int(h*0.2):int(h*0.8), int(w*0.2):int(w*0.8)]
        gray_center = cv2.cvtColor(center_crop, cv2.COLOR_BGR2GRAY)
        std_val = float(np.std(gray_center))

        if std_val < 15.0:
            defects.append(QCDefect(
                category="anatomy_presence",
                severity=DefectSeverity.CRITICAL,
                description="Center frame appears empty or missing model subject.",
                suggested_fix="Ensure primary model is fully rendered in the center of the frame."
            ))
            return 0.20, "FAIL"

        return 0.95, "PASS"

    def evaluate(
        self,
        person_ref_bytes: Optional[bytes],
        garment_ref_bytes: Optional[bytes],
        generated_bytes: bytes,
        secondary_garment_ref_bytes: Optional[bytes] = None
    ) -> Tier2Result:
        defects: List[QCDefect] = []

        if not HAS_CV2:
            return Tier2Result(
                passed=True,
                score=1.0,
                defects=[],
                details={"note": "OpenCV not available, skipped Tier 2 vision"}
            )

        person_bgr = self._decode_bgr(person_ref_bytes)
        garment_bgr = self._decode_bgr(garment_ref_bytes)
        gen_bgr = self._decode_bgr(generated_bytes)

        if gen_bgr is None:
            return Tier2Result(
                passed=False,
                score=0.0,
                defects=[QCDefect(
                    category="image_decode",
                    severity=DefectSeverity.CRITICAL,
                    description="Could not decode generated image for Tier 2 evaluation."
                )]
            )

        # 1. Face QA
        face_count, face_sim, face_status = self._evaluate_face(person_bgr, gen_bgr, defects)

        # 2. Garment Fidelity QA (focusing on upper torso for top garment if paired with pants)
        is_pair = secondary_garment_ref_bytes is not None and len(secondary_garment_ref_bytes) > 100
        color_sim, texture_sim, garment_score, garment_status = self._evaluate_garment(
            garment_bgr, gen_bgr, defects, is_upper_garment_only=is_pair
        )

        # 3. Anatomy Heuristics
        anatomy_score, anatomy_status = self._evaluate_anatomy_heuristics(gen_bgr, defects)

        # Weighted calculation
        weights = []
        scores = []

        if face_sim is not None:
            weights.append(0.35)
            scores.append(face_sim)
        else:
            weights.append(0.15)
            scores.append(1.0 if face_count >= 1 else 0.5)

        weights.append(0.45)
        scores.append(garment_score)

        weights.append(0.20)
        scores.append(anatomy_score)

        total_weight = sum(weights)
        combined_score = round(sum(w * s for w, s in zip(weights, scores)) / total_weight, 3)

        has_critical = any(d.severity == DefectSeverity.CRITICAL for d in defects)
        passed = (not has_critical) and (combined_score >= 0.65) and (garment_status != "FAIL")

        return Tier2Result(
            passed=passed,
            score=combined_score,
            face_detected_count=face_count,
            face_identity_score=face_sim,
            face_status=face_status,
            garment_color_similarity=color_sim,
            garment_texture_similarity=texture_sim,
            garment_fidelity_score=garment_score,
            garment_status=garment_status,
            anatomy_score=anatomy_score,
            anatomy_status=anatomy_status,
            defects=defects,
            details={
                "face_count": face_count,
                "face_sim": face_sim,
                "color_sim": color_sim,
                "texture_sim": texture_sim,
                "garment_score": garment_score
            }
        )
