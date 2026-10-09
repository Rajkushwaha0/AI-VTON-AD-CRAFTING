#!/usr/bin/env python3
"""
Comprehensive Automated Test Suite for V2 Quality Control (QC) & VTON Check Layer.
Tests:
1. Tier 1 Deterministic Fast-Fail (Corruptions, Laplacian Blur, Exposure).
2. Tier 2 Vision Evaluators (Garment HSV Color Correlation, Face Detection).
3. Tier 4 Decision Engine (PASS, RETRY, MANUAL_REVIEW routing & repair prompt synthesis).
4. QCQualityGatekeeperV2 end-to-end integration & short-circuit fail-fast behavior.
5. V1 vs V2 dual-engine compatibility verification.
"""

import io
import sys
import os
import unittest
import numpy as np
import cv2
from PIL import Image

# Ensure pipeline root is in sys.path
pipeline_dir = os.path.dirname(os.path.abspath(__file__))
if pipeline_dir not in sys.path:
    sys.path.insert(0, pipeline_dir)

from src.services.qc_v2.models import (
    QCAction,
    DefectSeverity,
    QCDefect,
    Tier1Result,
    Tier2Result,
    AggregatedQCReportV2
)
from src.services.qc_v2.tier1_deterministic_qc import Tier1DeterministicQC
from src.services.qc_v2.tier2_vision_evaluators import Tier2VisionEvaluators
from src.services.qc_v2.decision_engine import QCDecisionEngine
from src.services.qc_v2.gatekeeper_v2 import QCQualityGatekeeperV2
from src.services.qc_inspector import QCQualityGatekeeper as QCQualityGatekeeperV1
from src.services.vton_image_gen import ParallelImageGenerator


def create_synthetic_image(
    width: int = 768, 
    height: int = 1024, 
    color: tuple = (120, 150, 180),
    add_texture: bool = True,
    blur: bool = False
) -> bytes:
    """Generates synthetic JPEG bytes with optional high-frequency texture and facial/torso features."""
    img = np.full((height, width, 3), color, dtype=np.uint8)
    
    if add_texture:
        # Add high-frequency gradient edges (simulating fabric, model silhouette)
        cv2.rectangle(img, (int(width*0.25), int(height*0.25)), (int(width*0.75), int(height*0.75)), (40, 90, 160), -1)
        # Add high contrast lines (simulating pattern)
        for y in range(int(height*0.3), int(height*0.7), 20):
            cv2.line(img, (int(width*0.25), y), (int(width*0.75), y), (200, 220, 255), 2)
            
    if blur:
        img = cv2.GaussianBlur(img, (35, 35), 15.0)

    _, encoded = cv2.imencode(".jpg", img)
    return encoded.tobytes()


class TestQCV2DeterministicTier1(unittest.TestCase):
    def setUp(self):
        self.t1 = Tier1DeterministicQC(
            min_width=512,
            min_height=512,
            blur_threshold_full=40.0
        )

    def test_clean_sharp_image_passes(self):
        sharp_bytes = create_synthetic_image(width=768, height=1024, add_texture=True, blur=False)
        result = self.t1.evaluate(sharp_bytes, target_aspect_ratio="9:16")
        self.assertTrue(result.passed, f"Sharp image should pass Tier 1. Defects: {result.defects}")
        self.assertGreaterEqual(result.score, 0.70)
        self.assertGreater(result.full_laplacian_sharpness, 40.0)

    def test_blurry_image_fails_fast(self):
        blurry_bytes = create_synthetic_image(width=768, height=1024, add_texture=True, blur=True)
        result = self.t1.evaluate(blurry_bytes, target_aspect_ratio="9:16")
        self.assertFalse(result.passed, "Heavily blurred image must fail Tier 1")
        blur_defects = [d for d in result.defects if d.category == "blur"]
        self.assertGreater(len(blur_defects), 0, "Expected blur defect to be recorded")
        self.assertLess(result.full_laplacian_sharpness, 40.0)

    def test_corrupted_empty_stream_fails(self):
        corrupted_bytes = b"not_an_image"
        result = self.t1.evaluate(corrupted_bytes)
        self.assertFalse(result.passed)
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.defects[0].severity, DefectSeverity.CRITICAL)


