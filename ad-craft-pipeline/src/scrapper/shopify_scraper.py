import os
import re
import json
import urllib.request
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from bs4 import BeautifulSoup

from .models import (
    ProductData, PriceInfo, BrandInfo, ImageMedia, SizeOption,
    ColorVariant, Specification, RatingSummary, Review, SellerInfo, ServiceFlags
)
from .image_hd import transform_to_hd_image_url
from .url_utils import normalize_shopify_url, extract_shopify_handle, detect_platform
from .loop_policy import ensure_playwright_event_loop_policy

class ShopifyFetcherError(Exception):
    pass

def fetch_shopify_json_http(url: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """
    Fetches raw Shopify product JSON using .js or .json endpoint with anti-bot Chrome headers.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate',
        'Sec-Ch-Ua': '"Not-A.Brand";v="99", "Chromium";v="124", "Google Chrome";v="124"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin'
    }

    clean_base = url.split('?')[0].rstrip('/')

    # Try .js endpoint first
    endpoints = [clean_base + '.js', clean_base + '.json']
    for ep in endpoints:
        try:
            req = urllib.request.Request(ep, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                encoding = response.headers.get_param('charset') or 'utf-8'
                if response.info().get('Content-Encoding') == 'gzip':
                    import gzip
                    raw_text = gzip.decompress(response.read()).decode(encoding, errors='replace')
                else:
                    raw_text = response.read().decode(encoding, errors='replace')
                
                data = json.loads(raw_text)
                if isinstance(data, dict):
                    if 'product' in data and isinstance(data['product'], dict):
                        return data['product']
                    if 'title' in data and ('variants' in data or 'price' in data):
                        return data
        except Exception:
            continue

    return None

def fetch_shopify_html_http(url: str, timeout: int = 12) -> str:
    """
    Fetches raw HTML from Shopify product page using HTTP request.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate',
        'Sec-Ch-Ua': '"Not-A.Brand";v="99", "Chromium";v="124", "Google Chrome";v="124"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Upgrade-Insecure-Requests': '1'
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
        raise ShopifyFetcherError(f"Shopify HTTP fetch failed for {url}: {e}")

def fetch_shopify_page_playwright(url: str, timeout: int = 30000) -> Dict[str, Any]:
    """
    Playwright stealth headless browser fetcher for Shopify stores protected by Cloudflare.
    """
    try:
        ensure_playwright_event_loop_policy()
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = None
            for channel in ["chrome", "msedge", None]:
                try:
                    kwargs = {
                        "headless": True,
                        "args": [
                            '--disable-blink-features=AutomationControlled',
                            '--no-sandbox',
                            '--disable-dev-shm-usage'
                        ]
                    }
                    if channel:
                        kwargs["channel"] = channel
                    browser = p.chromium.launch(**kwargs)
                    if browser:
                        break
                except Exception:
                    continue
                    
            if not browser:
                browser = p.chromium.launch(headless=True)
                
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                locale='en-US',
                extra_http_headers={
                    'Accept-Language': 'en-US,en;q=0.9',
                    'sec-ch-ua': '"Not-A.Brand";v="99", "Chromium";v="124", "Google Chrome";v="124"',
                    'sec-ch-ua-mobile': '?0',
                    'sec-ch-ua-platform': '"Windows"'
                }
            )
            page = context.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            intercepted_product = None

            def handle_response(response):
                nonlocal intercepted_product
                r_url = response.url
                try:
                    ct = response.headers.get("content-type", "")
                    if "json" in ct and ("/products/" in r_url and (".js" in r_url or ".json" in r_url)):
                        data = response.json()
                        if isinstance(data, dict):
                            intercepted_product = data.get("product") or data
                except Exception:
                    pass

            page.on("response", handle_response)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            page.wait_for_timeout(3000)
            
            html = page.content()
            browser.close()
            return {"html": html, "json_data": intercepted_product}
    except Exception as e:
        raise ShopifyFetcherError(f"Playwright fetch failed for Shopify URL {url}: {e!r}")

