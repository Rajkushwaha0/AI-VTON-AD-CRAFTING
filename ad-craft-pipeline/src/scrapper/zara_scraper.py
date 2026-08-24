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
from .url_utils import normalize_zara_url, extract_zara_id
from .image_hd import transform_to_hd_image_url
from .loop_policy import ensure_playwright_event_loop_policy

class ZaraFetcherError(Exception):
    pass

def fetch_zara_html_http(url: str, timeout: int = 12) -> str:
    """
    Fetches raw HTML from Zara using HTTP request with anti-bot Chrome headers.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Sec-Ch-Ua': '"Not-A.Brand";v="99", "Chromium";v="124", "Google Chrome";v="124"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
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
        raise ZaraFetcherError(f"HTTP fetch failed for {url}: {e}")

def fetch_zara_page_playwright(url: str, timeout: int = 30000) -> Dict[str, Any]:
    """
    Playwright stealth headless browser fetcher for Zara.
    Bypasses Akamai interstitial challenges and intercepts window.zara state / REST API data.
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
                timezone_id='Asia/Kolkata',
                extra_http_headers={
                    'Accept-Language': 'en-US,en;q=0.9',
                    'sec-ch-ua': '"Not-A.Brand";v="99", "Chromium";v="124", "Google Chrome";v="124"',
                    'sec-ch-ua-mobile': '?0',
                    'sec-ch-ua-platform': '"Windows"'
                }
            )
            page = context.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            api_data = {}

            def handle_response(response):
                r_url = response.url
                try:
                    ct = response.headers.get("content-type", "")
                    if "json" in ct and ("zara" in r_url or "itx" in r_url or "product" in r_url or "catalog" in r_url):
                        api_data[r_url] = response.json()
                except Exception:
                    pass

            page.on("response", handle_response)
            
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            page.wait_for_timeout(5000)
            
            html = page.content()
            browser.close()
            return {"html": html, "api_data": api_data}
    except Exception as e:
        raise ZaraFetcherError(f"Playwright fetch failed for Zara URL {url}: {e!r}")

def parse_price_value(val: Any) -> float:
    """
    Parses numeric price values from Zara JSON structures.
    Zara represents prices multiplied by 100 (e.g. 395000 = ₹ 3,950.00).
    """
    if val is None:
        return 0.0
    if isinstance(val, dict):
        val = val.get("amount") or val.get("value") or val.get("price") or 0.0
    try:
        raw = float(val)
        if raw > 10000: # Standard Zara 100x pricing factor
            return round(raw / 100.0, 2)
        return round(raw, 2)
    except (ValueError, TypeError):
        return 0.0

