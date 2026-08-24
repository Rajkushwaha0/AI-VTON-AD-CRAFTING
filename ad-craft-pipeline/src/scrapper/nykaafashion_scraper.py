import json
import re
import urllib.request
from datetime import datetime
from typing import Dict, Any, Optional, List
from bs4 import BeautifulSoup

from .models import (
    ProductData, PriceInfo, BrandInfo, ImageMedia, SizeOption,
    ColorVariant, Specification, RatingSummary, Review, SellerInfo, ServiceFlags
)
from .url_utils import normalize_nykaa_url, extract_nykaa_id
from .image_hd import transform_to_hd_image_url
from .loop_policy import ensure_playwright_event_loop_policy

class NykaaFashionFetcherError(Exception):
    pass

_STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = { runtime: {} };
"""

_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]

_BROWSER_CHANNELS: List[Optional[str]] = ["chrome", "msedge", None]

def fetch_nykaa_html_playwright(url: str, timeout: int = 35000) -> str:
    """
    Playwright fetcher with stealth initialization scripts and browser channel fallback.
    Bypasses Nykaa Fashion Akamai WAF.
    """
    ensure_playwright_event_loop_policy()
    from playwright.sync_api import sync_playwright

    last_error = None
    with sync_playwright() as p:
        for channel in _BROWSER_CHANNELS:
            try:
                launch_opts: Dict[str, Any] = {
                    "headless": True,
                    "args": _LAUNCH_ARGS
                }
                if channel:
                    launch_opts["channel"] = channel

                browser = p.chromium.launch(**launch_opts)
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                    viewport={'width': 1366, 'height': 768},
                    extra_http_headers={
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                        'Sec-Ch-Ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                        'Sec-Ch-Ua-Mobile': '?0',
                        'Sec-Ch-Ua-Platform': '"Windows"',
                        'Sec-Fetch-Dest': 'document',
                        'Sec-Fetch-Mode': 'navigate',
                        'Sec-Fetch-Site': 'none',
                        'Sec-Fetch-User': '?1',
                        'Upgrade-Insecure-Requests': '1'
                    }
                )
                context.add_init_script(_STEALTH_SCRIPT)
                page = context.new_page()

                # If URL is direct product ID, warm up session first
                if "/p/" in url or url.endswith("nykaafashion.com"):
                    page.goto("https://www.nykaafashion.com", wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(1000)

                resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                page.wait_for_timeout(2500)

                content = page.content()
                browser.close()

                if content and ("skuData" in content or "productMedia" in content or "application/ld+json" in content or "details" in content):
                    return content
                else:
                    last_error = f"Page loaded with status {resp.status if resp else 'None'} but script payload missing"
            except Exception as e:
                last_error = e
                print(f"Warning: Nykaa Playwright fetch with channel={channel} failed: {e!r}")

    raise NykaaFashionFetcherError(f"All Playwright fetch channels failed for {url}: {last_error}")

def fetch_nykaa_html_http(url: str, timeout: int = 12) -> str:
    """
    HTTP urllib fallback fetcher with anti-bot headers.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': 'https://www.nykaafashion.com/'
    }

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            encoding = response.headers.get_param('charset') or 'utf-8'
            if response.info().get('Content-Encoding') == 'gzip':
                import gzip
                html = gzip.decompress(response.read()).decode(encoding, errors='replace')
            else:
                html = response.read().decode(encoding, errors='replace')
            return html
    except Exception as e:
        raise NykaaFashionFetcherError(f"HTTP fetch failed for {url}: {e}")