class TestQCV2VisionTier2(unittest.TestCase):
    def setUp(self):
        self.t2 = Tier2VisionEvaluators(min_garment_color_corr=0.60)

    def test_garment_color_matching(self):
        # Emerald green garment reference
        garment_ref = create_synthetic_image(width=512, height=512, color=(40, 160, 60), add_texture=False)
        # Matching green VTON generated image
        matching_vton = create_synthetic_image(width=768, height=1024, color=(40, 160, 60), add_texture=False)
        # Mismatched red VTON generated image
        mismatched_vton = create_synthetic_image(width=768, height=1024, color=(200, 30, 30), add_texture=False)

        match_res = self.t2.evaluate(person_ref_bytes=None, garment_ref_bytes=garment_ref, generated_bytes=matching_vton)
        self.assertGreaterEqual(match_res.garment_color_similarity, 0.70, "Matching color should have high correlation")

        mismatch_res = self.t2.evaluate(person_ref_bytes=None, garment_ref_bytes=garment_ref, generated_bytes=mismatched_vton)
        self.assertLess(mismatch_res.garment_color_similarity, 0.40, "Mismatched color should have low correlation")
        self.assertEqual(mismatch_res.garment_status, "FAIL")


class TestQCV2DecisionEngine(unittest.TestCase):
    def setUp(self):
        self.engine = QCDecisionEngine(pass_threshold=0.78, max_attempts=3)

    def test_action_pass_when_clean(self):
        t1 = Tier1Result(passed=True, score=0.95, full_laplacian_sharpness=120.0)
        t2 = Tier2Result(passed=True, score=0.90, garment_color_similarity=0.92, garment_status="PASS")
        report = self.engine.decide(tier1=t1, tier2=t2, tier3=None, attempt_number=1)
        self.assertEqual(report.action, QCAction.PASS)
        self.assertTrue(report.overall_passed)
        self.assertGreaterEqual(report.overall_score, 0.78)

    def test_action_retry_synthesizes_repairs(self):
        t1 = Tier1Result(passed=True, score=0.85)
        t2 = Tier2Result(
            passed=False, 
            score=0.55, 
            garment_status="FAIL",
            defects=[QCDefect(
                category="garment_color", 
                severity=DefectSeverity.HIGH, 
                description="Garment color deviated to dark grey",
                suggested_fix="Preserve vibrant emerald green dye shade"
            )]
        )
        report = self.engine.decide(tier1=t1, tier2=t2, tier3=None, attempt_number=1)
        self.assertEqual(report.action, QCAction.RETRY)
        self.assertFalse(report.overall_passed)
        self.assertIsNotNone(report.targeted_repair_prompt)
        self.assertIn("emerald green", report.targeted_repair_prompt)
        self.assertTrue(any("wrong garment color" in item for item in report.negative_prompt_additions))

    def test_action_manual_review_when_retries_exhausted(self):
        t1 = Tier1Result(passed=True, score=0.75)
        t2 = Tier2Result(passed=False, score=0.60, garment_status="FAIL")
        # Attempt 3 (max_attempts = 3)
        report = self.engine.decide(tier1=t1, tier2=t2, tier3=None, attempt_number=3)
        self.assertEqual(report.action, QCAction.MANUAL_REVIEW)
        self.assertIn("Max retries", report.summary_reason)


class TestQCGatekeeperV2Integration(unittest.TestCase):
    def setUp(self):
        self.gatekeeper_v2 = QCQualityGatekeeperV2()

    def test_fail_fast_on_blurry_image(self):
        blurry_bytes = create_synthetic_image(width=768, height=1024, add_texture=True, blur=True)
        garment_bytes = create_synthetic_image(width=512, height=512, color=(50, 100, 150))

        report = self.gatekeeper_v2.evaluate_vton(
            garment_ref_bytes=garment_bytes,
            generated_bytes=blurry_bytes,
            person_ref_bytes=None,
            client=None,
            attempt_number=1
        )
        self.assertEqual(report.action, QCAction.RETRY)
        self.assertFalse(report.overall_passed)
        self.assertIsNone(report.tier2, "Tier 2 should be skipped in fail-fast short circuit")
        self.assertIsNone(report.tier3, "Tier 3 should be skipped in fail-fast short circuit")

    def test_v1_and_v2_coexistence(self):
        # V1 Generator
        gen_v1 = ParallelImageGenerator(qc_version="v1")
        self.assertEqual(gen_v1.qc_version, "v1")
        self.assertIsInstance(gen_v1.gatekeeper, QCQualityGatekeeperV1)

        # V2 Generator
        gen_v2 = ParallelImageGenerator(qc_version="v2")
        self.assertEqual(gen_v2.qc_version, "v2")
        self.assertIsInstance(gen_v2.gatekeeper_v2, QCQualityGatekeeperV2)


if __name__ == "__main__":
    print("\n=======================================================")
    print("  RUNNING V2 QC & VTON EVALUATION TEST SUITE")
    print("=======================================================\n")
    unittest.main(verbosity=2)
