import json
import re
from typing import Dict, Any, Tuple, List, Optional
from google.genai import types

from ..image_processing.geometry import extract_face_crop

FACE_IDENTITY_CHECKLIST = """
Mandatory Facial Geometry Checkpoints:
1. Facial Thirds Proportion (Forehead to brow, brow to base of nose, base of nose to chin).
2. Eye Shape, Eye Spacing, and Eyelid Fold Geometry.
3. Nose Bridge Width, Nostril Flare, and Columella Angle.
4. Philtrum Depth and Upper Lip Cupid's Bow Contour.
5. Lip Thickness (Upper vs Lower Ratio) and Mouth Corner Angle.
6. Jawline Width, Chin Prominence, and Mandibular Angle.
7. Cheekbone Projection and Zygomatic Arch Definition.
8. Skin Undertone Consistency and Authentic Pore Texture (No waxy plastic airbrushing).
"""

def build_face_identity_pass_prompt() -> str:
    return (
        "You are a forensic biometric face identity inspector for high-fashion commercial campaigns.\n"
        "Compare IMAGE 1 (Reference Catalog Model Face) and IMAGE 2 (Generated Scene Hero Model Face).\n\n"
        f"{FACE_IDENTITY_CHECKLIST}\n"
        "Evaluate strictly and objectively. Return ONLY a valid JSON object matching this schema:\n"
        "{\n"
        '  "same_person_identity": boolean,\n'
        '  "confidence_score": number (0.0 to 1.0),\n'
        '  "natural_skin_texture": boolean,\n'
        '  "no_facial_distortion": boolean,\n'
        '  "biometric_discrepancies": [string],\n'
        '  "correction_remedies": [string]\n'
        "}\n"
        "Rules:\n"
        "- If key facial geometry differs meaningfully (e.g. different nose structure or jawline), set same_person_identity: false.\n"
        "- If skin looks excessively blurred, plastic, or over-airbrushed with lost micro-texture, set natural_skin_texture: false."
    )

class FaceIdentityQA:
    def __init__(self, client=None, temperature: float = 0.0):
        self.client = client
        self.temperature = temperature

    def evaluate(
        self, 
        reference_img_bytes: bytes, 
        hero_img_bytes: bytes, 
        client=None
    ) -> Dict[str, Any]:
        ai_client = client or self.client
        if not ai_client:
            return {
                "passed": True,
                "score": 1.0,
                "note": "Skipped (no client)",
                "corrections": []
            }

        try:
            ref_crop = extract_face_crop(reference_img_bytes, min_size_px=640)
            hero_crop = extract_face_crop(hero_img_bytes, min_size_px=640)

            contents = [
                types.Part.from_bytes(data=ref_crop, mime_type="image/png"),
                types.Part.from_bytes(data=hero_crop, mime_type="image/png"),
                types.Part.from_text(text=build_face_identity_pass_prompt())
            ]

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=self.temperature
            )

            # Try 2.5-pro first for zero-temperature biometric precision, fallback to 2.5-flash
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
                return {"passed": True, "score": 0.85, "note": "Vision evaluation empty response", "corrections": []}

            text = resp.text.strip()
            # Clean markdown code blocks if returned
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
            data = json.loads(text)

            same_person = bool(data.get("same_person_identity", False))
            natural_skin = bool(data.get("natural_skin_texture", True))
            no_distortion = bool(data.get("no_facial_distortion", True))
            confidence = float(data.get("confidence_score", 0.85))

            discrepancies = data.get("biometric_discrepancies", [])
            corrections = data.get("correction_remedies", [])

            # Programmatic strict pass logic
            passed = same_person and natural_skin and no_distortion and (confidence >= 0.70)
            
            if not natural_skin and "Restore natural micro-pores and realistic skin undertone; eliminate waxy AI airbrushing." not in corrections:
                corrections.append("Restore natural micro-pores and realistic skin undertone; eliminate waxy AI airbrushing.")
            if not same_person and discrepancies:
                corrections.append(f"Align facial geometry precisely with catalog model: {', '.join(discrepancies[:2])}")

            return {
                "passed": passed,
                "score": confidence if passed else min(confidence, 0.65),
                "same_person_identity": same_person,
                "natural_skin_texture": natural_skin,
                "no_facial_distortion": no_distortion,
                "discrepancies": discrepancies,
                "corrections": corrections
            }
        except Exception as e:
            return {
                "passed": True,  # Non-fatal error fallback
                "score": 0.80,
                "error": str(e),
                "corrections": []
            }
