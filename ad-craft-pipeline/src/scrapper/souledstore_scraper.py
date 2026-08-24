import re
import json
import urllib.request
from datetime import datetime
from typing import Dict, Any, Optional, List
from bs4 import BeautifulSoup

from .models import (
    ProductData, PriceInfo, BrandInfo, ImageMedia, SizeOption,
    ColorVariant, Specification, RatingSummary, Review, SellerInfo, ServiceFlags
)
from .url_utils import normalize_souledstore_url, extract_souledstore_slug
from .loop_policy import ensure_playwright_event_loop_policy

class SouledStoreFetcherError(Exception):
    pass

def fetch_souledstore_api_data(slug: str, timeout: int = 12) -> Dict[str, Any]:
    """
    Fetches raw product data, pricing, and inventory directly from The Souled Store REST APIs.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Origin': 'https://www.thesouledstore.com',
        'Referer': f'https://www.thesouledstore.com/product/{slug}',
        'Accept-Language': 'en-US,en;q=0.9'
    }

    def _get_json(endpoint: str) -> Optional[Dict[str, Any]]:
        req = urllib.request.Request(endpoint, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                content = response.read().decode('utf-8', errors='replace')
                return json.loads(content)
        except Exception as e:
            print(f"Warning: SouledStore API call to {endpoint} failed: {e}")
            return None

    prod_url = f"https://api.thesouledstore.com/api/v2/static/product/{slug}"
    pricing_url = f"https://api.thesouledstore.com/api/v2/product/{slug}/pricing"
    inventory_url = f"https://api.thesouledstore.com/api/v2/product/{slug}/inventory"

    product_data = _get_json(prod_url)
    if not product_data:
        raise SouledStoreFetcherError(f"Static product API failed for slug: '{slug}'")

    pricing_data = _get_json(pricing_url) or {}
    inventory_data = _get_json(inventory_url) or {}

    return {
        'product': product_data,
        'pricing': pricing_data,
        'inventory': inventory_data
    }

def fetch_souledstore_html_playwright(url: str, timeout: int = 30000) -> str:
    """
    Playwright headless browser fetcher fallback for The Souled Store.
    """
    try:
        ensure_playwright_event_loop_policy()
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                viewport={'width': 1366, 'height': 768}
            )
            page = context.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=timeout)
            page.wait_for_timeout(2500)
            content = page.content()
            browser.close()
            return content
    except Exception as e:
        raise SouledStoreFetcherError(f"Souled Store Playwright fetch failed: {e!r}")

def format_brand_name(artist_slug: Optional[str], raw_product_name: Optional[str] = None) -> str:
    """
    Formats artist / merch slug into a clean brand name.
    """
    if artist_slug:
        clean = artist_slug.replace('-', ' ').title()
        if "Merchandise" in clean or "Originals" in clean or "Official" in clean:
            return clean
        return f"{clean} Official Merchandise"
    if raw_product_name and ":" in raw_product_name:
        return raw_product_name.split(":")[0].strip()
    return "The Souled Store"

class SouledStoreScraper:
    """
    Production-grade The Souled Store Product Scraper.
    """

    @classmethod
    def scrape_url(cls, url_or_slug: str) -> ProductData:
        """
        Scrapes a product page on The Souled Store given a URL or slug.
        """
        slug = extract_souledstore_slug(url_or_slug)
        if not slug:
            raise ValueError(f"Could not extract product slug from: '{url_or_slug}'")

        canonical_url = normalize_souledstore_url(url_or_slug)

        try:
            api_payload = fetch_souledstore_api_data(slug)
            return cls.parse_api_payload(api_payload, canonical_url, slug)
        except Exception as e:
            print(f"API fetch warning: {e}. Falling back to Playwright headless...")
            html = fetch_souledstore_html_playwright(canonical_url)
            return cls.parse_html_fallback(html, canonical_url, slug)

    @classmethod
    def parse_api_payload(cls, payload: Dict[str, Any], canonical_url: str, slug: str) -> ProductData:
        """
        Transforms Souled Store API response JSON into ProductData domain model.
        """
        p = payload.get('product') or {}
        pr = payload.get('pricing') or {}
        inv = payload.get('inventory') or {}

        style_id = p.get('id') or p.get('_id') or slug

        # 1. Product Identity
        raw_title = p.get('product') or ""
        meta_title = p.get('meta_title') or ""
        title = raw_title if raw_title else (meta_title.split(" Online")[0].replace("Buy ", "") if meta_title else slug.replace("-", " ").title())

        # 2. Brand
        artist_slug = p.get('artist_slug')
        brand_name = format_brand_name(artist_slug, raw_title)
        brand_info = BrandInfo(name=brand_name)

        # 3. Pricing
        mrp = float(pr.get('mrp') or p.get('mrp') or pr.get('price') or 0.0)
        
        # selling_price evaluation
        spl_price = float(pr.get('spl_price') or 0.0)
        exclusive_price = float(pr.get('exclusive_price') or 0.0)
        regular_price = float(pr.get('price') or mrp)

        if spl_price > 0:
            selling_price = spl_price
        elif regular_price > 0 and regular_price < mrp:
            selling_price = regular_price
        elif exclusive_price > 0 and exclusive_price < mrp:
            selling_price = exclusive_price
        else:
            selling_price = regular_price if regular_price > 0 else mrp

        if mrp <= 0 and selling_price > 0:
            mrp = selling_price

        discount_amount = max(0.0, mrp - selling_price)
        discount_pct = int(round((discount_amount / mrp) * 100)) if mrp > 0 else 0

        price_info = PriceInfo(
            mrp=mrp,
            selling_price=selling_price,
            discount_amount=discount_amount,
            discount_percentage=discount_pct,
            currency="INR"
        )

        # 4. Images & Media
        media_list: List[ImageMedia] = []
        hd_urls: List[str] = []

        images_raw = p.get('images') or p.get('desktop_images') or []
        for idx, img_val in enumerate(images_raw):
            if isinstance(img_val, str):
                img_file = img_val
            elif isinstance(img_val, dict):
                img_file = img_val.get('name') or img_val.get('src') or ""
            else:
                img_file = ""

            if img_file:
                if img_file.startswith("http"):
                    raw_url = img_file
                    hd_url = img_file
                else:
                    raw_url = f"https://prod-img.thesouledstore.com/public/theSoul/uploads/catalog/product/{img_file}"
                    hd_url = f"https://prod-img.thesouledstore.com/public/theSoul/uploads/catalog/product/{img_file}?w=1080&dpr=1"

                media_list.append(ImageMedia(
                    image_id=str(idx + 1),
                    view_type="default",
                    raw_url=raw_url,
                    hd_url=hd_url
                ))
                if hd_url not in hd_urls:
                    hd_urls.append(hd_url)

        # 5. Sizes & Inventory
        sizes_list: List[SizeOption] = []
        variants = inv.get('variant') or []
        for var in variants:
            size_val = ""
            for attr in var.get('attributes', []):
                if attr.get('name') == 'size':
                    size_val = attr.get('value', '').strip()
                    break

            if not size_val:
                # Fallback parse from variant name e.g. "Oversized T-Shirt: MechaSoul - Variant ( M )"
                var_name = var.get('name', '')
                m = re.search(r'\(\s*([A-Za-z0-9]+)\s*\)', var_name)
                if m:
                    size_val = m.group(1)

            stock = int(var.get('stock') or 0)
            if size_val:
                sizes_list.append(SizeOption(
                    label=size_val,
                    sku_id=str(var.get('id', '')),
                    available=(stock > 0),
                    inventory=stock
                ))

        total_prod_qty = int(inv.get('prod_qty') or 0)
        in_stock = any(s.available for s in sizes_list) if sizes_list else (total_prod_qty > 0 or p.get('is_avail', 1) == 1)

        # 6. Color Variants / Shop by Variant/Look
        color_variants: List[ColorVariant] = []
        seen_variant_ids = set()

        # Strategy A: Parse color_group_details (Shop by Variant/Look)
        c_groups = p.get('color_group_details') or []
        for cg in c_groups:
            if isinstance(cg, dict):
                v_prod_id = str(cg.get('prod_id') or '')
                v_url_key = cg.get('url_key') or ''
                v_hex = cg.get('color') or ''
                def_img = cg.get('default_image') or ''

                if v_url_key and v_url_key != slug and v_url_key not in seen_variant_ids:
                    seen_variant_ids.add(v_url_key)
                    v_name = v_url_key.replace('-', ' ').title()
                    
                    if def_img:
                        thumb_url = def_img if def_img.startswith('http') else f"https://prod-img.thesouledstore.com/public/theSoul/uploads/catalog/product/{def_img}?w=300&dpr=1"
                    else:
                        thumb_url = None

                    v_prod_url = f"https://www.thesouledstore.com/product/{v_url_key}"
                    color_variants.append(ColorVariant(
                        style_id=v_prod_id or v_url_key,
                        color_name=v_name,
                        color_hex=v_hex,
                        image_thumb=thumb_url,
                        product_url=v_prod_url
                    ))

        # Strategy B: Fallback raw_colors array
        if not color_variants:
            raw_colors = p.get('colors') or []
            for c in raw_colors:
                if isinstance(c, dict):
                    c_name = c.get('colorName') or c.get('name') or ""
                    c_hex = c.get('colorHex') or c.get('hex') or ""
                    c_id = str(c.get('id') or '')
                    if c_name:
                        color_variants.append(ColorVariant(
                            style_id=c_id or slug,
                            color_name=c_name,
                            color_hex=c_hex
                        ))

        # 7. Specifications
        specs_list: List[Specification] = []
        spec_keys = [
            ('Fabric', 'fabric_name'),
            ('Neck', 'neck'),
            ('Sleeve', 'sleeve'),
            ('Fit Type', 'fittype'),
            ('Pattern', 'patterns'),
            ('Product Type', 'prod_type')
        ]
        for title_key, prop_key in spec_keys:
            val = p.get(prop_key)
            if val and str(val).lower() not in ['none', 'null', '']:
                specs_list.append(Specification(title=title_key, value=str(val)))

        # 8. Description Sections
        desc_sections: List[Dict[str, str]] = []
        user_desc = p.get('user_description') or p.get('long_desc') or p.get('add_desc') or p.get('meta_desc') or ""
        if user_desc:
            clean_desc = re.sub(r'<br\s*/?>', '\n', user_desc)
            clean_desc = BeautifulSoup(clean_desc, 'html.parser').get_text().strip()
            if clean_desc:
                desc_sections.append({
                    'title': 'Product Details',
                    'description': clean_desc
                })

        # 9. Breadcrumbs & Category
        cat_tree: List[str] = []
        primary_cat = p.get('primary_category_name') or p.get('cat_breadcrum_name') or p.get('cat') or p.get('category') or ""
        sub_cat = p.get('breadcrum_title') or p.get('category_slug') or ""
        
        if primary_cat:
            cat_tree.append(str(primary_cat))
        if sub_cat and sub_cat not in cat_tree:
            cat_tree.append(str(sub_cat))

        gender_type = p.get('gender_type')
        gender_str = "Men" if gender_type == 1 else ("Women" if gender_type == 2 else "Unisex")
        if "men" in slug and gender_str == "Unisex":
            gender_str = "Men"
        elif "women" in slug and gender_str == "Unisex":
            gender_str = "Women"

        # 10. Ratings
        avg_rating = float(p.get('avg_rating') or p.get('avg_product_rating') or 0.0)
        rating_count = int(p.get('rating_count') or 0)
        rating_summary = RatingSummary(
            average_rating=round(avg_rating, 2),
            total_ratings_count=rating_count
        )

        # 11. Service Flags & Seller Info
        is_returnable = bool(p.get('is_returnable', 1))
        is_exchangeable = bool(p.get('is_exchangeable', 1))

        seller_info = SellerInfo(
            seller_name="The Souled Store Official",
            seller_rating=4.8,
            return_period_days=7 if is_returnable else 0,
            is_returnable=is_returnable,
            cod_available=True
        )

        service_flags = ServiceFlags(
            is_returnable=is_returnable,
            is_exchangeable=is_exchangeable,
            cod_enabled=True,
            out_of_stock=not in_stock
        )

        return ProductData(
            platform="souledstore",
            style_id=style_id,
            canonical_url=canonical_url,
            product_url=canonical_url,
            title=title,
            subtitle=desc_sections[0]['description'] if desc_sections else None,
            brand=brand_info,
            price=price_info,
            media=media_list,
            hd_image_urls=hd_urls,
            sizes=sizes_list,
            in_stock=in_stock,
            color_variants=color_variants,
            specifications=specs_list,
            product_description_sections=desc_sections,
            breadcrumbs=cat_tree,
            category=primary_cat or "Apparel",
            gender=gender_str,
            ratings=rating_summary,
            reviews=[],
            seller=seller_info,
            flags=service_flags,
            scraped_at=datetime.utcnow().isoformat() + "Z"
        )

    @classmethod
    def parse_html_fallback(cls, html: str, canonical_url: str, slug: str) -> ProductData:
        """
        Fallback HTML parser for The Souled Store using BeautifulSoup.
        """
        soup = BeautifulSoup(html, 'html.parser')

        # Meta title
        og_title = soup.find('meta', property='og:title') or soup.find('meta', id='ogtitle')
        title = og_title.get('content') if og_title and og_title.get('content') else (soup.title.string if soup.title else slug.replace('-', ' ').title())

        # Meta description
        og_desc = soup.find('meta', property='og:description') or soup.find('meta', name='description')
        desc_text = og_desc.get('content') if og_desc and og_desc.get('content') else ""

        # Images
        hd_urls: List[str] = []
        for img in soup.find_all('img'):
            src = img.get('src') or ""
            if ('uploads/catalog/product' in src or 'prod-img.thesouledstore.com' in src) and not src.endswith('.svg') and not src.endswith('.gif'):
                clean_url = src.split('?')[0]
                if clean_url not in hd_urls:
                    hd_urls.append(clean_url)

        return ProductData(
            platform="souledstore",
            style_id=slug,
            canonical_url=canonical_url,
            product_url=canonical_url,
            title=title.strip(),
            brand=BrandInfo(name="The Souled Store"),
            price=PriceInfo(mrp=0.0, selling_price=0.0),
            hd_image_urls=hd_urls,
            media=[ImageMedia(raw_url=u, hd_url=u) for u in hd_urls],
            in_stock=True,
            product_description_sections=[{'title': 'Description', 'description': desc_text}] if desc_text else [],
            ratings=RatingSummary(),
            seller=SellerInfo(seller_name="The Souled Store Official"),
            flags=ServiceFlags(),
            scraped_at=datetime.utcnow().isoformat() + "Z"
        )
