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
from .url_utils import normalize_nykaa_beauty_url, extract_beauty_nykaa_id
from .image_hd import transform_to_hd_image_url
from .loop_policy import ensure_playwright_event_loop_policy

class NykaaFetcherError(Exception):
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

def fetch_nykaa_beauty_html_playwright(url: str, timeout: int = 35000) -> str:
    """
    Playwright fetcher with stealth scripts to fetch Nykaa Beauty (nykaa.com) pages.
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

                # Warm up session on nykaa.com
                page.goto("https://www.nykaa.com", wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(1000)

                resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                page.wait_for_timeout(2500)

                content = page.content()
                browser.close()

                if content and ("__PRELOADED_STATE__" in content or "productPage" in content or "application/ld+json" in content):
                    return content
                else:
                    last_error = f"Page loaded with status {resp.status if resp else 'None'} but state payload missing"
            except Exception as e:
                last_error = e
                print(f"Warning: Nykaa Beauty Playwright fetch with channel={channel} failed: {e!r}")

    raise NykaaFetcherError(f"All Playwright fetch channels failed for Nykaa Beauty URL {url}: {last_error}")

def fetch_nykaa_beauty_html_http(url: str, timeout: int = 12) -> str:
    """
    HTTP urllib fallback fetcher for Nykaa Beauty.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': 'https://www.nykaa.com/'
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
        raise NykaaFetcherError(f"HTTP fetch failed for {url}: {e}")

