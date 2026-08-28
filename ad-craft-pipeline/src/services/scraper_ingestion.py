import os
import sys
import json
from typing import Dict, Any, List, Optional
import urllib.request

from ..core.interfaces import IIngestionService
from ..core.models import PipelineContext, ProductData

class ScraperIngestionService(IIngestionService):
    def ingest(
        self, 
        context: PipelineContext, 
        product_url: str = None, 
        raw_details: dict = None,
        secondary_url: str = None,
        secondary_details: dict = None
    ) -> ProductData:
        context.log(f"Starting ingestion. Primary URL: {product_url}, Secondary URL: {secondary_url}")
        
        # Ingest Primary Product
        primary_title, primary_price, primary_images, primary_raw = self._ingest_single(
            context, product_url, raw_details, target_dir=context.primary_input_dir, prefix="primary"
        )
        
        # Ingest Secondary Product (if provided)
        secondary_title, secondary_price, secondary_images, secondary_raw = None, None, [], {}
        if secondary_url or secondary_details:
            context.log("Ingesting Secondary Garment for Two-Piece Pairing...")
            secondary_title, secondary_price, secondary_images, secondary_raw = self._ingest_single(
                context, secondary_url, secondary_details, target_dir=context.secondary_input_dir, prefix="secondary"
            )
            context.log(f"Secondary Garment Ingested: '{secondary_title}' with {len(secondary_images)} images.")

        # Save metadata JSONs in input directories
        if primary_raw:
            with open(os.path.join(context.primary_input_dir, "product_info.json"), "w", encoding="utf-8") as f:
                json.dump(primary_raw, f, indent=4)
        if secondary_raw:
            with open(os.path.join(context.secondary_input_dir, "product_info.json"), "w", encoding="utf-8") as f:
                json.dump(secondary_raw, f, indent=4)

        return ProductData(
            title=primary_title or "Primary Garment",
            price=primary_price,
            currency="INR",
            key_selling_points=[],
            image_paths=primary_images,
            secondary_title=secondary_title,
            secondary_price=secondary_price,
            secondary_image_paths=secondary_images,
            raw_data={
                "primary": primary_raw,
                "secondary": secondary_raw
            },
            aspect_ratio=context.aspect_ratio
        )

    def _ingest_single(
        self,
        context: PipelineContext,
        url: Optional[str],
        details: Optional[dict],
        target_dir: str,
        prefix: str
    ) -> tuple[str, Optional[float], List[str], Dict[str, Any]]:
        has_images = bool(details and details.get("image_urls") and len(details.get("image_urls")) > 0)
        has_title = bool(details and details.get("title"))

        if has_images and has_title:
            context.log(f"Using provided raw details for {prefix} product.")
            title = details.get("title", f"Provided {prefix.title()} Product")
            price = details.get("price")
            image_urls = details.get("image_urls", [])
            downloaded = self._download_images(context, image_urls, target_dir)
            return title, price, downloaded, details

        if not url:
            if details:
                return details.get("title", f"{prefix} Product"), details.get("price"), [], details
            return f"Mock {prefix} Product", 1999.0, [], {"mocked": True}

        context.log(f"Attempting to scrape {prefix} product from {url}")
        
        src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)

        try:
            from scrapper import scrape_product
            pdata = scrape_product(url)

            if not pdata:
                raise RuntimeError(f"Scraper returned no data for {url}")

            hd_urls = getattr(pdata, "hd_image_urls", [])
            title = getattr(pdata, "title", f"Scraped {prefix.title()} Product")
            price_obj = getattr(pdata, "price", None)
            price_val = getattr(price_obj, "selling_price", None) if price_obj else None

            context.log(f"Scrape successful: {title}, found {len(hd_urls)} images.")
            downloaded_paths = self._download_images(context, hd_urls, target_dir)
            raw_dict = pdata.model_dump() if hasattr(pdata, "model_dump") else {}

            return title, price_val, downloaded_paths, raw_dict

        except Exception as e:
            context.log(f"Scraping {prefix} failed: {e}. Using fallback.")
            return f"Mock {prefix} Product", 1999.0, [], {"error": str(e), "url": url}

    def _download_images(self, context: PipelineContext, urls: List[str], target_dir: str) -> List[str]:
        os.makedirs(target_dir, exist_ok=True)
        paths = []
        for idx, url in enumerate(urls):
            try:
                if not url or not url.startswith("http"):
                    continue
                ext = url.split(".")[-1].split("?")[0]
                if ext not in ["jpg", "jpeg", "png", "webp"]:
                    ext = "jpg"
                filename = f"product_{idx}.{ext}"
                filepath = os.path.join(target_dir, filename)

                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=12) as response, open(filepath, 'wb') as out_file:
                    out_file.write(response.read())

                paths.append(filepath)
                context.log(f"Downloaded image: {url} -> {os.path.basename(target_dir)}/{filename}")
            except Exception as e:
                context.log(f"Failed to download image {url}: {e}")
        return paths
