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
from .url_utils import normalize_pantaloons_url, extract_pantaloons_id
from .image_hd import transform_to_hd_image_url
from .loop_policy import ensure_playwright_event_loop_policy

class PantaloonsFetcherError(Exception):
    pass

def fetch_pantaloons_html_http(url: str, timeout: int = 12) -> str:
    """
    Fetches raw HTML from Pantaloons using fast HTTP request with anti-bot Chrome headers.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Cache-Control': 'no-cache',
        'Sec-Ch-Ua': '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
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
        raise PantaloonsFetcherError(f"HTTP fetch failed for {url}: {e}")

def fetch_pantaloons_page_playwright(url: str, timeout: int = 30000) -> Dict[str, Any]:
    """
    Playwright headless browser fetcher for Pantaloons.
    Captures HTML and intercepts getProductPricenSize API data.
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
            
            api_data = {}

            def handle_response(response):
                if "getProductPricenSize" in response.url:
                    try:
                        api_data["pricing"] = response.json()
                    except Exception:
                        pass

            page.on("response", handle_response)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            page.wait_for_timeout(2500)
            
            html = page.content()
            browser.close()
            return {"html": html, "api_data": api_data}
    except Exception as e:
        raise PantaloonsFetcherError(f"Playwright fetch failed for Pantaloons URL {url}: {e!r}")

def extract_brand_name(title: str, breadcrumbs: List[str], soup: BeautifulSoup) -> str:
    """
    Extracts product brand name from title, breadcrumbs, or DOM elements.
    """
    known_brands = [
        "Spykar", "Levis", "Levi's", "Pepe Jeans", "Byford", "Bare Denim",
        "People", "Urban Ranger", "Ajile", "Akkriti", "Rangmanch", "SF Jeans",
        "Trishaa", "Honey", "Annabelle", "Candie's", "Allen Solly", "Louis Philippe",
        "Van Heusen", "Peter England", "W", "Aurelia", "Biba", "Gini & Jony", "Doodle"
    ]
    
    # Check title
    for b in known_brands:
        if re.search(rf'\b{re.escape(b)}\b', title, re.IGNORECASE):
            return b
            
    # Check breadcrumbs
    for bc in breadcrumbs:
        for b in known_brands:
            if re.search(rf'\b{re.escape(b)}\b', bc, re.IGNORECASE):
                return b
                
    # Search DOM text snippets
    full_text = soup.get_text(" ", strip=True)
    for b in known_brands:
        if re.search(rf'\b{re.escape(b)}\b', full_text, re.IGNORECASE):
            return b
            
    return "Pantaloons Official"