def get_zara_image_key(img_url: str) -> str:
    """
    Extracts canonical image identifier key from Zara asset URL for strict deduplication.
    Example:
    'https://static.zara.net/assets/public/4d9b/c0bf/45334941801a/8323df18f182/T9687476024-p/T9687476024-p.jpg?ts=1785939069047'
    -> 't9687476024-p'
    """
    clean_url = img_url.split('?')[0]
    m = re.search(r'([0-9a-zA-Z]{8,14}-[a-z0-9]+)(?:\.[a-z]+)?$', clean_url, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    parts = clean_url.rstrip('/').split('/')
    if parts:
        last = parts[-1]
        name, _ = os.path.splitext(last)
        return name.lower()
    return clean_url.lower()

def extract_colors_from_product_node(p_node: Dict[str, Any]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """
    Recursively extracts all color objects across main product and any bundle / sub-products.
    Returns list of (product_node, color_object) tuples.
    """
    results = []
    
    def walk_node(node):
        if not isinstance(node, dict):
            return
        p_detail = node.get("detail") or {}
        p_colors = p_detail.get("colors") or []
        for c in p_colors:
            if isinstance(c, dict):
                results.append((node, c))
                # Check color-level bundle products
                b_prods = c.get("bundleProducts") or c.get("relatedProducts") or c.get("components") or []
                for bp in b_prods:
                    walk_node(bp)
        # Check product-level bundle products / components
        p_b_prods = node.get("bundleProducts") or p_detail.get("relatedProducts") or node.get("components") or []
        for bp in p_b_prods:
            walk_node(bp)

    walk_node(p_node)
    return results

class ZaraScraper:
    """
    Production-grade Zara Product Scraper (zara.com).
    Supports standard products, multi-color variants, and Bundle / Total-Look product pages (-pt...html).
    """

    @classmethod
    def scrape_url(cls, url_or_id: str) -> ProductData:
        """
        Scrapes a Zara product page given a URL or Product ID.
        """
        zara_id = extract_zara_id(url_or_id) or "0"
        canonical_url = normalize_zara_url(url_or_id)
        
        # Fast HTTP fetch attempt first
        try:
            html = fetch_zara_html_http(canonical_url)
            if ("window.zara.viewPayload" in html or "application/ld+json" in html) and "Access Denied" not in html and len(html) > 5000:
                return cls.parse_product(html, canonical_url, zara_id)
        except Exception as e:
            print(f"HTTP fetch warning: {e}. Falling back to Playwright stealth browser...")
            
        pw_res = fetch_zara_page_playwright(canonical_url)
        return cls.parse_product(pw_res["html"], canonical_url, zara_id, pw_res.get("api_data"))

    @classmethod
    def parse_product(
        cls,
        html: str,
        canonical_url: str,
        default_id: str,
        api_data: Optional[Dict[str, Any]] = None
    ) -> ProductData:
        """
        Parses Zara product page HTML, window.zara.viewPayload JSON state, intercepted API responses, and JSON-LD schema.
        """
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. Extract window.zara.viewPayload state object
        view_payload: Dict[str, Any] = {}
        for script in soup.find_all('script'):
            stext = script.string or ""
            if 'window.zara.viewPayload' in stext or 'viewPayload' in stext:
                m = re.search(r'window\.zara\.viewPayload\s*=\s*({.*?});', stext, re.DOTALL)
                if m:
                    try:
                        view_payload = json.loads(m.group(1))
                        break
                    except Exception:
                        pass
                        
        # 2. Extract JSON-LD script object
        json_ld: Dict[str, Any] = {}
        for script in soup.find_all('script', type='application/ld+json'):
            if script.string:
                try:
                    data = json.loads(script.string)
                    if isinstance(data, dict) and data.get('@type') == 'Product':
                        json_ld = data
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and item.get('@type') == 'Product':
                                json_ld = item
                except Exception:
                    pass

        # 3. Product Info & Identity
        prod_obj = view_payload.get('product') or {}
        detail = prod_obj.get('detail') or {}
        
        style_id = prod_obj.get('id') or default_id or extract_zara_id(canonical_url) or "0"
        raw_title = prod_obj.get('name') or json_ld.get('name') or ""

        # Extract all colors & sub-products (for bundle/total-look products)
        color_nodes = extract_colors_from_product_node(prod_obj)

        # Build Title if main title is missing/empty (e.g. for Bundle products)
        sub_names = []
        for p_node, c_obj in color_nodes:
            p_name = p_node.get("name")
            if p_name and p_name not in sub_names and p_name != raw_title:
                sub_names.append(p_name)

        if raw_title:
            title = raw_title
        elif sub_names:
            title = " & ".join(sub_names)
        else:
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'):
                title = og_title['content'].split('|')[0].strip()
            elif soup.title and soup.title.string:
                title = soup.title.string.split('|')[0].strip()
            else:
                title = ""

        if not title or title.lower() == "access denied" or title.lower().startswith("search") or title.strip() == "| ZARA India":
            title = f"Zara Product {style_id}"

        # 4. Brand
        brand_name = "ZARA"
        brand_info = BrandInfo(name=brand_name)

        # 5. Pricing, Colors, Sizes & HD Media
        mrp = 0.0
        selling_price = 0.0
        sizes_list: List[SizeOption] = []
        color_variants: List[ColorVariant] = []
        hd_urls: List[str] = []
        media_list: List[ImageMedia] = []
        seen_image_keys: set = set()

        for p_node, c in color_nodes:
            c_name = str(c.get('name') or c.get('colorName') or "Default").strip()
            c_hex = c.get('hexCode')
            c_id = str(c.get('id') or c.get('productId') or p_node.get('id') or style_id)
            p_item_name = p_node.get('name') or ""

            # Price check
            c_price = parse_price_value(c.get('price') or p_node.get('price'))
            if c_price > 0:
                if selling_price == 0.0:
                    selling_price = c_price
                if mrp == 0.0:
                    mrp = c_price

            c_old_price = parse_price_value(c.get('oldPrice') or c.get('rawPrice'))
            if c_old_price > mrp:
                mrp = c_old_price

            # Media / Images extraction across all xmedia keys
            c_xmedia = (
                c.get('xmedia') or
                c.get('mainImgs') or
                c.get('colorSelectorMedias') or
                c.get('shopcartMedia') or
                []
            )

            thumb_url = ""
            if c_xmedia:
                first_m = c_xmedia[0]
                raw_u = first_m.get('extraInfo', {}).get('deliveryUrl') or first_m.get('url') or first_m.get('path') or ""
                if raw_u:
                    thumb_url = transform_to_hd_image_url(raw_u, width=300)

            variant_label = f"{p_item_name} ({c_name})" if (p_item_name and p_item_name != title) else c_name
            v_url = f"https://www.zara.com/in/en/product-p{style_id}.html?v1={c_id}"
            
            if not any(cv.style_id == c_id for cv in color_variants):
                color_variants.append(ColorVariant(
                    style_id=c_id,
                    color_name=variant_label,
                    color_hex=c_hex,
                    image_thumb=thumb_url,
                    product_url=v_url
                ))

            # Add HD image URLs with strict key deduplication
            for img_idx, m in enumerate(c_xmedia):
                raw_src = m.get('extraInfo', {}).get('deliveryUrl') or m.get('url') or m.get('path') or ""
                if raw_src and not raw_src.endswith(".json") and "watermark" not in raw_src:
                    hd_u = transform_to_hd_image_url(raw_src, width=2048)
                    img_key = get_zara_image_key(hd_u)
                    if hd_u and img_key not in seen_image_keys:
                        seen_image_keys.add(img_key)
                        hd_urls.append(hd_u)
                        media_list.append(ImageMedia(
                            image_id=str(m.get('name') or f"{c_id}_{img_idx+1}"),
                            view_type="default",
                            raw_url=raw_src,
                            hd_url=hd_u
                        ))

            # Sizes extraction
            c_sizes = c.get('sizes') or []
            for s in c_sizes:
                raw_sname = str(s.get('name') or s.get('description') or "").strip()
                if not raw_sname:
                    continue
                s_label = f"{p_item_name} - {raw_sname}" if (p_item_name and p_item_name != title) else raw_sname
                s_avail_str = str(s.get('availability') or "").lower()
                s_is_avail = s_avail_str in ['in_stock', 'low_on_stock', 'available', 'true'] or s.get('available', False)
                s_sku = str(s.get('sku') or s.get('id') or "")

                if not any(existing.label == s_label for existing in sizes_list):
                    sizes_list.append(SizeOption(
                        label=s_label,
                        sku_id=s_sku,
                        available=s_is_avail,
                        inventory=10 if s_is_avail else 0
                    ))

        # Check total look / bundle price fallback
        if selling_price == 0.0:
            top_price = parse_price_value(prod_obj.get('price'))
            if top_price > 0:
                selling_price = top_price
                mrp = top_price

        # Fallback to JSON-LD offers for pricing if not extracted
        if selling_price == 0.0 and json_ld.get('offers'):
            offers = json_ld['offers']
            if isinstance(offers, dict):
                selling_price = parse_price_value(offers.get('price'))
                mrp = selling_price
            elif isinstance(offers, list) and offers:
                selling_price = parse_price_value(offers[0].get('price'))
                mrp = selling_price

        # Fallback pricing from DOM meta tags
        if selling_price == 0.0:
            price_meta = soup.find('meta', property='product:price:amount') or soup.find('meta', property='og:price:amount')
            if price_meta and price_meta.get('content'):
                selling_price = parse_price_value(price_meta['content'])
                mrp = selling_price

        if mrp <= 0.0 and selling_price > 0.0:
            mrp = selling_price
        elif selling_price <= 0.0 and mrp > 0.0:
            selling_price = mrp

        discount_amount = max(0.0, mrp - selling_price)
        discount_pct = int(round((discount_amount / mrp) * 100)) if mrp > 0 else 0

        price_info = PriceInfo(
            mrp=mrp,
            selling_price=selling_price,
            discount_amount=discount_amount,
            discount_percentage=discount_pct,
            currency="INR"
        )

        # 6. Additional Image Harvesting from DOM / regex tags
        # Scan <img> elements and script tags for static.zara.net assets
        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-src') or ""
            if 'static.zara.net/assets/public/' in src and not src.endswith('.json') and "watermark" not in src:
                hd_u = transform_to_hd_image_url(src, width=2048)
                img_key = get_zara_image_key(hd_u)
                if hd_u and img_key not in seen_image_keys:
                    seen_image_keys.add(img_key)
                    hd_urls.append(hd_u)
                    media_list.append(ImageMedia(raw_url=src, hd_url=hd_u))

        # Regex scan for static.zara.net asset URLs in html text
        zara_img_matches = re.findall(r'(https://static\.zara\.net/assets/public/[a-zA-Z0-9/_.-]+\.(?:jpg|jpeg|png|webp))', html)
        for src in zara_img_matches:
            if "watermark" not in src and not src.endswith('.json'):
                hd_u = transform_to_hd_image_url(src, width=2048)
                img_key = get_zara_image_key(hd_u)
                if hd_u and img_key not in seen_image_keys:
                    seen_image_keys.add(img_key)
                    hd_urls.append(hd_u)
                    media_list.append(ImageMedia(raw_url=src, hd_url=hd_u))

        # Fallback Images from og:image meta tags
        if not hd_urls:
            for meta_img in soup.find_all('meta', property='og:image'):
                src = meta_img.get('content')
                if src:
                    hd_u = transform_to_hd_image_url(src, width=2048)
                    img_key = get_zara_image_key(hd_u)
                    if hd_u and img_key not in seen_image_keys:
                        seen_image_keys.add(img_key)
                        hd_urls.append(hd_u)
                        media_list.append(ImageMedia(raw_url=src, hd_url=hd_u))

        # 7. Stock Availability
        in_stock = any(s.available for s in sizes_list) if sizes_list else True
        if json_ld.get('offers', {}).get('availability') == 'https://schema.org/OutOfStock':
            in_stock = False

        # 8. Description, Specifications & Care
        desc_sections: List[Dict[str, str]] = []
        specs_list: List[Specification] = []

        raw_desc = prod_obj.get('description') or json_ld.get('description') or ""
        if not raw_desc:
            for p_node, c in color_nodes:
                c_desc = c.get('description') or c.get('rawDescription')
                if c_desc:
                    raw_desc = c_desc
                    break

        if raw_desc:
            clean_desc = BeautifulSoup(raw_desc, 'html.parser').get_text().strip()
            if clean_desc:
                desc_sections.append({
                    'title': 'Description',
                    'description': clean_desc
                })

        # Parse detailedComposition if present
        det_comp = detail.get('detailedComposition') or {}
        if det_comp and isinstance(det_comp, dict):
            comp_parts = det_comp.get('parts') or []
            comp_lines = []
            for part in comp_parts:
                part_desc = part.get('description') or ''
                part_comps = part.get('components') or []
                mat_strs = [f"{comp.get('percentage', '')} {comp.get('material', '')}".strip() for comp in part_comps if comp.get('material')]
                if mat_strs:
                    comp_lines.append(f"{part_desc}: {', '.join(mat_strs)}" if part_desc else ", ".join(mat_strs))
            exceptions = det_comp.get('exceptions') or []
            if exceptions:
                comp_lines.extend(exceptions)
            if comp_lines:
                comp_text = "\n".join(comp_lines)
                desc_sections.append({
                    'title': 'Composition',
                    'description': comp_text
                })
                specs_list.append(Specification(title='Composition', value=comp_text))

        extra_sections = detail.get('extraDetailSections') or []
        for sec in extra_sections:
            sec_title = sec.get('title') or 'Composition & Care'
            texts = []
            comps = sec.get('components') or []
            for comp in comps:
                if comp.get('datatype') == 'text' and comp.get('text', {}).get('value'):
                    texts.append(comp['text']['value'])
            if texts:
                full_text = "\n".join(texts)
                desc_sections.append({
                    'title': sec_title,
                    'description': full_text
                })
                specs_list.append(Specification(title=sec_title, value=full_text))

        # Add sub-product specs if available
        for p_node, c in color_nodes:
            p_name = p_node.get('name')
            p_desc = p_node.get('description')
            if p_name and p_desc:
                clean_p_desc = BeautifulSoup(p_desc, 'html.parser').get_text().strip()
                if clean_p_desc:
                    desc_sections.append({
                        'title': p_name,
                        'description': clean_p_desc
                    })

        # 9. Breadcrumbs, Category & Gender
        cat_tree: List[str] = []
        raw_bc = view_payload.get('breadCrumbs') or []
        for b in raw_bc:
            if isinstance(b, dict):
                b_name = b.get('text') or b.get('name')
                if b_name and b_name not in cat_tree:
                    cat_tree.append(str(b_name).strip())

        section_name = str(prod_obj.get('sectionName') or "").upper()
        if section_name and section_name not in cat_tree:
            cat_tree.insert(0, section_name)

        category = cat_tree[-1] if cat_tree else "Apparel"
        
        gender = "Unisex"
        path_str = " ".join(cat_tree).lower() + " " + title.lower()
        if "woman" in path_str or "women" in path_str or "female" in path_str or "lady" in path_str:
            gender = "Women"
        elif "man" in path_str or "men" in path_str or "male" in path_str:
            gender = "Men"
        elif "kid" in path_str or "child" in path_str or "boy" in path_str or "girl" in path_str or "baby" in path_str:
            gender = "Kids"

        # 10. Seller & Service Flags
        seller_info = SellerInfo(
            seller_name="Zara Official (ITX Trading)",
            seller_rating=4.8,
            return_period_days=30,
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
            platform="zara",
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
            category=category,
            gender=gender,
            ratings=RatingSummary(),
            reviews=[],
            seller=seller_info,
            flags=service_flags,
            scraped_at=datetime.utcnow().isoformat() + "Z"
        )
