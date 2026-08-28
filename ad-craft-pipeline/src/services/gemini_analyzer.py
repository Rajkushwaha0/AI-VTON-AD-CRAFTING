import os
import json
from typing import List
from ..core.interfaces import IStyleAnalyzer
from ..core.models import PipelineContext, ProductData, StyleAnalysisResult

class GeminiStyleAnalyzer(IStyleAnalyzer):
    def __init__(self, prompt_path: str = None):
        if not prompt_path:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            prompt_path = os.path.join(base_dir, "prompts", "style_tagging.txt")
            
        self.prompt_path = prompt_path
        
        try:
            from google import genai  # type: ignore
            self.has_genai = True
        except ImportError:
            self.has_genai = False

    def analyze(self, context: PipelineContext, product_data: ProductData) -> StyleAnalysisResult:
        context.log(f"Starting Gemini style & accessory analysis for: {product_data.title}")
        
        api_key = os.environ.get("GEMINI_API_KEY")
        if not self.has_genai or not api_key:
            context.log("GEMINI_API_KEY not found or genai missing. Returning intelligent heuristic analysis.")
            return self._mock_analysis(context, product_data)
            
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
            
            client = genai.Client(api_key=api_key)
            
            with open(self.prompt_path, "r", encoding="utf-8") as f:
                system_instruction = f.read()
                
            parts = []
            prompt_text = f"Primary Garment: {product_data.title}\n"
            if product_data.secondary_title:
                prompt_text += f"Secondary Garment (Paired): {product_data.secondary_title}\n"
            parts.append(types.Part.from_text(text=prompt_text))
            
            # Upload and append primary images
            uploaded_files = []
            for img_path in product_data.image_paths[:2]:
                if os.path.exists(img_path):
                    up = client.files.upload(file=img_path)
                    uploaded_files.append(up)
                    parts.append(types.Part.from_uri(file_uri=up.uri, mime_type=up.mime_type))
                    
            # Upload secondary images if available
            for img_path in product_data.secondary_image_paths[:2]:
                if os.path.exists(img_path):
                    up = client.files.upload(file=img_path)
                    uploaded_files.append(up)
                    parts.append(types.Part.from_uri(file_uri=up.uri, mime_type=up.mime_type))

            context.log(f"Calling Gemini with {len(uploaded_files)} images for styling & accessory curation...")
            
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=parts,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    temperature=0.2
                ),
            )
            
            # Cleanup uploaded files
            for f in uploaded_files:
                try:
                    client.files.delete(name=f.name)
                except Exception:
                    pass
                    
            response_text = response.text.strip()
            if response_text.startswith("```"):
                import re
                response_text = re.sub(r"^```(?:json)?\s*\n?", "", response_text)
                response_text = response_text.rsplit("```", 1)[0].strip()
                
            parsed = json.loads(response_text)
            
            is_cloth = parsed.get("is_cloth", True)
            styles = parsed.get("styles", [])
            if not styles:
                styles = ["Contemporary Chic Studio", "European Sidewalk Cafe"]

            suggested_accessories = self._derive_accessories(product_data, styles)
            product_data.auto_accessories = suggested_accessories
            
            context.log(f"Analysis complete. is_cloth: {is_cloth}, styles: {styles}, accessories: {suggested_accessories}")
            return StyleAnalysisResult(
                is_cloth=is_cloth, 
                styles=styles,
                pairing_type="pair" if product_data.secondary_image_paths else "single",
                suggested_accessories=suggested_accessories
            )
            
        except Exception as e:
            context.log(f"Gemini analysis failed: {e}. Falling back to heuristic.")
            return self._mock_analysis(context, product_data)

    def _derive_accessories(self, product_data: ProductData, styles: List[str]) -> List[str]:
        title_lower = (product_data.title + " " + (product_data.secondary_title or "")).lower()
        if any(w in title_lower for w in ["lehenga", "saree", "kurta", "anarkali", "ethnic", "choli"]):
            return [
                "traditional delicate jhumka earrings",
                "understated gold bangles on one wrist",
                "matching embellished mini potli clutch bag"
            ]
        elif any(w in title_lower for w in ["jean", "denim", "top", "tee", "shirt", "casual"]):
            return [
                "minimalist structured leather shoulder bag",
                "delicate layered gold chain necklace",
                "clean metal wristwatch",
                "understated leather belt with sleek buckle"
            ]
        elif any(w in title_lower for w in ["dress", "gown", "blazer", "formal", "suit"]):
            return [
                "luxury leather top-handle bag",
                "minimalist stud earrings",
                "delicate gold wrist bracelet"
            ]
        return [
            "minimalist leather shoulder bag",
            "delicate fine jewelry accents",
            "understated leather belt"
        ]

    def _mock_analysis(self, context: PipelineContext, product_data: ProductData) -> StyleAnalysisResult:
        accessories = self._derive_accessories(product_data, ["Contemporary Chic Studio", "European Sidewalk Cafe"])
        product_data.auto_accessories = accessories
        return StyleAnalysisResult(
            is_cloth=True, 
            styles=["Contemporary Chic Studio", "European Sidewalk Cafe"],
            pairing_type="pair" if product_data.secondary_image_paths else "single",
            suggested_accessories=accessories
        )