class PantaloonsScraper:
    """
    Production-grade Pantaloons Product Scraper (pantaloons.com).
    """

    @classmethod
    def scrape_url(cls, url_or_id: str) -> ProductData:
        """
        Scrapes a product page on Pantaloons given a URL or Product ID.
        """
        product_id = extract_pantaloons_id(url_or_id) or "0"
        canonical_url = normalize_pantaloons_url(url_or_id)
        
        # Fast HTTP fetch first
        try:
            html = fetch_pantaloons_html_http(canonical_url)
            if "application/ld+json" in html and ("Product" in html or "schema.org" in html):
                # Try Playwright to get complete pricing & inventory if needed
                try:
                    pw_res = fetch_pantaloons_page_playwright(canonical_url)
                    return cls.parse_product(pw_res["html"], canonical_url, product_id, pw_res.get("api_data"))
                except Exception as pw_err:
                    print(f"Playwright fetch fallback warning: {pw_err}. Parsing from HTTP HTML...")
                    return cls.parse_product(html, canonical_url, product_id)
        except Exception as e:
            print(f"HTTP fetch warning: {e}. Falling back to Playwright headless...")
            
        pw_res = fetch_pantaloons_page_playwright(canonical_url)
        return cls.parse_product(pw_res["html"], canonical_url, product_id, pw_res.get("api_data"))

    @classmethod
    def parse_product(
        cls,
        html: str,
        canonical_url: str,
        default_id: str,
        api_data: Optional[Dict[str, Any]] = None
    ) -> ProductData:
        """
        Parses Pantaloons product page HTML, JSON-LD schema, and API data into ProductData domain model.
        """
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. Parse JSON-LD scripts
        prod_ld = {}
        breadcrumb_ld = []
        
        for script in soup.find_all('script', type='application/ld+json'):
            if not script.string:
                continue
            try:
                data = json.loads(script.string)
                if isinstance(data, dict):
                    t = data.get('@type')
                    if t == 'Product':
                        prod_ld = data
                    elif t == 'BreadcrumbList':
                        breadcrumb_ld = data.get('itemListElement', [])
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            t = item.get('@type')
                            if t == 'Product':
                                prod_ld = item
                            elif t == 'BreadcrumbList':
                                breadcrumb_ld = item.get('itemListElement', [])
            except Exception:
                pass

        # Product Title
        raw_name = prod_ld.get('name') or ""
        if not raw_name:
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'):
                raw_name = og_title['content']
            elif soup.title and soup.title.string:
                raw_name = soup.title.string.split('|')[0].strip()
                
        title = raw_name or f"Pantaloons Product {default_id}"

        # Breadcrumbs & Category & Gender
        cat_tree: List[str] = []
        for b_item in breadcrumb_ld:
            if isinstance(b_item, dict) and b_item.get('name'):
                b_name = b_item['name'].strip()
                if b_name and b_name.lower() != 'home' and b_name not in cat_tree:
                    cat_tree.append(b_name)
                    
        category = cat_tree[-2] if len(cat_tree) >= 2 else (cat_tree[-1] if cat_tree else "Apparel")
        
        gender = "Unisex"
        full_path_str = " ".join(cat_tree).lower() + " " + title.lower()
        if "women" in full_path_str or "girls" in full_path_str or "female" in full_path_str:
            gender = "Women"
        elif "men" in full_path_str or "boys" in full_path_str or "male" in full_path_str:
            gender = "Men"
        elif "kids" in full_path_str or "baby" in full_path_str:
            gender = "Kids"

        # Brand
        brand_name = extract_brand_name(title, cat_tree, soup)
        brand_info = BrandInfo(name=brand_name)

        # 2. Pricing & Size Inventory
        mrp = 0.0
        selling_price = 0.0
        sizes_list: List[SizeOption] = []
        api_hits = []
        
        if api_data and "pricing" in api_data:
            results = api_data["pricing"].get("results", {})
            api_hits = results.get("body", {}).get("hits", {}).get("hits", [])
            
        if api_hits:
            source = api_hits[0].get("_source", {})
            mrp = float(source.get("Price") or 0.0)
            selling_price = float(source.get("SellingPrice") or mrp)
            
            raw_sizes = source.get("Sizes") or []
            for s in raw_sizes:
                s_name = str(s.get("Name") or "").strip()
                qty = int(s.get("Quantity") or 0)
                s_price = float(s.get("SellingPrice") or selling_price)
                s_mrp = float(s.get("Price") or mrp)
                s_id = str(s.get("ID") or "")
                
                if s_name:
                    sizes_list.append(SizeOption(
                        label=s_name,
                        sku_id=s_id,
                        available=(qty > 0),
                        inventory=qty
                    ))
        else:
            offers = prod_ld.get('offers') or {}
            price_val = float(offers.get('price') or 0.0)
            mrp = price_val
            selling_price = price_val

        if mrp <= 0 and selling_price > 0:
            mrp = selling_price
        elif selling_price <= 0 and mrp > 0:
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

        # 3. Images & Media
        media_list: List[ImageMedia] = []
        hd_urls: List[str] = []

        ld_images = prod_ld.get('image') or []
        if isinstance(ld_images, str):
            ld_images = [ld_images]

        for idx, img_src in enumerate(ld_images):
            if img_src and isinstance(img_src, str):
                hd_url = transform_to_hd_image_url(img_src, width=1400)
                media_list.append(ImageMedia(
                    image_id=str(idx + 1),
                    view_type="default",
                    raw_url=img_src,
                    hd_url=hd_url
                ))
                if hd_url not in hd_urls:
                    hd_urls.append(hd_url)

        # Fallback to og:image meta tags if no ld+json images
        if not hd_urls:
            for meta_img in soup.find_all('meta', property='og:image'):
                src = meta_img.get('content')
                if src:
                    hd_url = transform_to_hd_image_url(src, width=1400)
                    if hd_url not in hd_urls:
                        hd_urls.append(hd_url)
                        media_list.append(ImageMedia(raw_url=src, hd_url=hd_url))

        # 4. In Stock & Service Flags
        in_stock = any(s.available for s in sizes_list) if sizes_list else True
        if prod_ld.get('offers', {}).get('availability') == 'https://schema.org/OutOfStock':
            in_stock = False

        # 5. Specifications & Description
        specs_list: List[Specification] = []
        
        color_val = prod_ld.get('color')
        if color_val:
            specs_list.append(Specification(title="Color", value=str(color_val)))
            
        mat_val = prod_ld.get('material')
        if mat_val:
            specs_list.append(Specification(title="Material", value=str(mat_val)))

        desc_sections: List[Dict[str, str]] = []
        ld_desc = prod_ld.get('description') or ""
        if ld_desc:
            clean_desc = BeautifulSoup(ld_desc, 'html.parser').get_text().strip()
            if clean_desc:
                desc_sections.append({
                    'title': 'Product Details',
                    'description': clean_desc
                })

        # 6. Color Variants Extraction
        color_variants: List[ColorVariant] = []
        seen_color_ids = set()

        sel_color = soup.find(string=re.compile(r'SELECT COLOR', re.IGNORECASE))
        if sel_color:
            parent = sel_color.parent
            for _ in range(6):
                if parent.parent:
                    parent = parent.parent

            for img in parent.find_all('img'):
                src = img.get('src') or img.get('data-src') or ''
                alt = img.get('alt') or img.get('title') or ''
                
                if src and ('pantaloons.com/img/app/product/' in src or 'w=50' in src or 'w=450' in src):
                    m_pid = re.search(r'/product/\d+/(\d+)-\d+\.jpg', src)
                    v_pid = m_pid.group(1) if m_pid else default_id
                    
                    c_name = alt.strip() or f"Color {v_pid}"
                    if v_pid not in seen_color_ids:
                        seen_color_ids.add(v_pid)
                        hd_thumb = transform_to_hd_image_url(src, width=300)
                        clean_slug = re.sub(r'-\d+\.html$', '', canonical_url.split('/')[-1]) if '/p/' in canonical_url else 'product'
                        v_url = f"https://www.pantaloons.com/p/{c_name.lower().replace(' ', '-')}-{clean_slug}-{v_pid}.html" if v_pid != default_id else canonical_url
                        
                        color_variants.append(ColorVariant(
                            style_id=v_pid,
                            color_name=c_name.title(),
                            image_thumb=hd_thumb,
                            product_url=v_url
                        ))

        if not color_variants:
            for img in soup.find_all('img'):
                src = img.get('src') or ''
                alt = img.get('alt') or ''
                if 'w=50' in src and '/product/' in src:
                    m_pid = re.search(r'/product/\d+/(\d+)-\d+\.jpg', src)
                    if m_pid:
                        v_pid = m_pid.group(1)
                        if v_pid not in seen_color_ids:
                            seen_color_ids.add(v_pid)
                            color_variants.append(ColorVariant(
                                style_id=v_pid,
                                color_name=alt.title() if alt else f"Color {v_pid}",
                                image_thumb=transform_to_hd_image_url(src, width=300),
                                product_url=f"https://www.pantaloons.com/p/product-{v_pid}.html"
                            ))

        # 7. Seller Info & Serviceability
        seller_info = SellerInfo(
            seller_name="Pantaloons Official",
            seller_rating=4.7,
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
            platform="pantaloons",
            style_id=default_id,
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