def parse_shopify_price(val: Any) -> float:
    """
    Parses numeric price values from Shopify JSON structures.
    In .js endpoints, prices are multiplied by 100 (e.g. 59900 = ₹ 599.00).
    In .json endpoints or HTML, prices can be floats or strings (e.g. "599.00").
    """
    if val is None:
        return 0.0
    if isinstance(val, dict):
        val = val.get("amount") or val.get("value") or val.get("price") or 0.0
    try:
        raw = float(val)
        # If integer-like > 10000 and has no decimal part, it represents cents
        if raw >= 10000 and raw == int(raw):
            return round(raw / 100.0, 2)
        return round(raw, 2)
    except (ValueError, TypeError):
        return 0.0


class ShopifyScraper:
    """
    Universal Shopify E-Commerce Product Scraper (xyxxcrew.com, snitch.co.in, blissclub.com, etc.).
    Extracts high-resolution images, variants, specifications, stock availability, pricing, and ratings.
    """

    @classmethod
    def probe_is_shopify(cls, url: str, timeout: int = 5) -> bool:
        """
        Dynamically probes an unknown URL to determine if it is powered by Shopify.
        Checks:
        1. Direct response from /products/<handle>.js or <url>.js endpoint
        2. HTML signatures: cdn.shopify.com, window.Shopify, Shopify.theme, ShopifyAnalytics, etc.
        """
        if not url or not url.startswith("http"):
            return False

        clean_url = url.split('?')[0].rstrip('/')

        # 1. Quick probe to .js endpoint
        js_url = clean_url + '.js'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/javascript, */*; q=0.01'
        }
        try:
            req = urllib.request.Request(js_url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if isinstance(data, dict) and ('title' in data or 'product' in data or 'variants' in data):
                    return True
        except Exception:
            pass

        # 2. Probe HTML for Shopify digital fingerprints
        try:
            html_req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(html_req, timeout=timeout) as resp:
                content = resp.read().decode('utf-8', errors='ignore')
                shopify_fingerprints = [
                    'cdn.shopify.com', 'window.Shopify', 'Shopify.theme',
                    'ShopifyAnalytics', 'shopify-payment-button', 'myshopify.com'
                ]
                if any(fp in content for fp in shopify_fingerprints):
                    return True
        except Exception:
            pass

        return False

    @classmethod
    def scrape_url(cls, url_or_handle: str) -> ProductData:
        """
        Scrapes a Shopify product page given a URL or product handle.
        """
        canonical_url = normalize_shopify_url(url_or_handle)
        handle = extract_shopify_handle(canonical_url) or "shopify-product"

        # 1. Fast HTTP fetch for .js / .json endpoint
        js_data = fetch_shopify_json_http(canonical_url)
        html = ""

        # 2. Fast HTTP fetch for HTML (for JSON-LD, specs, and reviews)
        try:
            html = fetch_shopify_html_http(canonical_url)
        except Exception:
            pass

        # 3. If both HTTP fetches failed or blocked, fall back to Playwright
        if not js_data and (not html or len(html) < 2000 or "Access Denied" in html or "cf-browser-verification" in html):
            pw_res = fetch_shopify_page_playwright(canonical_url)
            html = pw_res.get("html") or ""
            js_data = pw_res.get("json_data") or js_data

        return cls.parse_product(js_data or {}, html, canonical_url, handle)

    @classmethod
    def parse_product(
        cls,
        js_data: Dict[str, Any],
        html: str,
        canonical_url: str,
        handle: str
    ) -> ProductData:
        """
        Parses Shopify product data from JSON payload and HTML page.
        """
        soup = BeautifulSoup(html, 'html.parser') if html else None

        # 1. Extract JSON-LD Schema if present
        json_ld: Dict[str, Any] = {}
        if soup:
            for script in soup.find_all('script', type='application/ld+json'):
                if script.string:
                    try:
                        data = json.loads(script.string)
                        if isinstance(data, dict) and data.get('@type') == 'Product':
                            json_ld = data
                            break
                        elif isinstance(data, list):
                            for item in data:
                                if isinstance(item, dict) and item.get('@type') == 'Product':
                                    json_ld = item
                                    break
                    except Exception:
                        pass

        # 2. Identity & Title
        style_id = str(js_data.get('id') or handle)
        title = js_data.get('title') or json_ld.get('name') or ""
        if not title and soup:
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'):
                title = og_title['content'].split('|')[0].strip()
            elif soup.title and soup.title.string:
                title = soup.title.string.split('|')[0].strip()

        if not title:
            words = handle.replace('-', ' ').title().split()
            title = " ".join(words) if words else f"Shopify Product {style_id}"

        # 3. Brand / Vendor
        brand_name = js_data.get('vendor') or ""
        if not brand_name and json_ld.get('brand'):
            b = json_ld['brand']
            brand_name = b.get('name', '') if isinstance(b, dict) else str(b)
        if not brand_name and canonical_url:
            # Fallback to domain name (e.g. xyxxcrew.com -> XYXX Crew)
            m_dom = re.search(r'https?://(?:www\.)?([^/]+)', canonical_url)
            if m_dom:
                dom = m_dom.group(1).split('.')[0]
                brand_name = dom.upper()
        if not brand_name:
            brand_name = "Shopify Brand"

        brand_info = BrandInfo(name=brand_name)

        # 4. Pricing
        raw_price = js_data.get('price')
        raw_mrp = js_data.get('compare_at_price')

        selling_price = parse_shopify_price(raw_price)
        mrp = parse_shopify_price(raw_mrp)

        # Check JSON-LD offers if prices not found
        if selling_price == 0.0 and json_ld.get('offers'):
            offers = json_ld['offers']
            if isinstance(offers, list) and offers:
                selling_price = parse_shopify_price(offers[0].get('price'))
            elif isinstance(offers, dict):
                selling_price = parse_shopify_price(offers.get('price'))

        # Check variants for lowest price
        variants = js_data.get('variants') or []
        if selling_price == 0.0 and variants:
            v_prices = [parse_shopify_price(v.get('price')) for v in variants if parse_shopify_price(v.get('price')) > 0]
            if v_prices:
                selling_price = min(v_prices)
            v_mrps = [parse_shopify_price(v.get('compare_at_price')) for v in variants if parse_shopify_price(v.get('compare_at_price')) > 0]
            if v_mrps:
                mrp = max(v_mrps)

        if mrp <= 0.0 or mrp < selling_price:
            mrp = selling_price

        discount_amount = max(0.0, mrp - selling_price)
        discount_pct = int(round((discount_amount / mrp) * 100)) if mrp > 0 else 0

        # Currency code extraction
        currency = "INR"
        if json_ld.get('offers'):
            offers = json_ld['offers']
            if isinstance(offers, list) and offers:
                currency = offers[0].get('priceCurrency') or "INR"
            elif isinstance(offers, dict):
                currency = offers.get('priceCurrency') or "INR"

        price_info = PriceInfo(
            mrp=mrp,
            selling_price=selling_price,
            discount_amount=discount_amount,
            discount_percentage=discount_pct,
            currency=currency
        )

        # 5. Media & High-Resolution Images
        hd_urls: List[str] = []
        media_list: List[ImageMedia] = []
        seen_image_keys: set = set()

        raw_images = js_data.get('images') or []
        for idx, img_src in enumerate(raw_images):
            if isinstance(img_src, dict):
                img_src = img_src.get('src') or img_src.get('url') or ''
            if img_src and isinstance(img_src, str):
                hd_u = transform_to_hd_image_url(img_src, width=2048)
                clean_key = hd_u.split('?')[0].lower()
                if hd_u and clean_key not in seen_image_keys:
                    seen_image_keys.add(clean_key)
                    hd_urls.append(hd_u)
                    media_list.append(ImageMedia(
                        image_id=str(idx + 1),
                        view_type="default",
                        raw_url=img_src,
                        hd_url=hd_u
                    ))

        # Fallback to JSON-LD images if none extracted
        if not hd_urls and json_ld.get('image'):
            ld_imgs = json_ld['image']
            if isinstance(ld_imgs, str):
                ld_imgs = [ld_imgs]
            for idx, img_src in enumerate(ld_imgs):
                if isinstance(img_src, str):
                    hd_u = transform_to_hd_image_url(img_src, width=2048)
                    clean_key = hd_u.split('?')[0].lower()
                    if hd_u and clean_key not in seen_image_keys:
                        seen_image_keys.add(clean_key)
                        hd_urls.append(hd_u)
                        media_list.append(ImageMedia(
                            image_id=str(idx + 1),
                            view_type="default",
                            raw_url=img_src,
                            hd_url=hd_u
                        ))

        # Fallback to DOM og:image
        if not hd_urls and soup:
            for meta_img in soup.find_all('meta', property='og:image'):
                src = meta_img.get('content')
                if src:
                    hd_u = transform_to_hd_image_url(src, width=2048)
                    clean_key = hd_u.split('?')[0].lower()
                    if hd_u and clean_key not in seen_image_keys:
                        seen_image_keys.add(clean_key)
                        hd_urls.append(hd_u)
                        media_list.append(ImageMedia(
                            image_id=str(len(hd_urls) + 1),
                            view_type="default",
                            raw_url=src,
                            hd_url=hd_u
                        ))

        # 6. Sizes & Variants
        sizes_list: List[SizeOption] = []
        color_variants: List[ColorVariant] = []

        options = js_data.get('options') or []
        color_option_pos = None
        size_option_pos = None

        for idx, opt in enumerate(options):
            opt_name = str(opt.get('name') if isinstance(opt, dict) else opt).lower()
            if 'color' in opt_name or 'colour' in opt_name or 'shade' in opt_name:
                color_option_pos = idx + 1
            elif 'size' in opt_name or 'fit' in opt_name or 'waist' in opt_name or 'chest' in opt_name:
                size_option_pos = idx + 1

        seen_size_labels = set()
        seen_color_names = set()

        for v in variants:
            v_id = str(v.get('id') or '')
            v_title = str(v.get('title') or '').strip()
            v_sku = str(v.get('sku') or '')
            v_avail = bool(v.get('available', True))

            # Extract size label
            size_label = ""
            if size_option_pos:
                size_label = str(v.get(f'option{size_option_pos}') or '').strip()
            if not size_label and v_title and v_title.lower() != 'default title':
                size_label = v_title

            if size_label and size_label not in seen_size_labels:
                seen_size_labels.add(size_label)
                sizes_list.append(SizeOption(
                    label=size_label,
                    sku_id=v_sku or v_id,
                    available=v_avail,
                    inventory=10 if v_avail else 0
                ))

            # Extract color variant
            color_name = ""
            if color_option_pos:
                color_name = str(v.get(f'option{color_option_pos}') or '').strip()
            
            if color_name and color_name not in seen_color_names:
                seen_color_names.add(color_name)
                v_img = v.get('featured_image', {})
                v_thumb = ""
                if isinstance(v_img, dict) and v_img.get('src'):
                    v_thumb = transform_to_hd_image_url(v_img['src'], width=300)
                elif hd_urls:
                    v_thumb = hd_urls[0]

                v_url = f"{canonical_url}?variant={v_id}" if v_id else canonical_url
                color_variants.append(ColorVariant(
                    style_id=v_id or color_name,
                    color_name=color_name,
                    image_thumb=v_thumb,
                    product_url=v_url
                ))

        # 7. Stock Availability
        in_stock = any(s.available for s in sizes_list) if sizes_list else bool(js_data.get('available', True))
        if json_ld.get('offers'):
            offers = json_ld['offers']
            if isinstance(offers, dict) and offers.get('availability') == 'https://schema.org/OutOfStock':
                in_stock = False
            elif isinstance(offers, list) and offers and all(o.get('availability') == 'https://schema.org/OutOfStock' for o in offers):
                in_stock = False

        # 8. Description & Specifications
        desc_sections: List[Dict[str, str]] = []
        specs_list: List[Specification] = []
        seen_spec_keys: set = set()

        raw_desc_html = js_data.get('description') or js_data.get('body_html') or json_ld.get('description') or ""
        if raw_desc_html:
            desc_soup = BeautifulSoup(raw_desc_html, 'html.parser')
            
            # Extract bullet list specifications from description
            for li in desc_soup.find_all('li'):
                li_text = li.get_text().strip()
                if ':' in li_text and len(li_text) < 120:
                    k, v = li_text.split(':', 1)
                    k_clean, v_clean = k.strip(), v.strip()
                    if k_clean and v_clean and k_clean.lower() not in seen_spec_keys:
                        seen_spec_keys.add(k_clean.lower())
                        specs_list.append(Specification(title=k_clean, value=v_clean))

            # Extract table specifications from description
            for tr in desc_soup.find_all('tr'):
                tds = tr.find_all(['td', 'th'])
                if len(tds) >= 2:
                    k_clean = tds[0].get_text().strip()
                    v_clean = tds[1].get_text().strip()
                    if k_clean and v_clean and len(k_clean) < 40 and len(v_clean) < 150:
                        if k_clean.lower() not in seen_spec_keys:
                            seen_spec_keys.add(k_clean.lower())
                            specs_list.append(Specification(title=k_clean, value=v_clean))

            clean_desc = desc_soup.get_text(separator='\n').strip()
            # Clean up multi-newlines
            clean_desc = re.sub(r'\n{3,}', '\n\n', clean_desc)
            if clean_desc:
                desc_sections.append({
                    'title': 'Description',
                    'description': clean_desc
                })

        # Parse tags for structured attributes (e.g. Fabric_Cotton, Pattern_Solids, Pack_Single)
        tags = js_data.get('tags') or []
        for tag in tags:
            if isinstance(tag, str) and '_' in tag:
                parts = tag.split('_', 1)
                t_key = parts[0].strip().title()
                t_val = parts[1].strip()
                if t_key and t_val and not t_key.startswith('Label') and len(t_key) < 30 and len(t_val) < 50:
                    if t_key.lower() not in seen_spec_keys:
                        seen_spec_keys.add(t_key.lower())
                        specs_list.append(Specification(title=t_key, value=t_val))

        # 9. Category, Breadcrumbs & Gender
        breadcrumbs: List[str] = []
        product_type = js_data.get('type') or ""
        if product_type:
            breadcrumbs.append(product_type)

        category = product_type or (breadcrumbs[-1] if breadcrumbs else "Apparel")

        # Gender inference
        gender = "Unisex"
        all_text = f"{title} {category} {' '.join(tags)}".lower()
        if any(w in all_text for w in ['men', "men's", 'mens', 'male', 'trunks', 'boxers', 'briefs']):
            gender = "Men"
        elif any(w in all_text for w in ['women', "women's", 'womens', 'female', 'bra', 'leggings', 'dress', 'saree']):
            gender = "Women"
        elif any(w in all_text for w in ['kids', 'boys', 'girls', 'child', 'baby']):
            gender = "Kids"

        # 10. Ratings & Reviews
        avg_rating = 0.0
        total_ratings = 0
        total_reviews = 0

        if json_ld.get('aggregateRating'):
            agg = json_ld['aggregateRating']
            try:
                avg_rating = float(agg.get('ratingValue') or 0.0)
                total_ratings = int(agg.get('ratingCount') or agg.get('reviewCount') or 0)
                total_reviews = int(agg.get('reviewCount') or total_ratings)
            except (ValueError, TypeError):
                pass

        rating_summary = RatingSummary(
            average_rating=round(avg_rating, 2),
            total_ratings_count=total_ratings,
            total_reviews_count=total_reviews
        )

        # 11. Seller & Service Flags
        seller_info = SellerInfo(
            seller_name=brand_name or "Official Brand Store",
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

        return ProductData(
            platform="shopify",
            style_id=style_id,
            canonical_url=canonical_url,
            product_url=canonical_url,
            title=title,
            subtitle=desc_sections[0]['description'][:200] if desc_sections else None,
            brand=brand_info,
            price=price_info,
            media=media_list,
            hd_image_urls=hd_urls,
            sizes=sizes_list,
            in_stock=in_stock,
            color_variants=color_variants,
            specifications=specs_list,
            product_description_sections=desc_sections,
            breadcrumbs=breadcrumbs,
            category=category,
            gender=gender,
            ratings=rating_summary,
            reviews=[],
            seller=seller_info,
            flags=service_flags,
            scraped_at=datetime.utcnow().isoformat() + "Z"
        )
