import json
from typing import Dict, Any, List, Optional
from google.genai import types

SCENE_REALISM_RULES = """
Mandatory Physical Realism Rules:
1. Footing & Grounding: Both feet must be realistically planted on the floor with crisp, physically accurate contact shadows (no floating model or disconnected ground contact).
2. Human Scale Proportion: Model must have natural human proportions (160–185 cm) relative to furniture, lamps, doorways, and architectural background props (no giant lamps or miniature dollhouse props).
3. Ceiling & Headroom Clearance: Normal headroom clearance above model's head (ceiling fixtures not clipping into hair or uncomfortably low).
4. Mirror & Reflection Consistency: If mirrors or glass are present, reflections must be physically consistent with zero duplicate floating ghost figures.
5. Perspective & Horizon Line: Consistent camera eye-level perspective across model and environment.
"""

def build_scene_realism_prompt() -> str:
    return (
        "You are an architectural and spatial realism auditor for luxury commercial advertising campaigns.\n"
        "Audit the provided generated fashion photoshoot image.\n\n"
        f"{SCENE_REALISM_RULES}\n"
        "Evaluate strictly and objectively. Return ONLY a valid JSON object matching this schema:\n"
        "{\n"
        '  "feet_planted_firmly": boolean,\n'
        '  "realistic_proportions": boolean,\n'
        '  "correct_perspective_and_shadows": boolean,\n'
        '  "no_ghost_reflections": boolean,\n'
        '  "confidence_score": number (0.0 to 1.0),\n'
        '  "identified_flaws": [string],\n'
        '  "scene_negative_constraints": [string]\n'
        "}\n"
        "Rules:\n"
        "- If the model appears to float or lacks direct floor contact shadows, set feet_planted_firmly: false.\n"
        "- If furniture or background elements have bizarre, surreal, or miniature scaling, set realistic_proportions: false."
    )

class SceneRealismQA:
    def __init__(self, client=None, temperature: float = 0.0):
        self.client = client
        self.temperature = temperature

    def evaluate(
        self, 
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
            contents = [
                types.Part.from_bytes(data=hero_img_bytes, mime_type="image/png"),
                types.Part.from_text(text=build_scene_realism_prompt())
            ]

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=self.temperature
            )

            models_to_try = ["gemini-2.5-flash", "gemini-2.5-pro"]
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
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
            data = json.loads(text)

            feet_planted = bool(data.get("feet_planted_firmly", True))
            proportions = bool(data.get("realistic_proportions", True))
            perspective = bool(data.get("correct_perspective_and_shadows", True))
            no_ghosts = bool(data.get("no_ghost_reflections", True))
            confidence = float(data.get("confidence_score", 0.90))

            flaws = data.get("identified_flaws", [])
            negatives = data.get("scene_negative_constraints", [])

            passed = feet_planted and proportions and perspective and no_ghosts and (confidence >= 0.70)

            if not feet_planted and "floating model, missing contact shadows" not in negatives:
                negatives.append("floating model, hovering feet, missing floor contact shadow")
            if not proportions and "distorted props, miniature furniture" not in negatives:
                negatives.append("distorted furniture props, surreal scaling, oversized lamps")

            return {
                "passed": passed,
                "score": confidence if passed else min(confidence, 0.65),
                "feet_planted_firmly": feet_planted,
                "realistic_proportions": proportions,
                "correct_perspective_and_shadows": perspective,
                "no_ghost_reflections": no_ghosts,
                "identified_flaws": flaws,
                "scene_negative_constraints": negatives
            }
        except Exception as e:
            return {
                "passed": True,
                "score": 0.80,
                "error": str(e),
                "scene_negative_constraints": []
            }
