"""
Dynamic Style Recommendation Service ported from StyleLoop.
Auto-generates high-converting commercial ad aesthetics, studio lighting, and camera settings
when styles are not explicitly specified or need enhancement.
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
from ..core.models import PipelineContext, ProductData

logger = logging.getLogger("DynamicStyleService")


class DynamicStyleService:
    def __init__(self, default_model: str = "gemini-2.5-flash"):
        self.model_name = default_model

    def synthesize_styles(self, context: PipelineContext, product_data: ProductData, count: int = 3) -> List[Dict[str, str]]:
        """
        Synthesizes rich commercial fashion ad scene styles for the given product.
        """
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return self._fallback_styles(product_data)

        try:
            from google import genai
            from google.genai import types
            from PIL import Image

            client = genai.Client(api_key=api_key)
            contents = []

            # Add reference image if available
            for img_path in product_data.image_paths[:2]:
                if os.path.exists(img_path):
                    contents.append(Image.open(img_path))

            prompt = f"""
            You are a Creative Director for a Luxury Fashion Brand (e.g. Zara, Vogue, Kith).
            Garment Title: {product_data.title}
            Key Features: {', '.join(product_data.key_selling_points)}

            Generate {count} distinct, photorealistic commercial ad visual styles:
            1. Scene ambiance & photorealistic location matching the garment.
            2. Exact studio lighting parameters (e.g. soft diffuse key light, subtle golden rim).
            3. Camera & lens setup (e.g. 85mm f/1.4, sharp fabric texture).
            4. Model editorial pose.

            Return JSON array of objects:
            [
              {{
                "style_name": "string",
                "scene_ambiance": "string",
                "lighting": "string",
                "prompt_enhancement": "string"
              }}
            ]
            """
            contents.append(prompt)

            response = client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.4
                )
            )
            styles = json.loads(response.text.strip())
            return styles
        except Exception as e:
            context.log(f"Dynamic style generation error: {e}. Using fallback.")
            return self._fallback_styles(product_data)

    def _fallback_styles(self, product_data: ProductData) -> List[Dict[str, str]]:
        return [
            {
                "style_name": "Minimalist Studio Loft",
                "scene_ambiance": "Sunlit architectural loft with textured concrete and minimalist clean aesthetics",
                "lighting": "Soft natural window light with subtle fill",
                "prompt_enhancement": "clean high-fashion editorial, sharp 85mm focus"
            },
            {
                "style_name": "Modern Metropolitan Street",
                "scene_ambiance": "Sleek European urban street with warm architectural bokeh in the background",
                "lighting": "Golden hour ambient backlight with crisp subject illumination",
                "prompt_enhancement": "vibrant street style, editorial magazine look"
            }
        ]