class NykaaFashionScraper:
    """
    Production-grade Nykaa Fashion product scraper supporting Redux state extraction,
    JSON-LD product schema, and fallback HTML meta tag parsing.
    """

    @classmethod
    def scrape_url(cls, url_or_id: str) -> ProductData:
        """
        Scrapes a product page on Nykaa Fashion given a URL or product ID.
        """
        nykaa_id = extract_nykaa_id(url_or_id)
        canonical_url = normalize_nykaa_url(url_or_id)

        try:
            html = fetch_nykaa_html_playwright(canonical_url)
            return cls.parse_html(html, canonical_url, nykaa_id or "")
        except Exception as e:
            print(f"Playwright fetch warning: {e}. Trying HTTP fallback...")
            try:
                html = fetch_nykaa_html_http(canonical_url)
                return cls.parse_html(html, canonical_url, nykaa_id or "")
            except Exception as e2:
                raise NykaaFashionFetcherError(f"Failed to scrape Nykaa product '{url_or_id}': {e2}")

    @classmethod
    def parse_html(cls, html: str, source_url: str, default_id: str = "") -> ProductData:
        """
        Parses raw Nykaa PDP HTML using Redux state, JSON-LD, or meta tags.
        """
        # Primary strategy: Extract product object from embedded Redux state script
        product_dict = cls._extract_redux_product(html)
        if product_dict:
            return cls.parse_product_dict(product_dict, source_url, default_id)

        # Secondary strategy: Parse JSON-LD Product schema
        json_ld = cls._extract_json_ld(html)
        if json_ld:
            return cls._parse_json_ld(json_ld, source_url, default_id)

        # Tertiary strategy: Fallback HTML meta tags
        return cls._parse_html_meta(html, source_url, default_id)

    @classmethod
    def _extract_redux_product(cls, html: str) -> Optional[Dict[str, Any]]:
        """
        Extracts product object from Redux state embedded in script tags.
        """
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. Search script tags for 'skuData' or 'details' or '__NEXT_DATA__'
        for script in soup.find_all('script'):
            text = script.string or script.get_text() or ""
            if not text.strip():
                continue

            if 'skuData' in text or 'productMedia' in text or 'details' in text:
                # Try raw json decode if the whole script is a JSON object
                text_clean = text.strip()
                if text_clean.startswith('{') and text_clean.endswith('}'):
                    try:
                        obj = json.loads(text_clean)
                        prod = cls._find_product_in_obj(obj)
                        if prod:
                            return prod
                    except Exception:
                        pass

                # Try finding json substring inside script text
                start_idx = text.find('{')
                while start_idx != -1:
                    try:
                        dec = json.JSONDecoder()
                        obj, _ = dec.raw_decode(text[start_idx:])
                        prod = cls._find_product_in_obj(obj)
                        if prod:
                            return prod
                        break
                    except Exception:
                        start_idx = text.find('{', start_idx + 1)

        # 2. Check Next.js data script
        next_script = soup.find('script', id='__NEXT_DATA__')
        if next_script:
            try:
                obj = json.loads(next_script.string or next_script.get_text())
                prod = cls._find_product_in_obj(obj)
                if prod:
                    return prod
            except Exception:
                pass

        return None

    @classmethod
    def _find_product_in_obj(cls, obj: Any) -> Optional[Dict[str, Any]]:
        """
        Recursively searches an object for Nykaa's product data dictionary.
        """
        if not isinstance(obj, dict):
            return None

        # Direct path: details -> skuData -> product
        if "details" in obj and isinstance(obj["details"], dict):
            sku_data = obj["details"].get("skuData")
            if isinstance(sku_data, dict) and "product" in sku_data and isinstance(sku_data["product"], dict):
                return sku_data["product"]

        if "skuData" in obj and isinstance(obj["skuData"], dict) and "product" in obj["skuData"]:
            if isinstance(obj["skuData"]["product"], dict):
                return obj["skuData"]["product"]

        if "product" in obj and isinstance(obj["product"], dict) and ("productMedia" in obj["product"] or "title" in obj["product"] or "discountedPrice" in obj["product"]):
            return obj["product"]

        # Recursive key search
        for k, v in obj.items():
            if isinstance(v, dict):
                res = cls._find_product_in_obj(v)
                if res:
                    return res

        return None

    @classmethod
    def parse_product_dict(cls, p: Dict[str, Any], source_url: str, default_id: str = "") -> ProductData:
        """
        Transforms Nykaa product JSON dictionary into ProductData domain model.
        """
        style_id = str(p.get('id') or p.get('productId') or p.get('parentId') or default_id or "")

        # 1. Title & Brand Identity
        brand_name = p.get('title') or p.get('brandName') or p.get('categoryName') or ""
        subtitle = p.get('subTitle') or p.get('name') or ""

        if brand_name and subtitle:
            if subtitle.lower().startswith(brand_name.lower()):
                full_title = subtitle
            else:
                full_title = f"{brand_name} {subtitle}"
        else:
            full_title = brand_name or subtitle or f"Nykaa Fashion Product {style_id}"

        brand_info = BrandInfo(
            name=brand_name or "Nykaa Fashion",
            uid=str(p.get('brandId') or "")
        )

        # 2. Pricing
        mrp = float(p.get('price') or p.get('mrp') or 0.0)
        selling_price = float(p.get('discountedPrice') or p.get('sp') or mrp)
        if mrp <= 0.0 and selling_price > 0.0:
            mrp = selling_price

        discount_amount = max(0.0, mrp - selling_price)
        raw_discount = p.get('discount')
        if raw_discount is not None and str(raw_discount).isdigit():
            discount_pct = int(raw_discount)
        else:
            discount_pct = int(round((discount_amount / mrp) * 100)) if mrp > 0 else 0

        price_info = PriceInfo(
            mrp=mrp,
            selling_price=selling_price,
            discount_amount=discount_amount,
            discount_percentage=discount_pct,
            currency="INR"
        )

        # 3. Media & HD Images
        media_list: List[ImageMedia] = []
        hd_urls: List[str] = []

        product_media = p.get('productMedia') or p.get('media') or []
        for idx, item in enumerate(product_media):
            raw_url = ""
            if isinstance(item, dict):
                raw_url = item.get('url') or item.get('imageUrl') or item.get('mediaUrl') or item.get('src') or ""
            elif isinstance(item, str):
                raw_url = item

            if raw_url:
                hd_url = transform_to_hd_image_url(raw_url)
                media_list.append(ImageMedia(
                    image_id=str(item.get('id') if isinstance(item, dict) else idx + 1),
                    view_type=str(item.get('position', idx + 1)) if isinstance(item, dict) else "default",
                    raw_url=raw_url,
                    hd_url=hd_url
                ))
                if hd_url not in hd_urls:
                    hd_urls.append(hd_url)

        if not hd_urls and p.get('imageUrl'):
            single_url = transform_to_hd_image_url(p['imageUrl'])
            media_list.append(ImageMedia(image_id="1", view_type="default", raw_url=p['imageUrl'], hd_url=single_url))
            hd_urls.append(single_url)

        # 4. Size Options & Inventory
        sizes_list: List[SizeOption] = []
        size_options_raw = p.get('sizeOptions') or {}
        raw_options = []
        if isinstance(size_options_raw, dict):
            raw_options = size_options_raw.get('options') or []
        elif isinstance(size_options_raw, list):
            raw_options = size_options_raw

        for opt in raw_options:
            if isinstance(opt, dict):
                label = opt.get('size') or opt.get('label') or opt.get('name') or opt.get('optionValue') or ""
                sku_val = opt.get('variant_id') or opt.get('sku') or opt.get('optionId') or opt.get('id')
                qty = opt.get('quantity') or opt.get('inventory') or opt.get('stock')
                inv_count = int(qty) if qty is not None and str(qty).isdigit() else None
                in_stock_val = bool(opt.get('in_stock', True)) if qty is None else (inv_count > 0 if inv_count is not None else True)

                if label:
                    sizes_list.append(SizeOption(
                        label=str(label).strip(),
                        sku_id=str(sku_val) if sku_val else None,
                        available=in_stock_val,
                        inventory=inv_count
                    ))

        is_out_of_stock = bool(p.get('isOutOfStock', 0))
        in_stock = not is_out_of_stock and (any(s.available for s in sizes_list) if sizes_list else not is_out_of_stock)

        # 5. Color Variants Extraction
        color_variants: List[ColorVariant] = []
        seen_color_ids = set()

        # Step 1: Add current product swatch if color info is available
        curr_color = p.get('product_detail_color') or p.get('color') or "Current Color"
        curr_img = None
        if media_list:
            curr_img = media_list[0].hd_url or media_list[0].raw_url
        if not curr_img:
            curr_img = p.get('imageUrl')

        if curr_color and style_id:
            seen_color_ids.add(style_id)
            color_variants.append(ColorVariant(
                style_id=style_id,
                color_name=str(curr_color).title(),
                image_thumb=transform_to_hd_image_url(curr_img, width=300) if curr_img else None,
                product_url=source_url
            ))

        # Step 2: Add sister color variants from colorOptions / colors
        raw_colors = p.get('colorOptions') or p.get('colors') or []
        for c in raw_colors:
            if isinstance(c, dict):
                c_name = c.get('name') or c.get('colorName') or c.get('product_detail_color') or ""
                c_hex = c.get('colorCode') or c.get('hex') or None
                c_action = c.get('action_url') or c.get('actionUrl') or ""
                c_id = c.get('id') or c.get('productId')
                if not c_id and c_action:
                    m_pid = re.search(r'/p/(\d+)', c_action)
                    if m_pid:
                        c_id = m_pid.group(1)

                c_img = c.get('image_url') or c.get('imageUrl') or c.get('image') or c.get('swatch') or ""
                c_url = f"https://www.nykaafashion.com{c_action}" if c_action.startswith('/') else (c_action if c_action.startswith('http') else (f"https://www.nykaafashion.com/p/{c_id}" if c_id else source_url))

                v_id = str(c_id or c_name)
                if v_id and v_id not in seen_color_ids:
                    seen_color_ids.add(v_id)
                    color_variants.append(ColorVariant(
                        style_id=v_id,
                        color_name=str(c_name).title(),
                        color_hex=c_hex,
                        image_thumb=transform_to_hd_image_url(c_img, width=300) if c_img else None,
                        product_url=c_url
                    ))

        # 6. Specifications & Descriptions
        specs_list: List[Specification] = []
        desc_sections: List[Dict[str, str]] = []
        sold_by = ""
        manufacturer = ""
        country_of_origin = ""

        # Color spec
        if p.get('product_detail_color'):
            specs_list.append(Specification(title="Color", value=str(p['product_detail_color'])))

        pdp_sections = p.get('pdp_sections') or []
        for sec in pdp_sections:
            if not isinstance(sec, dict):
                continue
            sec_title = sec.get('title') or "Details"
            sec_val = sec.get('value') or ""

            # Check child_widgets / attributes
            child_widgets = sec.get('child_widgets') or []
            for cw in child_widgets:
                if isinstance(cw, dict) and cw.get('attributes'):
                    for attr in cw['attributes']:
                        if isinstance(attr, dict) and attr.get('label') and attr.get('value'):
                            specs_list.append(Specification(
                                title=str(attr['label']).strip(),
                                value=str(attr['value']).strip()
                            ))

            # Attributes directly on section (e.g. Vendor Details)
            if sec.get('attributes'):
                for attr in sec['attributes']:
                    if isinstance(attr, dict) and attr.get('label') and attr.get('value'):
                        lbl = str(attr['label']).strip()
                        val = str(attr['value']).strip()
                        specs_list.append(Specification(title=lbl, value=val))

                        if "sold by" in lbl.lower():
                            sold_by = val
                        elif "country of origin" in lbl.lower():
                            country_of_origin = val
                        elif "manufacturer" in lbl.lower():
                            manufacturer = val

            if sec_val:
                clean_val = re.sub(r'<br\s*/?>', '\n', str(sec_val))
                clean_val = BeautifulSoup(clean_val, 'html.parser').get_text().strip()
                if clean_val:
                    desc_sections.append({
                        'title': sec_title,
                        'description': clean_val
                    })

        # 7. Categories & Breadcrumbs
        cat_tree: List[str] = []
        cats_json = p.get('primary_categories_json_all') or {}
        if isinstance(cats_json, dict):
            for level in ['l1_Category', 'l2_Category', 'l3_Category']:
                cat_obj = cats_json.get(level)
                if isinstance(cat_obj, dict) and cat_obj.get('name'):
                    c_name = cat_obj['name']
                    if c_name not in ['Nykaa', 'Nykaa Fashion'] and c_name not in cat_tree:
                        cat_tree.append(c_name)

        category = cat_tree[-1] if cat_tree else (p.get('categoryName') or "Apparel")
        gender = "Women" if "women" in [c.lower() for c in cat_tree] or "women" in full_title.lower() else ("Men" if "men" in [c.lower() for c in cat_tree] or "men" in full_title.lower() else "Unisex")

        # 8. Ratings & Reviews
        ratings_raw = p.get('review_rating_json') or {}
        avg_rating = float(ratings_raw.get('star_rating') or ratings_raw.get('averageRating') or 0.0)
        total_ratings = int(ratings_raw.get('star_rating_count') or ratings_raw.get('totalCount') or 0)
        total_reviews = int(ratings_raw.get('review_count_v2') or ratings_raw.get('review_count') or 0)

        reviews_list: List[Review] = []
        top_reviews = ratings_raw.get('top_reviews_v2') or ratings_raw.get('top_reviews') or []
        for r in top_reviews:
            if isinstance(r, dict):
                r_user = r.get('nickname') or "Verified Buyer"
                r_rating = int(r.get('rating') or 5)
                r_comment = r.get('detail') or r.get('reviewText') or ""
                r_date = r.get('created_at') or ""
                r_imgs = [transform_to_hd_image_url(img) for img in r.get('images', []) if isinstance(img, str)]

                reviews_list.append(Review(
                    user_name=r_user,
                    rating=r_rating,
                    headline="",
                    comment=r_comment,
                    date=r_date,
                    user_photos=r_imgs
                ))

        rating_summary = RatingSummary(
            average_rating=round(avg_rating, 2),
            total_ratings_count=total_ratings,
            total_reviews_count=total_reviews
        )

        # 9. Seller Info & Service Flags
        is_returnable = bool(p.get('isReturnable', 1))
        return_period = int(p.get('return_period') or 7)
        cod_data = p.get('cash_on_delivery') or {}
        cod_available = bool(cod_data.get('enable', True)) if isinstance(cod_data, dict) else True

        seller_info = SellerInfo(
            seller_name=sold_by or brand_name or "Nykaa Fashion Preferred Seller",
            seller_rating=4.7,
            return_period_days=return_period if is_returnable else 0,
            is_returnable=is_returnable,
            cod_available=cod_available
        )

        service_flags = ServiceFlags(
            is_returnable=is_returnable,
            is_exchangeable=is_returnable,
            cod_enabled=cod_available,
            out_of_stock=not in_stock
        )

        action_url = p.get('action_url') or ""
        canonical_url = f"https://www.nykaafashion.com{action_url}" if action_url else f"https://www.nykaafashion.com/p/{style_id}"

        return ProductData(
            platform="nykaafashion",
            style_id=int(style_id) if style_id.isdigit() else style_id,
            canonical_url=canonical_url,
            product_url=source_url,
            title=full_title,
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
            category=category,
            gender=gender,
            ratings=rating_summary,
            reviews=reviews_list,
            seller=seller_info,
            flags=service_flags,
            manufacturer=manufacturer,
            country_of_origin=country_of_origin,
            scraped_at=datetime.utcnow().isoformat() + "Z"
        )

    @classmethod
    def _extract_json_ld(cls, html: str) -> Optional[Dict[str, Any]]:
        """
        Extracts Product JSON-LD block from HTML.
        """
        soup = BeautifulSoup(html, 'html.parser')
        for script in soup.find_all('script', type='application/ld+json'):
            text = script.string or script.get_text() or ""
            if not text.strip():
                continue
            try:
                data = json.loads(text)
                candidates = data if isinstance(data, list) else [data]
                for item in candidates:
                    if isinstance(item, dict) and item.get('@type') in ('Product', 'ProductGroup'):
                        return item
            except Exception:
                continue
        return None

    @classmethod
    def _parse_json_ld(cls, ld: Dict[str, Any], source_url: str, default_id: str = "") -> ProductData:
        """
        Fallback parser converting Schema.org Product JSON-LD into ProductData.
        """
        style_id = str(ld.get('sku') or ld.get('mpn') or default_id or extract_nykaa_id(source_url) or "")
        title = ld.get('name') or f"Nykaa Product {style_id}"
        brand_raw = ld.get('brand') or {}
        brand_name = brand_raw.get('name', 'Nykaa Fashion') if isinstance(brand_raw, dict) else str(brand_raw)

        offers = ld.get('offers') or {}
        if isinstance(offers, list) and offers:
            offers = offers[0]

        price_val = 0.0
        if isinstance(offers, dict):
            price_val = float(offers.get('price') or 0.0)

        images_raw = ld.get('image') or []
        if isinstance(images_raw, str):
            images_raw = [images_raw]

        hd_urls = [transform_to_hd_image_url(img) for img in images_raw if img]
        media_list = [ImageMedia(image_id=str(idx+1), view_type="default", raw_url=u, hd_url=u) for idx, u in enumerate(hd_urls)]

        return ProductData(
            platform="nykaafashion",
            style_id=int(style_id) if style_id.isdigit() else style_id,
            canonical_url=source_url,
            product_url=source_url,
            title=title,
            brand=BrandInfo(name=brand_name),
            price=PriceInfo(mrp=price_val, selling_price=price_val),
            media=media_list,
            hd_image_urls=hd_urls,
            in_stock=True,
            ratings=RatingSummary(),
            seller=SellerInfo(seller_name=brand_name),
            flags=ServiceFlags(),
            scraped_at=datetime.utcnow().isoformat() + "Z"
        )

    @classmethod
    def _parse_html_meta(cls, html: str, source_url: str, default_id: str = "") -> ProductData:
        """
        Tertiary fallback parser using OpenGraph and meta tags.
        """
        soup = BeautifulSoup(html, 'html.parser')
        style_id = default_id or extract_nykaa_id(source_url) or "0"

        title = ""
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            title = og_title['content']
        elif soup.title:
            title = soup.title.string or ""

        hd_urls = []
        for meta_img in soup.find_all('meta', property='og:image'):
            if meta_img.get('content'):
                hd_urls.append(transform_to_hd_image_url(meta_img['content']))

        return ProductData(
            platform="nykaafashion",
            style_id=int(style_id) if style_id.isdigit() else style_id,
            canonical_url=source_url,
            product_url=source_url,
            title=title or f"Nykaa Product {style_id}",
            brand=BrandInfo(name="Nykaa Fashion"),
            price=PriceInfo(mrp=0.0, selling_price=0.0),
            hd_image_urls=hd_urls,
            media=[ImageMedia(raw_url=u, hd_url=u) for u in hd_urls],
            in_stock=True,
            ratings=RatingSummary(),
            seller=SellerInfo(),
            flags=ServiceFlags(),
            scraped_at=datetime.utcnow().isoformat() + "Z"
        )
