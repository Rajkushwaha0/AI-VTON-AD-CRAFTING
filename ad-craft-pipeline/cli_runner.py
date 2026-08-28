import os
import sys
import json
import argparse

# Automatically load environment variables from .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from src.pipeline.orchestrator import PipelineOrchestrator
from src.services.scraper_ingestion import ScraperIngestionService
from src.services.gemini_analyzer import GeminiStyleAnalyzer
from src.services.vton_image_gen import ParallelImageGenerator
from src.services.video_gen import ParallelVideoGenerator

def main():
    parser = argparse.ArgumentParser(description="AdCraft Multi-Garment AI Video Pipeline")
    parser.add_argument("--demo", type=str, default="demo.json", help="Path to demo.json payload")
    parser.add_argument("--url", type=str, default=None, help="Direct primary product URL to scrape")
    parser.add_argument("--secondary-url", type=str, default=None, help="Direct secondary product URL for pairing")
    parser.add_argument("--stage", type=str, default="video", choices=["scrape", "style", "image", "video"], help="Target stage to run until")
    parser.add_argument("--aspect-ratio", type=str, default="9:16", choices=["9:16", "1:1", "16:9"], help="Target aspect ratio for images and video")
    parser.add_argument("--duration", type=int, default=10, choices=[10, 15], help="Target video duration (10s=5sx2, 15s=5sx3)")
    parser.add_argument("--merge-video", action="store_true", default=None, help="Merge shots into final video via FFmpeg")
    parser.add_argument("--merge-video-idea", type=str, default=None, choices=["smooth", "fast_paced"], help="Merge transition style ('smooth' or 'fast_paced')")
    parser.add_argument("--user-prompt", type=str, default=None, help="Custom camera / movement prompt override")
    args = parser.parse_args()
    
    url = args.url
    secondary_url = args.secondary_url
    details = None
    secondary_details = None
    styles = None
    aspect_ratio = args.aspect_ratio
    duration = args.duration
    merge_video = True
    merge_video_idea = "smooth"
    user_prompt = args.user_prompt
    
    if not url and os.path.exists(args.demo):
        with open(args.demo, "r", encoding="utf-8") as f:
            payload = json.load(f)
        url = payload.get("product_url")
        secondary_url = payload.get("secondary_product_url", secondary_url)
        details = payload.get("product_details")
        secondary_details = payload.get("secondary_product_details")
        styles = payload.get("styles")
        aspect_ratio = payload.get("aspect_ratio", aspect_ratio)
        duration = int(payload.get("duration", duration))
        if "mergeVideo" in payload:
            merge_video = bool(payload.get("mergeVideo"))
        merge_video_idea = payload.get("mergeVideoIdea", merge_video_idea)
        user_prompt = payload.get("user_prompt", user_prompt)

    if args.merge_video is not None:
        merge_video = args.merge_video
    if args.merge_video_idea is not None:
        merge_video_idea = args.merge_video_idea

    print("==================================================")
    print("      AdCraft Next-Gen AI Video Pipeline          ")
    print("==================================================")
    print(f"• Primary URL: {url}")
    print(f"• Secondary URL: {secondary_url or 'None (Single Garment)'}")
    print(f"• Target Aspect Ratio: {aspect_ratio}")
    print(f"• Target Duration: {duration}s ({2 if duration==10 else 3} parallel shots of 5s each)")
    print(f"• Merge Video: {merge_video} (Idea: '{merge_video_idea}')")
    print(f"• Target Stage: {args.stage}")
    print("--------------------------------------------------")
    
    # Dependency Injection
    ingestion = ScraperIngestionService()
    analyzer = GeminiStyleAnalyzer()
    image_gen = ParallelImageGenerator()
    video_gen = ParallelVideoGenerator()
    
    orchestrator = PipelineOrchestrator(
        ingestion_service=ingestion,
        style_analyzer=analyzer,
        image_generator=image_gen,
        video_generator=video_gen
    )
    
    result = orchestrator.run(
        product_url=url, 
        raw_details=details,
        secondary_url=secondary_url,
        secondary_details=secondary_details,
        styles=styles, 
        target_stage=args.stage,
        aspect_ratio=aspect_ratio,
        duration=duration,
        merge_video=merge_video,
        merge_video_idea=merge_video_idea,
        user_prompt=user_prompt
    )
    
    print(f"\nPipeline Result: {result}")
    print(f"Check the 'runs/{result.get('run_id')}' folder for full logs, inputs, and outputs.")

if __name__ == "__main__":
    main()
