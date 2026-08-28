import os
import time
import json
import base64
from typing import List, Optional, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..core.interfaces import IImageGenerator
from ..core.models import PipelineContext, ProductData, GeneratedAsset
from .image_processing.geometry import (
    enforce_aspect_ratio,
    normalize_aspect_ratio,
    create_detail_framing_crops
)
from .qc_inspector import QCQualityGatekeeper

IMAGE_MODELS = [
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-image-preview",
    "gemini-2.5-flash-image"
]

PRIMARY_GARMENT_LOCK = (
    "═══════════════════════════════════════════\n"
    "PRIMARY GARMENT (IMAGE 1) — PRESERVE EVERY MINUTE DETAIL EXACTLY\n"
    "═══════════════════════════════════════════\n"
    "The primary garment is an authentic, specific commercial product shown in Image 1. "
    "You must NOT redesign, recolor, modify, or simplify ANY part of it. "
    "Preserve the exact hue, dye shade, fabric weave texture, collar/neckline, sleeve cut, seamlines, and hardware.\n"
)

SECONDARY_GARMENT_LOCK = (
    "═══════════════════════════════════════════\n"
    "SECONDARY GARMENT (IMAGE 2) — PRESERVE DETAILS EXACTLY (TWO-PIECE PAIR)\n"
    "═══════════════════════════════════════════\n"
    "The secondary garment is also an authentic product shown in Image 2. "
    "The SAME model must wear BOTH garments simultaneously: Image 1 (Top/Upper) paired with Image 2 (Bottom/Jeans/Trousers/Skirt). "
    "Preserve the exact denim wash, waistband, pockets, distressing, silhouette, and hemline of Image 2. "
    "Ensure a natural waistband transition and realistic fabric drape where the two garments meet.\n"
)


