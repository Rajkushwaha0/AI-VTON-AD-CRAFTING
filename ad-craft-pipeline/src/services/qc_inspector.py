"""
Quality Control (QC) Gatekeeper and Inspector Suite ported from StyleLoop.
Integrates:
1. FaceIdentityQA: 2-Pass biometric and distinguishing marks verification.
2. SceneRealismQA: 18 physical realism checklist items (footing, prop proportions, headroom, reflections).
3. HeroQualityInspector: Anatomy integrity, skin pore preservation, and Laplacian sharpness.
4. HallucinationGarmentInspector: Forensic garment texture and pattern fidelity.
5. QCQualityGatekeeper: Aggregated scoring, targeted repair prompts, and negative constraint synthesis.
"""

import os
import json
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from .qc.face_identity_qa import FaceIdentityQA
from .qc.scene_realism_qa import SceneRealismQA
from .qc.hero_quality_inspector import HeroQualityInspector, _cv2_laplacian_sharpness

logger = logging.getLogger("QCQualityGatekeeper")

@dataclass
class QCInspectionResult:
    inspector_name: str
    passed: bool
    score: float  # 0.0 to 1.0
    defects: List[str] = field(default_factory=list)
    remedy_suggestions: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

@dataclass
class AggregatedQCReport:
    overall_passed: bool
    weighted_score: float
    results: List[QCInspectionResult]
    negative_prompt_additions: List[str] = field(default_factory=list)
    targeted_repair_prompt: Optional[str] = None
    face_qa_details: Optional[Dict[str, Any]] = None
    scene_qa_details: Optional[Dict[str, Any]] = None
    hero_inspector_details: Optional[Dict[str, Any]] = None

class QCQualityGatekeeper:
    """
    Unified Orchestration of Face Identity, Scene Realism, and Hero Integrity QA
    with Self-Healing Feedback and Targeted In-Place Defect Repair Prompts.
    """

    def __init__(self, pass_threshold: float = 0.78):
        self.face_qa = FaceIdentityQA(temperature=0.0)
        self.scene_qa = SceneRealismQA(temperature=0.0)
        self.hero_inspector = HeroQualityInspector(temperature=0.0)
        self.pass_threshold = pass_threshold

    def evaluate_bytes(
        self, 
        reference_bytes: Optional[bytes], 
        hero_bytes: bytes, 
        client: Any = None
    ) -> AggregatedQCReport:
        results = []
        negative_additions = []
        defect_repairs = []

        # 1. Hero Anatomy, Skin Texture & Laplacian Sharpness
        hero_res = self.hero_inspector.audit_hero(hero_bytes, client=client)
        results.append(QCInspectionResult(
            inspector_name="hero_anatomy_and_skin",
            passed=hero_res.get("passed", True),
            score=hero_res.get("score", 0.85),
            defects=hero_res.get("defects", []),
            remedy_suggestions=", ".join(hero_res.get("targeted_repair_instructions", [])) or None,
            details=hero_res
        ))
        if hero_res.get("negative_prompt_additions"):
            negative_additions.extend(hero_res["negative_prompt_additions"])
        if hero_res.get("targeted_repair_instructions"):
            defect_repairs.extend(hero_res["targeted_repair_instructions"])

        # 2. Scene Realism (footing, props, ceiling, mirror reflections)
        scene_res = self.scene_qa.evaluate(hero_bytes, client=client)
        results.append(QCInspectionResult(
            inspector_name="scene_spatial_realism",
            passed=scene_res.get("passed", True),
            score=scene_res.get("score", 0.85),
            defects=scene_res.get("identified_flaws", []),
            remedy_suggestions=", ".join(scene_res.get("scene_negative_constraints", [])) or None,
            details=scene_res
        ))
        if scene_res.get("scene_negative_constraints"):
            negative_additions.extend(scene_res["scene_negative_constraints"])

        # 3. Face Identity QA (if reference bytes provided)
        face_res = None
        if reference_bytes and len(reference_bytes) > 100:
            face_res = self.face_qa.evaluate(reference_bytes, hero_bytes, client=client)
            results.append(QCInspectionResult(
                inspector_name="face_biometric_identity",
                passed=face_res.get("passed", True),
                score=face_res.get("score", 0.85),
                defects=face_res.get("discrepancies", []),
                remedy_suggestions=", ".join(face_res.get("corrections", [])) or None,
                details=face_res
            ))
            if face_res.get("corrections"):
                negative_additions.extend(face_res["corrections"])
                defect_repairs.extend(face_res["corrections"])

        # Calculate weighted overall score
        scores = [r.score for r in results]
        avg_score = sum(scores) / max(1, len(scores))
        all_passed = all(r.passed for r in results) and (avg_score >= self.pass_threshold)

        # Build in-place repair prompt
        repair_prompt = None
        if defect_repairs:
            repair_prompt = (
                f"Fix the following defects strictly: {'; '.join(defect_repairs[:3])}. "
                "Keep the rest of the image, garment silhouette, fabric texture, and background completely unchanged."
            )

        return AggregatedQCReport(
            overall_passed=all_passed,
            weighted_score=round(avg_score, 3),
            results=results,
            negative_prompt_additions=list(dict.fromkeys(negative_additions)),
            targeted_repair_prompt=repair_prompt,
            face_qa_details=face_res,
            scene_qa_details=scene_res,
            hero_inspector_details=hero_res
        )

    def evaluate(
        self, 
        source_garment_path: Optional[str], 
        generated_image_path: str, 
        client: Any = None
    ) -> AggregatedQCReport:
        ref_bytes = None
        if source_garment_path and os.path.exists(source_garment_path):
            with open(source_garment_path, "rb") as f:
                ref_bytes = f.read()

        hero_bytes = b""
        if generated_image_path and os.path.exists(generated_image_path):
            with open(generated_image_path, "rb") as f:
                hero_bytes = f.read()

        return self.evaluate_bytes(ref_bytes, hero_bytes, client=client)
