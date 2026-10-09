import os
from dataclasses import asdict
from typing import Dict, Any, List, Optional

from ..core.interfaces import IIngestionService, IStyleAnalyzer, IImageGenerator, IVideoGenerator
from ..core.models import PipelineContext, GeneratedAsset, StyleAnalysisResult, ProductData
from ..services.image_processing.geometry import normalize_aspect_ratio


class PipelineOrchestrator:
    def __init__(
        self,
        ingestion_service: IIngestionService,
        style_analyzer: IStyleAnalyzer,
        image_generator: IImageGenerator,
        video_generator: IVideoGenerator
    ):
        self.ingestion = ingestion_service
        self.analyzer = style_analyzer
        self.image_gen = image_generator
        self.video_gen = video_generator

    def run(
        self, 
        product_url: str = None, 
        raw_details: dict = None, 
        secondary_url: str = None,
        secondary_details: dict = None,
        styles: List[str] = None, 
        target_stage: str = "video",
        aspect_ratio: str = "9:16",
        duration: int = 10,
        merge_video: bool = True,
        merge_video_idea: str = "smooth",
        user_prompt: Optional[str] = None,
        qc_version: str = "v1"
    ) -> Dict[str, Any]:
        norm_aspect = normalize_aspect_ratio(aspect_ratio, default="9:16")
        
        context = PipelineContext(
            base_dir=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "runs"),
            aspect_ratio=norm_aspect
        )
        context.log("=== Starting Ad Craft Multi-Garment AI Video Pipeline ===")
        context.log(f"Run ID: {context.run_id} | Target Aspect Ratio: {norm_aspect} | Duration: {duration}s | Merge: {merge_video} ({merge_video_idea}) | QC Engine: {qc_version.upper()}")
        
        try:
            # Step 1: Ingestion (Primary & Optional Secondary Product)
            product_data = self.ingestion.ingest(
                context, 
                product_url=product_url, 
                raw_details=raw_details,
                secondary_url=secondary_url,
                secondary_details=secondary_details
            )
            product_data.aspect_ratio = norm_aspect
            
            if target_stage == "scrape":
                context.log("Stopping early at stage: scrape")
                self._finalize(context, product_data, None, [])
                return {"run_id": context.run_id, "status": "success", "stage": "scrape"}
                
            # Step 2: Gemini Style & Accessory Analyzer
            analysis_result = self.analyzer.analyze(context, product_data)
            
            if not analysis_result.is_cloth:
                context.log("Product is determined NOT to be clothing. Skipping generation.")
                self._finalize(context, product_data, analysis_result, [])
                return {"run_id": context.run_id, "status": "skipped", "reason": "not_clothing"}
                
            # Resolve target style: exactly ONE style per run per requirements
            candidate_styles = styles if (styles and len(styles) > 0) else analysis_result.styles
            if not candidate_styles:
                candidate_styles = ["Contemporary Chic Studio"]
            selected_single_style = [candidate_styles[0]]
            
            context.log(f"Selected style for this run: {selected_single_style[0]}")
            
            if target_stage == "style":
                context.log("Stopping early at stage: style")
                self._finalize(context, product_data, analysis_result, [])
                return {"run_id": context.run_id, "status": "success", "stage": "style", "styles": selected_single_style}
                
            # Step 3: VTON Image Generation (Dual Garments + Auto-Accessories + QC)
            context.log(f"Proceeding to VTON image generation for style: '{selected_single_style[0]}'...")
            assets = self.image_gen.generate_style_images(
                context, product_data, selected_single_style, max_parallel=1, aspect_ratio=norm_aspect, qc_version=qc_version
            )
            
            if target_stage == "image":
                context.log("Stopping early at stage: image")
                self._finalize(context, product_data, analysis_result, assets)
                return {"run_id": context.run_id, "status": "success", "stage": "image", "assets": len(assets)}
                
            # Step 4: Parallel Kling Video Generation + Standalone FFmpeg Merge
            context.log(f"Proceeding to video generation ({duration}s, merge: {merge_video})...")
            final_assets = self.video_gen.generate_videos(
                context=context,
                assets=assets,
                max_parallel=1,
                aspect_ratio=norm_aspect,
                duration=duration,
                merge_video=merge_video,
                merge_video_idea=merge_video_idea,
                user_prompt=user_prompt
            )
            
            # Finalize
            self._finalize(context, product_data, analysis_result, final_assets)
            
            context.log("=== Pipeline Completed Successfully ===")
            return {"run_id": context.run_id, "status": "success", "assets": len(final_assets)}
            
        except Exception as e:
            context.log(f"Pipeline failed with unhandled exception: {e}")
            return {"run_id": context.run_id, "status": "failed", "error": str(e)}

    def _finalize(self, context: PipelineContext, product: Optional[ProductData], analysis: Optional[StyleAnalysisResult], assets: List[GeneratedAsset]):
        failed_styles = [asdict(a) for a in assets if a.error] if assets else []
        successful_styles = [asdict(a) for a in assets if not a.error] if assets else []
        
        is_pair = bool(product and product.secondary_image_paths and len(product.secondary_image_paths) > 0)
        
        final_payload = {
            "run_id": context.run_id,
            "aspect_ratio": context.aspect_ratio,
            "pairing_mode": "pair" if is_pair else "single",
            "summary": {
                "total_styles": len(assets) if assets else 0,
                "successful_count": len(successful_styles),
                "failed_count": len(failed_styles),
                "failed_style_reasons": {a.style_name: a.error for a in assets if a.error} if assets else {}
            },
            "products": {
                "primary": {
                    "title": product.title if product else None,
                    "price": product.price if product else None,
                    "images": product.image_paths if product else []
                },
                "secondary": {
                    "title": product.secondary_title if product else None,
                    "price": product.secondary_price if product else None,
                    "images": product.secondary_image_paths if product else []
                } if is_pair else None
            },
            "styling": {
                "is_cloth": analysis.is_cloth if analysis else None,
                "styles": analysis.styles if analysis else [],
                "auto_accessories": product.auto_accessories if product else []
            },
            "generated_assets": [asdict(a) for a in assets] if assets else []
        }
        context.save_final_output(final_payload)