class ParallelImageGenerator(IImageGenerator):
    def __init__(self, prompts_dir: str = None):
        if not prompts_dir:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            prompts_dir = os.path.join(base_dir, "prompts")
        self.prompts_dir = prompts_dir
        self.gatekeeper = QCQualityGatekeeper()

    def generate_style_images(
        self, 
        context: PipelineContext, 
        product_data: ProductData, 
        styles: List[str], 
        max_parallel: int = 4,
        aspect_ratio: str = "9:16"
    ) -> List[GeneratedAsset]:
        
        target_aspect = normalize_aspect_ratio(aspect_ratio, default=context.aspect_ratio)
        selected_styles = styles[:max_parallel]
        is_pair = bool(product_data.secondary_image_paths and len(product_data.secondary_image_paths) > 0)
        context.log(f"Starting VTON image generation for {len(selected_styles)} styles: {selected_styles} | Aspect Ratio: {target_aspect} | Pair Mode: {is_pair}")
        
        client = None
        api_key = os.environ.get("GEMINI_API_KEY")
        if api_key:
            try:
                from google import genai
                client = genai.Client(api_key=api_key)
            except Exception as e:
                context.log(f"Gemini client init warning: {e}")

        # Load primary reference image bytes
        primary_ref_bytes = None
        if product_data.image_paths and os.path.exists(product_data.image_paths[0]):
            with open(product_data.image_paths[0], "rb") as f:
                primary_ref_bytes = f.read()

        # Load secondary reference image bytes (if pair)
        secondary_ref_bytes = None
        if is_pair and os.path.exists(product_data.secondary_image_paths[0]):
            with open(product_data.secondary_image_paths[0], "rb") as f:
                secondary_ref_bytes = f.read()

        results: List[GeneratedAsset] = []
        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            futures = {
                executor.submit(
                    self._generate_single_style, 
                    context, 
                    product_data, 
                    style, 
                    client, 
                    primary_ref_bytes,
                    secondary_ref_bytes,
                    target_aspect
                ): style 
                for style in selected_styles
            }
            for future in as_completed(futures):
                style_name = futures[future]
                try:
                    asset = future.result()
                    results.append(asset)
                    context.log(f"Finished style generation for: {style_name}")
                except Exception as exc:
                    context.log(f"Style '{style_name}' generated an exception: {exc}")
                    results.append(GeneratedAsset(
                        style_name=style_name, 
                        aspect_ratio=target_aspect,
                        error=str(exc)
                    ))
                    
        return results

    def _generate_single_style(
        self, 
        context: PipelineContext, 
        product_data: ProductData, 
        style: str,
        client: Any,
        primary_ref_bytes: Optional[bytes],
        secondary_ref_bytes: Optional[bytes],
        aspect_ratio: str
    ) -> GeneratedAsset:
        
        safe_style = "".join(c if c.isalnum() else "_" for c in style).strip("_").lower()
        style_dir = os.path.join(context.images_dir, safe_style)
        os.makedirs(style_dir, exist_ok=True)
        
        hero_path = os.path.join(style_dir, "hero.jpg")
        detail_path = os.path.join(style_dir, "detail.jpg")
        back_path = os.path.join(style_dir, "back.jpg")
        
        is_pair = secondary_ref_bytes is not None
        accessories = product_data.auto_accessories or [
            "minimalist leather shoulder bag",
            "delicate fine jewelry accents",
            "understated leather belt"
        ]
        
        trace = {
            "style": style,
            "aspect_ratio": aspect_ratio,
            "mode": "pair" if is_pair else "single",
            "primary_product": product_data.title,
            "secondary_product": product_data.secondary_title if is_pair else None,
            "accessories": accessories,
            "calls": [],
            "qc_evaluations": [],
            "errors": []
        }
        
        # Compose prompt blocks
        resolved_style = style
        style_negative_rules = []
        if any(w in style.lower() for w in ["solid", "plain", "studio", "background"]):
            resolved_style = (
                f"{style} with rich textured architectural cyclorama, warm muted taupe/terracotta/sand tones, "
                f"soft directional studio lighting and natural cast floor shadows"
            )
            style_negative_rules.append("pure white background, flat #FFFFFF blank canvas, sterile hospital lighting")

        # Garment lock text
        garment_prompt_header = PRIMARY_GARMENT_LOCK
        if is_pair:
            garment_prompt_header += "\n" + SECONDARY_GARMENT_LOCK
            outfit_desc = f"wearing BOTH Image 1 ({product_data.title}) and Image 2 ({product_data.secondary_title})"
        else:
            outfit_desc = f"wearing the exact garment from Image 1 ({product_data.title})"

        # Accessory block
        acc_text = ", ".join(accessories)
        accessory_directive = (
            f"EDITORIAL ACCESSORIES & STYLING: Tastefully accessorized with {acc_text}. "
            f"Accessories must be styled cleanly at the side, shoulder, or wrist without blocking or obscuring garment seams, buttons, or neckline details."
        )

        base_prompt = (
            f"{garment_prompt_header}\n"
            f"Generate a hyper-photorealistic fashion campaign photograph of one professional model {outfit_desc} in a {resolved_style} setting.\n"
            f"{accessory_directive}\n"
            f"CAMERA & LIGHTING: Shot on Hasselblad H6D-100c with 85mm f/1.4 luxury fashion portrait lens. "
            f"Tack sharp focus on garment fabric weave, realistic authentic skin pores (zero plastic airbrushing), "
            f"and physically grounded floor contact shadows.\n"
        )

        # Save VTON payload
        payload = {
            "style": style,
            "aspect_ratio": aspect_ratio,
            "mode": "pair" if is_pair else "single",
            "primary_product": product_data.title,
            "secondary_product": product_data.secondary_title if is_pair else None,
            "accessories": accessories,
            "base_prompt": base_prompt
        }
        with open(os.path.join(style_dir, "vton_payload.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=4)
        
        if not client or not primary_ref_bytes:
            context.log(f"Simulation Mode: Generating placeholder images for style: {style}")
            from PIL import Image, ImageDraw
            w, h = (720, 1280) if aspect_ratio == "9:16" else (1024, 1024)
            for p, label in [(hero_path, f"Hero: {style}"), (detail_path, f"Detail: {style}"), (back_path, f"Back: {style}")]:
                img = Image.new("RGB", (w, h), color=(35, 35, 40))
                draw = ImageDraw.Draw(img)
                draw.rectangle([20, 20, w - 20, h - 20], outline=(150, 150, 150), width=3)
                p_text = f"{label}\nProduct 1: {product_data.title[:18]}"
                if is_pair:
                    p_text += f"\nProduct 2: {product_data.secondary_title[:18]}"
                draw.text((w // 4, h // 2), p_text, fill=(240, 240, 240))
                img.save(p, "JPEG", quality=90)
                
            return GeneratedAsset(
                style_name=style,
                aspect_ratio=aspect_ratio,
                image_paths=[hero_path, detail_path, back_path],
                qc_report={"passed": True, "weighted_score": 0.95, "self_healing_attempts": 0},
                accessories_styled=accessories,
                metadata={"trace": trace, "simulation": True}
            )

        from google.genai import types

        def call_image_generation(prompt_str: str, negative_constraints: str = "") -> bytes:
            image_config_obj = None
            if hasattr(types, "ImageConfig"):
                try:
                    image_config_obj = types.ImageConfig(
                        aspect_ratio=aspect_ratio,
                        image_size="2K"
                    )
                except Exception:
                    image_config_obj = None

            full_prompt = prompt_str
            if negative_constraints:
                full_prompt += f"\n\nSTRICT NEGATIVE CONSTRAINTS (AVOID THESE DEFECTS): {negative_constraints}"

            contents = [
                types.Part.from_bytes(data=primary_ref_bytes, mime_type="image/jpeg")
            ]
            if is_pair and secondary_ref_bytes:
                contents.append(types.Part.from_bytes(data=secondary_ref_bytes, mime_type="image/jpeg"))
            contents.append(full_prompt)

            for model_name in IMAGE_MODELS:
                try:
                    trace["calls"].append({"model": model_name, "prompt_preview": full_prompt[:120]})
                    config_kwargs = {}
                    if image_config_obj:
                        config_kwargs["image_config"] = image_config_obj

                    response = client.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=types.GenerateContentConfig(**config_kwargs) if config_kwargs else None
                    )
                    
                    if response and hasattr(response, "candidates") and response.candidates:
                        for cand in response.candidates:
                            for part in getattr(cand.content, "parts", []):
                                if getattr(part, "inline_data", None) and part.inline_data.data:
                                    raw = part.inline_data.data
                                    if not (raw.startswith(b"\xff\xd8") or raw.startswith(b"\x89PNG")):
                                        raw = base64.b64decode(raw, validate=True)
                                    return enforce_aspect_ratio(raw, target_ratio=aspect_ratio)
                except Exception as err:
                    context.log(f"Model {model_name} failed: {err}. Trying fallback...")
                    time.sleep(1)
                    continue

            raise RuntimeError("All Gemini image generation fallback models failed.")

        try:
            # Generate Hero View with Self-Healing Loop (Max 3 attempts)
            max_qc_attempts = 3
            final_hero_bytes = None
            last_qc_report = None
            negative_remedies = ", ".join(style_negative_rules)
            healing_count = 0

            for attempt in range(1, max_qc_attempts + 1):
                context.log(f"🎨 Generating Hero Image for '{style}' (Attempt {attempt}/{max_qc_attempts}, Aspect: {aspect_ratio})...")
                current_hero_bytes = call_image_generation(f"{base_prompt}\nSHOT DIRECTIVE: Full body front hero campaign shot.", negative_constraints=negative_remedies)
                
                # Multi-Stage QC Audit
                context.log(f"🔍 Running Multi-Stage QC Audit on '{style}'...")
                qc_report = self.gatekeeper.evaluate_bytes(primary_ref_bytes, current_hero_bytes, client=client)
                context.log(f"QC Report for {style} (Attempt {attempt}): Score {qc_report.weighted_score:.2f} (Passed: {qc_report.overall_passed})")
                
                trace["qc_evaluations"].append({
                    "attempt": attempt,
                    "weighted_score": qc_report.weighted_score,
                    "passed": qc_report.overall_passed,
                    "defects": [d for r in qc_report.results for d in r.defects]
                })

                final_hero_bytes = current_hero_bytes
                last_qc_report = qc_report

                if qc_report.overall_passed or attempt == max_qc_attempts:
                    break

                healing_count += 1
                new_negatives = list(style_negative_rules) + qc_report.negative_prompt_additions
                negative_remedies = ", ".join(dict.fromkeys(new_negatives))
                context.log(f"🩹 Triggering Self-Healing retry with targeted negative constraints: {qc_report.negative_prompt_additions}")

            with open(hero_path, "wb") as f:
                f.write(final_hero_bytes)

            # Generate Detail & Back Views
            context.log(f"Generating Detail and Back views for style '{style}'...")
            detail_prompt = (
                f"{base_prompt}\n"
                f"SHOT DIRECTIVE: High-fashion medium close-up beauty shot focusing on the fabric texture, seams, stitching, and craftsmanship. "
                f"Same model, same paired outfit, same lighting."
            )
            detail_bytes = call_image_generation(detail_prompt, negative_constraints=negative_remedies)
            with open(detail_path, "wb") as f:
                f.write(detail_bytes)

            back_prompt = (
                f"{base_prompt}\n"
                f"SHOT DIRECTIVE: Full-length rear view of the SAME model turned away from the camera, "
                f"showcasing the rear back design of the outfit. Same model, same paired look, same environment."
            )
            back_bytes = call_image_generation(back_prompt, negative_constraints=negative_remedies)
            with open(back_path, "wb") as f:
                f.write(back_bytes)

        except Exception as e:
            context.log(f"Image generation failed for {style}: {e}")
            trace["errors"].append(str(e))
            return GeneratedAsset(
                style_name=style, 
                aspect_ratio=aspect_ratio,
                error=f"Image generation failed: {e}",
                metadata={"trace": trace}
            )

        qc_summary = {
            "passed": last_qc_report.overall_passed if last_qc_report else True,
            "weighted_score": last_qc_report.weighted_score if last_qc_report else 1.0,
            "self_healing_attempts": healing_count
        }

        return GeneratedAsset(
            style_name=style,
            aspect_ratio=aspect_ratio,
            image_paths=[hero_path, detail_path, back_path],
            qc_report=qc_summary,
            face_qa_report=last_qc_report.face_qa_details if last_qc_report else None,
            scene_qa_report=last_qc_report.scene_qa_details if last_qc_report else None,
            hero_inspector_report=last_qc_report.hero_inspector_details if last_qc_report else None,
            self_healing_attempts=healing_count,
            accessories_styled=accessories,
            metadata={"trace": trace}
        )
