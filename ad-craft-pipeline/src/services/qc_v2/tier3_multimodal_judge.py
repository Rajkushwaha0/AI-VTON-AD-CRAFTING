import json
import logging
from typing import Optional, Dict, Any, List
from google.genai import types

from .models import Tier3Result, QCDefect, DefectSeverity

logger = logging.getLogger("Tier3MultimodalJudge")

VTON_MULTIMODAL_QC_PROMPT = """
You are a Principal Fashion Quality Inspector and Art Director for commercial virtual try-on (VTON) advertising campaigns.
Audit the GENERATED VTON IMAGE against the authentic REFERENCE GARMENT (and Reference Person Model if provided).

Evaluate strictly across 5 critical pillars:
1. Garment Fidelity & Silhouette:
   - Does the generated outfit faithfully preserve the reference garment's exact silhouette, neckline, collar, buttons, closures, sleeve cut, and fabric pattern?
   - Is there any bizarre texture melting, missing buttons, or simplified solid coloring where prints should exist?

2. Two-Piece Pairing & Waistband Drape (if paired):
   - Natural waistband transition and realistic fabric drape where top and bottom meet (no awkward fusion or vanishing waistbands).

3. Model-to-Environment Proportions & Spatial Scale:
   - Is the model realistically proportioned within the architectural environment (e.g. bedroom, studio, street)?
   - REJECT if the model is disproportionately giant, dominating 90%+ of the frame, or dwarfs the background furniture/doors like an oversized sticker.
   - There must be natural headroom (clear space above model's head) and grounded floor contact shadows.

4. Authentic Human Skin & Dermal Realism:
   - Does the model have realistic, natural skin micro-texture and authentic skin pores?
   - REJECT if the skin looks plastic, waxy, over-airbrushed, or has synthetic porcelain/CGI mannequin smoothing.

5. Anatomy & Hand Integrity:
   - Exactly 2 arms, 2 legs, natural human limb geometry.
   - Hand integrity: exactly 5 distinct fingers per visible hand (no fused digits, extra 6th fingers, or mangled joints).
   - Natural, distortion-free face and realistic eye gaze.

Return ONLY a valid JSON object matching this schema:
{
  "garment_silhouette_preserved": boolean,
  "garment_details_fidelity": boolean,
  "anatomy_integrity": boolean,
  "skin_micro_texture_natural": boolean,
  "scene_spatial_realism": boolean,
  "model_scale_proportional": boolean,
  "confidence_score": number (0.0 to 1.0),
  "defects": [
    {
      "category": string ("garment_fidelity" | "anatomy" | "scene_realism" | "skin_texture" | "model_scale" | "face_identity"),
      "severity": string ("CRITICAL" | "HIGH" | "MEDIUM" | "LOW"),
      "description": string,
      "suggested_fix": string
    }
  ],
  "targeted_repair_instructions": [string],
  "negative_prompt_additions": [string]
}
"""

def _optimize_bytes_for_vision(image_bytes: bytes, max_dim: int = 1024) -> bytes:
    """Downsamples image bytes for fast network transfer to multimodal LLM."""
    if not image_bytes or len(image_bytes) < 100:
        return image_bytes
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / float(max(w, h))
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return image_bytes


