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
from .url_utils import normalize_bewakoof_url, extract_bewakoof_slug_or_id
from .image_hd import transform_to_hd_image_url
from .loop_policy import ensure_playwright_event_loop_policy

class BewakoofFetcherError(Exception):
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

def fetch_bewakoof_html_playwright(url: str, timeout: int = 35000) -> str:
    """
    Playwright fetcher with stealth scripts to fetch Bewakoof (bewakoof.com) product pages.
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

                # Warm up session on bewakoof.com
                page.goto("https://www.bewakoof.com", wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(1000)

                resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                page.wait_for_timeout(2500)

                content = page.content()
                browser.close()

                if content and ("productDetails" in content or "__NEXT_DATA__" in content or "application/ld+json" in content):
                    return content
                else:
                    last_error = f"Page loaded with status {resp.status if resp else 'None'} but script payload missing"
            except Exception as e:
                last_error = e
                print(f"Warning: Bewakoof Playwright fetch with channel={channel} failed: {e!r}")

    raise BewakoofFetcherError(f"All Playwright fetch channels failed for Bewakoof URL {url}: {last_error}")

def fetch_bewakoof_html_http(url: str, timeout: int = 12) -> str:
    """
    HTTP urllib fallback fetcher for Bewakoof.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': 'https://www.bewakoof.com/'
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
        raise BewakoofFetcherError(f"HTTP fetch failed for {url}: {e}")

