import os
import logging
from typing import Optional, Dict, Any, List

from .models import (
    QCAction,
    AggregatedQCReportV2,
    Tier1Result,
    Tier2Result,
    Tier3Result
)
from .tier1_deterministic_qc import Tier1DeterministicQC
from .tier2_vision_evaluators import Tier2VisionEvaluators
from .tier3_multimodal_judge import Tier3MultimodalJudge
from .decision_engine import QCDecisionEngine

logger = logging.getLogger("QCQualityGatekeeperV2")

class QCQualityGatekeeperV2:
    """
    V2 Next-Gen Quality Control Gatekeeper & VTON Inspector Suite.
    Features:
    1. Tier 1: Fast Deterministic OpenCV Blur, Resolution, Exposure, and Contrast.
       - Rejects bad images immediately to avoid wasted model/API calls.
    2. Tier 2: Real Face Detection, 2D HSV Garment Color & Texture Fidelity, Anatomy Heuristics.
       - Correctly separates person reference from garment reference!
    3. Tier 3: Multimodal LLM-as-a-Judge for semantic details, hand/finger anatomy, and physical realism.
    4. Tier 4: Deterministic Decision Engine & Action Routing (PASS, RETRY, MANUAL_REVIEW).
    """

    def __init__(
        self,
        pass_threshold: float = 0.75,
        max_attempts: int = 3,
        temperature: float = 0.0
    ):
        self.tier1_engine = Tier1DeterministicQC()
        self.tier2_engine = Tier2VisionEvaluators()
        self.tier3_engine = Tier3MultimodalJudge(temperature=temperature)
        self.decision_engine = QCDecisionEngine(
            pass_threshold=pass_threshold,
            max_attempts=max_attempts
        )

    def evaluate_vton(
        self,
        garment_ref_bytes: Optional[bytes],
        generated_bytes: bytes,
        person_ref_bytes: Optional[bytes] = None,
        secondary_garment_ref_bytes: Optional[bytes] = None,
        client: Any = None,
        target_aspect_ratio: str = "9:16",
        attempt_number: int = 1
    ) -> AggregatedQCReportV2:
        logger.info(f"Running V2 QC Audit (Attempt {attempt_number})...")

        # 1. Tier 1 Fast Deterministic Check
        tier1_res = self.tier1_engine.evaluate(
            image_bytes=generated_bytes,
            target_aspect_ratio=target_aspect_ratio
        )

        # Fail-Fast Short Circuit:
        # If image is corrupted, below minimum resolution, or severely blurry,
        # skip expensive vision & LLM APIs!
        if not tier1_res.passed:
            logger.warning(f"Tier 1 fast check failed (Score: {tier1_res.score}). Short-circuiting AI calls.")
            return self.decision_engine.decide(
                tier1=tier1_res,
                tier2=None,
                tier3=None,
                attempt_number=attempt_number
            )

        # 2. Tier 2 Specialized CV & Color/Texture Evaluators
        tier2_res = self.tier2_engine.evaluate(
            person_ref_bytes=person_ref_bytes,
            garment_ref_bytes=garment_ref_bytes,
            generated_bytes=generated_bytes,
            secondary_garment_ref_bytes=secondary_garment_ref_bytes
        )

        # 3. Tier 3 Multimodal LLM-as-a-Judge (Fast Selective 2.5-flash)
        tier3_res = None
        if client:
            tier3_res = self.tier3_engine.evaluate(
                person_ref_bytes=person_ref_bytes,
                garment_ref_bytes=garment_ref_bytes,
                generated_bytes=generated_bytes,
                secondary_garment_ref_bytes=secondary_garment_ref_bytes,
                client=client
            )

        # 4. Tier 4 Decision Engine & Action Routing
        report = self.decision_engine.decide(
            tier1=tier1_res,
            tier2=tier2_res,
            tier3=tier3_res,
            attempt_number=attempt_number
        )

        logger.info(f"V2 QC Report: Action={report.action.value}, Score={report.overall_score:.2f}, Defects={len(report.all_defects)}")
        return report

    def evaluate_bytes(
        self,
        reference_bytes: Optional[bytes],
        hero_bytes: bytes,
        client: Any = None,
        attempt_number: int = 1
    ) -> AggregatedQCReportV2:
        """
        Drop-in compatible method with V1 interface.
        In V2, reference_bytes is treated as garment_ref_bytes by default.
        """
        return self.evaluate_vton(
            garment_ref_bytes=reference_bytes,
            generated_bytes=hero_bytes,
            person_ref_bytes=None,
            client=client,
            attempt_number=attempt_number
        )