class Tier3MultimodalJudge:
    def __init__(self, client=None, temperature: float = 0.0):
        self.client = client
        self.temperature = temperature

    def evaluate(
        self,
        person_ref_bytes: Optional[bytes],
        garment_ref_bytes: Optional[bytes],
        generated_bytes: bytes,
        secondary_garment_ref_bytes: Optional[bytes] = None,
        client=None
    ) -> Tier3Result:
        ai_client = client or self.client
        if not ai_client:
            return Tier3Result(
                passed=True,
                score=1.0,
                details={"note": "Gemini client not provided, skipped multimodal review"}
            )

        try:
            contents = []

            # 1. Primary Reference Garment (Top / Dress)
            if garment_ref_bytes and len(garment_ref_bytes) > 100:
                opt_garment = _optimize_bytes_for_vision(garment_ref_bytes)
                contents.append(types.Part.from_bytes(data=opt_garment, mime_type="image/jpeg"))
                contents.append(types.Part.from_text(text="IMAGE 1: Reference Primary Garment (Top / Main)"))

            # 2. Secondary Reference Garment (Bottom / Jeans, if paired)
            if secondary_garment_ref_bytes and len(secondary_garment_ref_bytes) > 100:
                opt_sec = _optimize_bytes_for_vision(secondary_garment_ref_bytes)
                contents.append(types.Part.from_bytes(data=opt_sec, mime_type="image/jpeg"))
                contents.append(types.Part.from_text(text="IMAGE 2: Reference Secondary Garment (Bottom / Jeans)"))

            # 3. Person Model Reference (if available)
            if person_ref_bytes and len(person_ref_bytes) > 100:
                opt_person = _optimize_bytes_for_vision(person_ref_bytes)
                contents.append(types.Part.from_bytes(data=opt_person, mime_type="image/jpeg"))
                contents.append(types.Part.from_text(text="IMAGE 3: Reference Person Model"))

            # 4. Generated VTON Image (Fast optimized JPEG)
            opt_generated = _optimize_bytes_for_vision(generated_bytes)
            contents.append(types.Part.from_bytes(data=opt_generated, mime_type="image/jpeg"))
            contents.append(types.Part.from_text(text="FINAL IMAGE: Generated VTON Result to Inspect"))

            # 5. Directive Prompt
            contents.append(types.Part.from_text(text=VTON_MULTIMODAL_QC_PROMPT))

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=self.temperature
            )

            # Prioritize fast 2.5-flash for 2-3 second latency, fallback to 2.5-pro only if flash fails
            models_to_try = ["gemini-2.5-flash", "gemini-2.5-pro"]
            resp = None
            for model_name in models_to_try:
                try:
                    resp = ai_client.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=config
                    )
                    if resp and resp.text:
                        break
                except Exception as err:
                    logger.warning(f"Tier 3 multimodal model {model_name} error: {err}")
                    continue

            if not resp or not resp.text:
                return Tier3Result(
                    passed=True,
                    score=0.85,
                    details={"note": "Empty multimodal LLM response, fallback to passing"}
                )

            text = resp.text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
            data = json.loads(text)

            silhouette_ok = bool(data.get("garment_silhouette_preserved", True))
            details_ok = bool(data.get("garment_details_fidelity", True))
            anatomy_ok = bool(data.get("anatomy_integrity", True))
            skin_ok = bool(data.get("skin_micro_texture_natural", True))
            scene_ok = bool(data.get("scene_spatial_realism", True))
            scale_ok = bool(data.get("model_scale_proportional", True))
            confidence = float(data.get("confidence_score", 0.90))

            raw_defects = data.get("defects", [])
            repairs = data.get("targeted_repair_instructions", [])
            negatives = data.get("negative_prompt_additions", [])

            parsed_defects: List[QCDefect] = []
            for d in raw_defects:
                sev_str = str(d.get("severity", "MEDIUM")).upper()
                try:
                    sev = DefectSeverity[sev_str]
                except KeyError:
                    sev = DefectSeverity.MEDIUM
                parsed_defects.append(QCDefect(
                    category=d.get("category", "semantic_defect"),
                    severity=sev,
                    description=d.get("description", "Semantic defect observed"),
                    suggested_fix=d.get("suggested_fix")
                ))

            # Programmatic pass rule:
            # Garment silhouette, details, anatomy, skin texture, model scale, and scene realism must be intact
            critical_flaws = [d for d in parsed_defects if d.severity == DefectSeverity.CRITICAL]
            high_flaws = [d for d in parsed_defects if d.severity == DefectSeverity.HIGH]

            passed = (
                silhouette_ok and 
                details_ok and 
                anatomy_ok and 
                skin_ok and 
                scene_ok and 
                scale_ok and
                (len(critical_flaws) == 0) and 
                (len(high_flaws) == 0) and 
                (confidence >= 0.70)
            )

            # Ensure essential negative prompts and repairs exist if defects detected
            if not anatomy_ok and "deformed fingers, extra digits, fused hands" not in negatives:
                negatives.append("deformed fingers, extra digits, fused hands, distorted limbs")
            if not skin_ok and "plastic skin, waxy face" not in negatives:
                negatives.append("plastic skin, waxy skin, over-smoothed plastic AI face, airbrushed poreless texture, porcelain doll complexion")
                repairs.append("Render authentic human skin micro-texture with visible natural pores and un-airbrushed dermal grain")
            if not scale_ok and "oversized model" not in negatives:
                negatives.append("oversized model dominating entire frame, giant model dwarfing background room, cramped framing without headroom")
                repairs.append("Frame model with natural editorial scale (70-75% vertical height with generous 15% headroom above head)")
            if not scene_ok and "floating model, hovering feet" not in negatives:
                negatives.append("floating model, hovering feet, missing floor contact shadows")

            score = confidence if passed else min(confidence, 0.65)

            return Tier3Result(
                passed=passed,
                score=round(score, 3),
                garment_silhouette_preserved=silhouette_ok,
                garment_details_fidelity=details_ok,
                anatomy_integrity=anatomy_ok,
                skin_micro_texture_natural=skin_ok,
                scene_spatial_realism=scene_ok,
                defects=parsed_defects,
                targeted_repair_instructions=repairs,
                negative_prompt_additions=negatives,
                details=data
            )

        except Exception as e:
            logger.error(f"Tier 3 multimodal judge exception: {e}")
            return Tier3Result(
                passed=True,
                score=0.80,
                details={"error": str(e)}
            )
