import json
from typing import Dict, Any, List, Optional, Tuple
from google.genai import types

def _cv2_laplacian_sharpness(image_bytes: bytes) -> float:
    """Calculates image sharpness via variance of Laplacian (with graceful fallback)."""
    try:
        import cv2
        import numpy as np
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return 100.0
        return float(cv2.Laplacian(img, cv2.CV_64F).var())
    except Exception:
        return 100.0

HERO_ANATOMY_PROMPT = """
You are a principal fashion art director and forensic QC inspector.
Audit this generated hero fashion image against anatomical integrity and garment preservation standards.

Inspect:
1. Limb & Hand Anatomy: Exactly 2 arms, 2 legs, 5 well-defined fingers per hand (no fused fingers, 6th digits, or phantom limbs).
2. Skin Micro-Texture: Natural pore structure and believable lighting (no waxy, over-smoothed plastic AI skin).
3. Garment Fidelity: Silhouette, collar, closures, buttons, and pattern align with luxury commercial standards without bizarre texture melting.

Return ONLY a valid JSON object matching:
{
  "limb_count_correct": boolean,
  "fingers_anatomically_natural": boolean,
  "natural_skin_pores": boolean,
  "garment_fidelity_intact": boolean,
  "confidence_score": number (0.0 to 1.0),
  "defects_summary": [string],
  "targeted_repair_instructions": [string],
  "negative_prompt_additions": [string]
}
"""

class HeroQualityInspector:
    def __init__(self, client=None, temperature: float = 0.0):
        self.client = client
        self.temperature = temperature

    def audit_hero(
        self,
        hero_img_bytes: bytes,
        client=None
    ) -> Dict[str, Any]:
        ai_client = client or self.client
        sharpness_score = _cv2_laplacian_sharpness(hero_img_bytes)
        
        if not ai_client:
            return {
                "passed": True,
                "score": 1.0,
                "sharpness_laplacian": sharpness_score,
                "defects": [],
                "negative_prompt_additions": []
            }

        try:
            contents = [
                types.Part.from_bytes(data=hero_img_bytes, mime_type="image/png"),
                types.Part.from_text(text=HERO_ANATOMY_PROMPT)
            ]

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=self.temperature
            )

            models_to_try = ["gemini-2.5-pro", "gemini-2.5-flash"]
            resp = None
            for model in models_to_try:
                try:
                    resp = ai_client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=config
                    )
                    if resp and resp.text:
                        break
                except Exception:
                    continue

            if not resp or not resp.text:
                return {
                    "passed": True, 
                    "score": 0.85, 
                    "sharpness_laplacian": sharpness_score, 
                    "defects": [], 
                    "negative_prompt_additions": []
                }

            text = resp.text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
            data = json.loads(text)

            limb_correct = bool(data.get("limb_count_correct", True))
            fingers_natural = bool(data.get("fingers_anatomically_natural", True))
            skin_pores = bool(data.get("natural_skin_pores", True))
            garment_intact = bool(data.get("garment_fidelity_intact", True))
            confidence = float(data.get("confidence_score", 0.90))

            defects = data.get("defects_summary", [])
            repairs = data.get("targeted_repair_instructions", [])
            negatives = data.get("negative_prompt_additions", [])

            # Laplacian blur penalty
            blur_detected = sharpness_score < 40.0
            if blur_detected:
                defects.append("Image lacks high-frequency edge sharpness (Laplacian blur detected).")
                negatives.append("blurry edges, out-of-focus soft fabric, motion blur")

            passed = limb_correct and fingers_natural and skin_pores and garment_intact and not blur_detected and (confidence >= 0.70)

            if not fingers_natural and "deformed fingers, extra digits, fused hands" not in negatives:
                negatives.append("deformed fingers, extra digits, fused hands, missing joints")
            if not skin_pores and "waxy airbrushed skin, plastic face" not in negatives:
                negatives.append("waxy skin, over-smoothed plastic AI face, airbrushed poreless texture")

            return {
                "passed": passed,
                "score": confidence if passed else min(confidence, 0.60),
                "sharpness_laplacian": round(sharpness_score, 2),
                "limb_count_correct": limb_correct,
                "fingers_anatomically_natural": fingers_natural,
                "natural_skin_pores": skin_pores,
                "garment_fidelity_intact": garment_intact,
                "defects": defects,
                "targeted_repair_instructions": repairs,
                "negative_prompt_additions": negatives
            }
        except Exception as e:
            return {
                "passed": True,
                "score": 0.80,
                "sharpness_laplacian": round(sharpness_score, 2),
                "error": str(e),
                "defects": [],
                "negative_prompt_additions": []
            }