class BewakoofScraper:
    """
    Production-grade Bewakoof (bewakoof.com) product scraper supporting pageProps.productDetails extraction,
    JSON-LD product schema, and fallback HTML meta tag parsing.
    """

    @classmethod
    def scrape_url(cls, url_or_slug: str) -> ProductData:
        """
        Scrapes a product page on Bewakoof given a URL, slug, or product ID.
        """
        slug = extract_bewakoof_slug_or_id(url_or_slug)
        canonical_url = normalize_bewakoof_url(url_or_slug)

        try:
            html = fetch_bewakoof_html_playwright(canonical_url)
            return cls.parse_html(html, canonical_url, slug or "")
        except Exception as e:
            print(f"Playwright fetch warning for Bewakoof: {e}. Trying HTTP fallback...")
            try:
                html = fetch_bewakoof_html_http(canonical_url)
                return cls.parse_html(html, canonical_url, slug or "")
            except Exception as e2:
                raise BewakoofFetcherError(f"Failed to scrape Bewakoof product '{url_or_slug}': {e2}")

    @classmethod
    def parse_html(cls, html: str, source_url: str, default_slug: str = "") -> ProductData:
        """
        Parses raw Bewakoof PDP HTML using Next.js pageProps, JSON-LD, or meta tags.
        """
        # Primary strategy: Extract productDetails object from __NEXT_DATA__
        product_dict = cls._extract_next_product_details(html)
        if product_dict:
            return cls.parse_product_dict(product_dict, source_url, default_slug)

        # Secondary strategy: Parse JSON-LD Product schema
        json_ld = cls._extract_json_ld(html)
        if json_ld:
            return cls._parse_json_ld(json_ld, source_url, default_slug)

        # Tertiary strategy: Fallback HTML meta tags
        return cls._parse_html_meta(html, source_url, default_slug)

    @classmethod
    def _extract_next_product_details(cls, html: str) -> Optional[Dict[str, Any]]:
        """
        Extracts productDetails from <script id="__NEXT_DATA__"> or window.__NEXT_DATA__.
        """
        soup = BeautifulSoup(html, 'html.parser')
        next_script = soup.find('script', id='__NEXT_DATA__')
        if next_script:
            text = next_script.string or next_script.get_text() or ""
            if text.strip():
                try:
                    obj = json.loads(text)
                    prod = obj.get('props', {}).get('pageProps', {}).get('productDetails')
                    if isinstance(prod, dict) and (prod.get('name') or prod.get('id')):
                        return prod
                except Exception as e:
                    print(f"Warning: Failed to parse __NEXT_DATA__ script: {e}")

        # Regex scan fallback across all script tags
        for script in soup.find_all('script'):
            text = script.string or script.get_text() or ""
            if 'productDetails' in text:
                m = re.search(r'"productDetails"\s*:\s*({.*?}),\s*"', text, re.DOTALL)
                if not m:
                    m = re.search(r'productDetails\s*=\s*({.*?});', text, re.DOTALL)
                if m:
                    try:
                        prod = json.loads(m.group(1))
                        if isinstance(prod, dict):
                            return prod
                    except Exception:
                        pass
        return None

    @classmethod
    def parse_product_dict(cls, p: Dict[str, Any], source_url: str, default_slug: str = "") -> ProductData:
        """
        Transforms Bewakoof productDetails dictionary into ProductData domain model.
        """
        style_id = str(p.get('id') or default_slug or "")

        # 1. Product Identity & Brand
        title = p.get('name') or p.get('meta_title') or f"Bewakoof Product {style_id}"
        
        # Brand extraction
        brand_name = p.get('brand') or ""
        if not brand_name:
            brand_link_info = p.get('brand_link')
            if isinstance(brand_link_info, dict):
                bl = brand_link_info.get('brand_link')
                if isinstance(bl, dict) and bl.get('brand_name'):
                    brand_name = bl['brand_name'].replace('', '').strip()
        if not brand_name:
            brand_name = "Bewakoof"

        brand_info = BrandInfo(name=brand_name)

        # 2. Pricing
        mrp = float(p.get('mrp') or 0.0)
        selling_price = float(p.get('price') or mrp)
        if mrp <= 0.0 and selling_price > 0.0:
            mrp = selling_price

        discount_amount = max(0.0, mrp - selling_price)
        
        # Parse discount string e.g. "50% OFF"
        disc_str = p.get('product_discount') or ""
        disc_match = re.search(r'(\d+)', str(disc_str))
        if disc_match:
            discount_pct = int(disc_match.group(1))
        else:
            discount_pct = int(round((discount_amount / mrp) * 100)) if mrp > 0 else 0

        price_info = PriceInfo(
            mrp=mrp,
            selling_price=selling_price,
            discount_amount=discount_amount,
            discount_percentage=discount_pct,
            currency="INR"
        )

        # Member price info if available
        member_price = float(p.get('member_price') or 0.0)

        # 3. Media & HD Images
        media_list: List[ImageMedia] = []
        hd_urls: List[str] = []

        images_dict = p.get('images') or {}
        image_items: List[Dict[str, Any]] = []
        if isinstance(images_dict, dict):
            for img_type in ['original', 'display', 'additional']:
                sub_list = images_dict.get(img_type)
                if isinstance(sub_list, list):
                    for img_obj in sub_list:
                        if isinstance(img_obj, dict) and img_obj not in image_items:
                            image_items.append(img_obj)

        for idx, item in enumerate(image_items):
            img_name = item.get('name') or item.get('url') or ""
            if img_name:
                hd_url = transform_to_hd_image_url(img_name)
                raw_url = f"https://images.bewakoof.com/t714/{img_name}" if not img_name.startswith("http") else img_name

                media_list.append(ImageMedia(
                    image_id=str(item.get('id', idx + 1)),
                    view_type="default",
                    raw_url=raw_url,
                    hd_url=hd_url
                ))
                if hd_url not in hd_urls:
                    hd_urls.append(hd_url)

        if not hd_urls and p.get('meta_image'):
            meta_img_url = transform_to_hd_image_url(p['meta_image'])
            media_list.append(ImageMedia(image_id="1", view_type="default", raw_url=p['meta_image'], hd_url=meta_img_url))
            hd_urls.append(meta_img_url)

        # 4. Sizes & Inventory
        sizes_list: List[SizeOption] = []
        raw_sizes = p.get('sizes') or []
        for s in raw_sizes:
            if not isinstance(s, dict):
                continue
            s_name = s.get('name') or s.get('garment_label') or ""
            s_var_id = s.get('product_variant_id') or s.get('product_size_id')
            s_qty = s.get('qty_avail')
            s_qty_count = int(s_qty) if s_qty is not None and str(s_qty).isdigit() else None
            s_status = str(s.get('status', 'enabled')).lower()
            s_avail = (s_qty_count > 0 if s_qty_count is not None else True) and s_status == 'enabled'

            if s_name:
                sizes_list.append(SizeOption(
                    label=str(s_name).strip(),
                    sku_id=str(s_var_id) if s_var_id else None,
                    available=s_avail,
                    inventory=s_qty_count
                ))

        in_stock_val = bool(p.get('in_stock', 1))
        in_stock = in_stock_val and (any(s.available for s in sizes_list) if sizes_list else in_stock_val)

        # 5. Color Variants
        color_variants: List[ColorVariant] = []
        raw_colors = p.get('colors') or []
        for c in raw_colors:
            if not isinstance(c, dict):
                continue
            c_name = c.get('cname') or c.get('name') or ""
            c_code = c.get('code')
            c_id = c.get('id') or style_id
            c_url_slug = c.get('url') or ""
            c_img_file = c.get('display_image') or ""
            c_img_url = transform_to_hd_image_url(c_img_file) if c_img_file else None
            c_url = f"https://www.bewakoof.com/p/{c_url_slug}" if c_url_slug else source_url

            if c_name:
                color_variants.append(ColorVariant(
                    style_id=str(c_id),
                    color_name=str(c_name).strip(),
                    color_hex=str(c_code) if c_code else None,
                    image_thumb=c_img_url,
                    product_url=c_url
                ))

        # 6. Specifications & Descriptions
        specs_list: List[Specification] = []
        desc_sections: List[Dict[str, str]] = []
        country_of_origin = ""
        manufacturer = ""

        # Product attributes map
        attrs = p.get('product_attributes') or {}
        if isinstance(attrs, dict):
            for k, v in attrs.items():
                if v and str(v).strip():
                    title_clean = str(k).replace('_', ' ').title()
                    specs_list.append(Specification(title=title_clean, value=str(v).strip()))

        # Member price spec
        if member_price > 0.0:
            specs_list.append(Specification(title="Tribe Member Price", value=f"₹{member_price:.0f}"))

        # Description list parsing
        raw_desc = p.get('description') or []
        if isinstance(raw_desc, list):
            for block in raw_desc:
                if not isinstance(block, dict):
                    continue
                d_head = block.get('head') or "Description"
                d_line1 = block.get('line1') or ""

                if d_line1:
                    # Clean HTML tags and extract Country of Origin & Manufacturer info
                    clean_line1 = re.sub(r'<br\s*/?>', '\n', str(d_line1))
                    soup_desc = BeautifulSoup(clean_line1, 'html.parser')
                    text_desc = soup_desc.get_text().strip()

                    # Extract Manufacturer & Country of Origin from text
                    m_coo = re.search(r'Country of Origin\s*[-–:]\s*([^\n]+)', text_desc, re.IGNORECASE)
                    if m_coo:
                        country_of_origin = m_coo.group(1).strip()
                        specs_list.append(Specification(title="Country of Origin", value=country_of_origin))

                    m_mfg = re.search(r'Manufactured By\s*[-–:]\s*([^\n]+)', text_desc, re.IGNORECASE)
                    if m_mfg:
                        manufacturer = m_mfg.group(1).strip()
                        specs_list.append(Specification(title="Manufacturer Name", value=manufacturer))

                    desc_sections.append({
                        'title': d_head,
                        'description': text_desc
                    })

                # Check specifications sub-block
                specs_sub = block.get('specifications')
                if isinstance(specs_sub, dict):
                    details_list = specs_sub.get('description_details') or []
                    if details_list:
                        spec_text = "\n".join([str(d) for d in details_list])
                        desc_sections.append({
                            'title': specs_sub.get('header') or "Product Specifications",
                            'description': spec_text
                        })

        # 7. Categories & Breadcrumbs
        cat_tree: List[str] = []
        crumbs = p.get('breadCrumb') or []
        if isinstance(crumbs, list):
            for c in crumbs:
                if isinstance(c, dict) and c.get('name'):
                    cat_tree.append(c['name'])

        cat_info = p.get('category_info') or {}
        if isinstance(cat_info, dict):
            subclass = cat_info.get('subclass') or cat_info.get('subtype') or cat_info.get('cat_type')
            if subclass and subclass not in cat_tree:
                cat_tree.append(subclass)

        category = cat_tree[-1] if cat_tree else "Apparel"
        gender = str(cat_info.get('gender') or p.get('gender') or "Unisex").title()

        # 8. Ratings & Reviews
        ratings_dict = p.get('ratings') or {}
        avg_rating = float(ratings_dict.get('avg') or 0.0) if isinstance(ratings_dict, dict) else 0.0
        rating_count = int(ratings_dict.get('count') or 0) if isinstance(ratings_dict, dict) else 0

        rating_summary = RatingSummary(
            average_rating=round(avg_rating, 2),
            total_ratings_count=rating_count,
            total_reviews_count=rating_count
        )

        # 9. Seller Info & Service Flags
        seller_info = SellerInfo(
            seller_name="Bewakoof Brands Pvt Ltd",
            seller_rating=4.8,
            return_period_days=15,
            is_returnable=True,
            cod_available=True
        )

        service_flags = ServiceFlags(
            is_returnable=True,
            is_exchangeable=True,
            cod_enabled=True,
            out_of_stock=not in_stock
        )

        url_path = p.get('url') or ""
        canonical_url = p.get('canonical_url') or (f"https://www.bewakoof.com/p/{url_path}" if url_path else source_url)

        return ProductData(
            platform="bewakoof",
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
            gender=gender,
            ratings=rating_summary,
            reviews=[],
            seller=seller_info,
            flags=service_flags,
            manufacturer=manufacturer or "Bewakoof Brands Pvt Ltd",
            country_of_origin=country_of_origin or "India",
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
    def _parse_json_ld(cls, ld: Dict[str, Any], source_url: str, default_slug: str = "") -> ProductData:
        """
        Fallback parser converting Schema.org Product JSON-LD into ProductData.
        """
        style_id = str(ld.get('sku') or ld.get('mpn') or default_slug or extract_bewakoof_slug_or_id(source_url) or "")
        title = ld.get('name') or f"Bewakoof Product {style_id}"
        brand_raw = ld.get('brand') or {}
        brand_name = brand_raw.get('name', 'Bewakoof') if isinstance(brand_raw, dict) else str(brand_raw)

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
            platform="bewakoof",
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
            seller=SellerInfo(seller_name="Bewakoof Brands Pvt Ltd"),
            flags=ServiceFlags(),
            scraped_at=datetime.utcnow().isoformat() + "Z"
        )

    @classmethod
    def _parse_html_meta(cls, html: str, source_url: str, default_slug: str = "") -> ProductData:
        """
        Tertiary fallback parser using OpenGraph and meta tags.
        """
        soup = BeautifulSoup(html, 'html.parser')
        style_id = default_slug or extract_bewakoof_slug_or_id(source_url) or "0"

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
            platform="bewakoof",
            style_id=int(style_id) if style_id.isdigit() else style_id,
            canonical_url=source_url,
            product_url=source_url,
            title=title or f"Bewakoof Product {style_id}",
            brand=BrandInfo(name="Bewakoof"),
            price=PriceInfo(mrp=0.0, selling_price=0.0),
            hd_image_urls=hd_urls,
            media=[ImageMedia(raw_url=u, hd_url=u) for u in hd_urls],
            in_stock=True,
            ratings=RatingSummary(),
            seller=SellerInfo(seller_name="Bewakoof Brands Pvt Ltd"),
            flags=ServiceFlags(),
            scraped_at=datetime.utcnow().isoformat() + "Z"
        )