class NykaaScraper:
    """
    Production-grade Nykaa Beauty (nykaa.com) product scraper supporting window.__PRELOADED_STATE__,
    JSON-LD product schema, and fallback HTML meta tag parsing.
    """

    @classmethod
    def scrape_url(cls, url_or_id: str) -> ProductData:
        """
        Scrapes a product page on Nykaa Beauty given a URL or product ID.
        """
        nykaa_id = extract_beauty_nykaa_id(url_or_id)
        canonical_url = normalize_nykaa_beauty_url(url_or_id)

        try:
            html = fetch_nykaa_beauty_html_playwright(canonical_url)
            return cls.parse_html(html, canonical_url, nykaa_id or "")
        except Exception as e:
            print(f"Playwright fetch warning for Nykaa Beauty: {e}. Trying HTTP fallback...")
            try:
                html = fetch_nykaa_beauty_html_http(canonical_url)
                return cls.parse_html(html, canonical_url, nykaa_id or "")
            except Exception as e2:
                raise NykaaFetcherError(f"Failed to scrape Nykaa Beauty product '{url_or_id}': {e2}")

    @classmethod
    def parse_html(cls, html: str, source_url: str, default_id: str = "") -> ProductData:
        """
        Parses raw Nykaa Beauty PDP HTML using preloaded state, JSON-LD, or meta tags.
        """
        # Primary strategy: Extract product object from window.__PRELOADED_STATE__
        product_dict = cls._extract_preloaded_product(html)
        if product_dict:
            return cls.parse_product_dict(product_dict, source_url, default_id)

        # Secondary strategy: Parse JSON-LD Product schema
        json_ld = cls._extract_json_ld(html)
        if json_ld:
            return cls._parse_json_ld(json_ld, source_url, default_id)

        # Tertiary strategy: Fallback HTML meta tags
        return cls._parse_html_meta(html, source_url, default_id)

    @classmethod
    def _extract_preloaded_product(cls, html: str) -> Optional[Dict[str, Any]]:
        """
        Extracts state.productPage.product from window.__PRELOADED_STATE__.
        """
        idx = html.find('window.__PRELOADED_STATE__')
        if idx != -1:
            start = html.find('{', idx)
            if start != -1:
                try:
                    dec = json.JSONDecoder()
                    obj, _ = dec.raw_decode(html[start:])
                    if isinstance(obj, dict):
                        prod = obj.get('productPage', {}).get('product')
                        if isinstance(prod, dict) and (prod.get('name') or prod.get('productTitle') or prod.get('id')):
                            return prod
                except Exception as e:
                    print(f"Warning: Failed to raw_decode __PRELOADED_STATE__: {e}")

        # Soup fallback for script tags
        soup = BeautifulSoup(html, 'html.parser')
        for script in soup.find_all('script'):
            text = script.string or script.get_text() or ""
            if 'window.__PRELOADED_STATE__' in text or 'productPage' in text:
                m = re.search(r'__PRELOADED_STATE__\s*=\s*({.*?});', text, re.DOTALL)
                if not m:
                    m = re.search(r'({.*?"productPage".*?})', text, re.DOTALL)
                if m:
                    try:
                        obj = json.loads(m.group(1))
                        prod = obj.get('productPage', {}).get('product')
                        if isinstance(prod, dict):
                            return prod
                    except Exception:
                        pass
        return None

    @classmethod
    def parse_product_dict(cls, p: Dict[str, Any], source_url: str, default_id: str = "") -> ProductData:
        """
        Transforms Nykaa Beauty product dictionary into ProductData domain model.
        """
        style_id = str(p.get('id') or p.get('parentId') or default_id or "")

        # 1. Product Identity
        title = p.get('productTitle') or p.get('title') or p.get('name') or f"Nykaa Product {style_id}"
        brand_name = p.get('brandName') or "Nykaa"

        brand_info = BrandInfo(
            name=brand_name,
            uid=str(p.get('brandId') or "")
        )

        # 2. Pricing
        mrp = float(p.get('mrp') or 0.0)
        selling_price = float(p.get('offerPrice') or p.get('mrp') or 0.0)
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

        parent_media = p.get('parentMedia') or p.get('media') or []
        for idx, item in enumerate(parent_media):
            raw_url = ""
            if isinstance(item, dict):
                raw_url = item.get('url') or item.get('imageUrl') or item.get('mediaUrl') or ""
            elif isinstance(item, str):
                raw_url = item

            if raw_url:
                hd_url = transform_to_hd_image_url(raw_url)
                media_list.append(ImageMedia(
                    image_id=str(item.get('id') if isinstance(item, dict) else idx + 1),
                    view_type=str(item.get('mediaType', 'image')) if isinstance(item, dict) else "default",
                    raw_url=raw_url,
                    hd_url=hd_url
                ))
                if hd_url not in hd_urls:
                    hd_urls.append(hd_url)

        if not hd_urls and p.get('imageUrl'):
            single_url = transform_to_hd_image_url(p['imageUrl'])
            media_list.append(ImageMedia(image_id="1", view_type="default", raw_url=p['imageUrl'], hd_url=single_url))
            hd_urls.append(single_url)

        # 4. Sizes / Volume Variants & Inventory
        sizes_list: List[SizeOption] = []
        color_variants: List[ColorVariant] = []

        raw_variants = p.get('variants') or []
        for v in raw_variants:
            if not isinstance(v, dict):
                continue
            v_name = v.get('variantName') or v.get('name') or v.get('title') or ""
            v_id = v.get('childId') or v.get('variantId') or v.get('id')
            v_stock = bool(v.get('inStock', True))
            v_qty = v.get('quantity') or v.get('stock')
            v_qty_count = int(v_qty) if v_qty is not None and str(v_qty).isdigit() else None

            if v_name:
                sizes_list.append(SizeOption(
                    label=str(v_name).strip(),
                    sku_id=str(v_id) if v_id else None,
                    available=v_stock,
                    inventory=v_qty_count
                ))

            # If variant is shade / color
            v_hex = v.get('shadeHex') or v.get('hexCode') or v.get('colorCode')
            v_img = v.get('imageUrl') or v.get('media') or ""
            if isinstance(v_img, list) and v_img:
                v_img = v_img[0].get('url') if isinstance(v_img[0], dict) else str(v_img[0])

            if v_name and (p.get('variantType') == 'shade' or v_hex or "shade" in str(v_name).lower()):
                color_variants.append(ColorVariant(
                    style_id=str(v_id) if v_id else style_id,
                    color_name=str(v_name).strip(),
                    color_hex=str(v_hex) if v_hex else None,
                    image_thumb=transform_to_hd_image_url(str(v_img), width=300) if v_img else None,
                    product_url=f"https://www.nykaa.com/p/{v_id}" if v_id else source_url
                ))

        in_stock = bool(p.get('inStock', True)) and (any(s.available for s in sizes_list) if sizes_list else True)

        # 5. Specifications & Descriptions
        specs_list: List[Specification] = []
        desc_sections: List[Dict[str, str]] = []

        if p.get('sku'):
            specs_list.append(Specification(title="SKU", value=str(p['sku'])))
        if p.get('originOfCountryName'):
            specs_list.append(Specification(title="Country of Origin", value=str(p['originOfCountryName'])))
        if p.get('manufacturerName'):
            specs_list.append(Specification(title="Manufacturer Name", value=str(p['manufacturerName'])))
        if p.get('manufacturerAddress'):
            specs_list.append(Specification(title="Manufacturer Address", value=str(p['manufacturerAddress'])))
        if p.get('expiry'):
            specs_list.append(Specification(title="Expiry Date", value=str(p['expiry'])))
        if p.get('returnMessage'):
            specs_list.append(Specification(title="Return Policy", value=str(p['returnMessage'])))

        # Description HTML cleaning
        raw_desc = p.get('description') or ""
        if raw_desc:
            clean_desc = re.sub(r'<br\s*/?>', '\n', str(raw_desc))
            clean_desc = BeautifulSoup(clean_desc, 'html.parser').get_text().strip()
            if clean_desc:
                desc_sections.append({
                    'title': 'Product Description',
                    'description': clean_desc
                })

        if p.get('ingredients'):
            clean_ing = BeautifulSoup(str(p['ingredients']), 'html.parser').get_text().strip()
            if clean_ing:
                desc_sections.append({'title': 'Ingredients', 'description': clean_ing})

        if p.get('howToUse'):
            clean_use = BeautifulSoup(str(p['howToUse']), 'html.parser').get_text().strip()
            if clean_use:
                desc_sections.append({'title': 'How to Use', 'description': clean_use})

        # 6. Categories & Breadcrumbs
        cat_tree: List[str] = []
        cats_json = p.get('primaryCategories') or p.get('categoryLevels') or {}
        if isinstance(cats_json, dict):
            for level in ['l1', 'l2', 'l3', '0', '1', '2']:
                cat_obj = cats_json.get(level)
                if isinstance(cat_obj, dict) and cat_obj.get('name'):
                    c_name = cat_obj['name']
                    if c_name not in ['Nykaa', 'Home'] and c_name not in cat_tree:
                        cat_tree.append(c_name)

        category = cat_tree[-1] if cat_tree else "Beauty"

        # 7. Ratings & Reviews
        avg_rating = float(p.get('rating') or 0.0)
        total_ratings = int(p.get('ratingCount') or 0)
        total_reviews = int(p.get('reviewCount') or 0)

        star_breakdown: Dict[str, int] = {}
        review_split = p.get('reviewSplitUp') or []
        for star_item in review_split:
            if isinstance(star_item, dict) and star_item.get('id') and star_item.get('count'):
                star_breakdown[str(star_item['id'])] = int(star_item['count'])

        reviews_list: List[Review] = []
        latest_reviews = p.get('latestReviews') or []
        for r in latest_reviews:
            if isinstance(r, dict):
                r_user = r.get('name') or "Verified Buyer"
                r_rating = int(r.get('rating') or 5)
                r_headline = r.get('title') or ""
                r_comment = r.get('description') or ""
                r_date = r.get('createdOnText') or r.get('createdOn') or ""
                r_imgs = [transform_to_hd_image_url(img) for img in r.get('images', []) if isinstance(img, str)]

                reviews_list.append(Review(
                    user_name=r_user,
                    rating=r_rating,
                    headline=r_headline,
                    comment=r_comment,
                    date=r_date,
                    user_photos=r_imgs
                ))

        rating_summary = RatingSummary(
            average_rating=round(avg_rating, 2),
            total_ratings_count=total_ratings,
            total_reviews_count=total_reviews,
            star_breakdown=star_breakdown
        )

        # 8. Seller Info & Service Flags
        is_returnable = bool(p.get('returnAvailable', True))

        seller_info = SellerInfo(
            seller_name=str(p.get('sellerName') or "Nykaa E Retail Limited"),
            seller_rating=4.8,
            return_period_days=7 if is_returnable else 0,
            is_returnable=is_returnable,
            cod_available=True
        )

        service_flags = ServiceFlags(
            is_returnable=is_returnable,
            is_exchangeable=is_returnable,
            cod_enabled=True,
            out_of_stock=not in_stock
        )

        slug = p.get('slug') or ""
        canonical_url = f"https://www.nykaa.com/{slug}/p/{style_id}" if slug else f"https://www.nykaa.com/p/{style_id}"

        return ProductData(
            platform="nykaa",
            style_id=int(style_id) if style_id.isdigit() else style_id,
            canonical_url=canonical_url,
            product_url=source_url,
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
            category=category,
            gender="Women",
            ratings=rating_summary,
            reviews=reviews_list,
            seller=seller_info,
            flags=service_flags,
            manufacturer=str(p.get('manufacturerName') or ''),
            country_of_origin=str(p.get('originOfCountryName') or ''),
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
        style_id = str(ld.get('sku') or ld.get('mpn') or default_id or extract_beauty_nykaa_id(source_url) or "")
        title = ld.get('name') or f"Nykaa Product {style_id}"
        brand_raw = ld.get('brand') or {}
        brand_name = brand_raw.get('name', 'Nykaa') if isinstance(brand_raw, dict) else str(brand_raw)

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
            platform="nykaa",
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
        style_id = default_id or extract_beauty_nykaa_id(source_url) or "0"

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
            platform="nykaa",
            style_id=int(style_id) if style_id.isdigit() else style_id,
            canonical_url=source_url,
            product_url=source_url,
            title=title or f"Nykaa Product {style_id}",
            brand=BrandInfo(name="Nykaa"),
            price=PriceInfo(mrp=0.0, selling_price=0.0),
            hd_image_urls=hd_urls,
            media=[ImageMedia(raw_url=u, hd_url=u) for u in hd_urls],
            in_stock=True,
            ratings=RatingSummary(),
            seller=SellerInfo(),
            flags=ServiceFlags(),
            scraped_at=datetime.utcnow().isoformat() + "Z"
        )
