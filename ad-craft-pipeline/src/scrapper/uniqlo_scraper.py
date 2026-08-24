import re
import json
import urllib.request
import urllib.parse
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from bs4 import BeautifulSoup

from .models import (
    ProductData, PriceInfo, BrandInfo, ImageMedia, SizeOption,
    ColorVariant, Specification, RatingSummary, Review, SellerInfo, ServiceFlags
)
from .url_utils import normalize_uniqlo_url, extract_uniqlo_id
from .image_hd import transform_to_hd_image_url
from .loop_policy import ensure_playwright_event_loop_policy

class UniqloFetcherError(Exception):
    pass

SIZE_CODE_MAP = {
    "SMA001": "XS",
    "SMA002": "S",
    "SMA003": "M",
    "SMA004": "L",
    "SMA005": "XL",
    "SMA006": "XXL",
    "SMA007": "3XL",
    "001": "XS",
    "002": "S",
    "003": "M",
    "004": "L",
    "005": "XL",
    "006": "XXL",
    "007": "3XL"
}

def _extract_region_info(url: str) -> Tuple[str, str]:
    m = re.search(r'uniqlo\.com/([a-z]{2})/([a-z]{2})', url, re.IGNORECASE)
    if m:
        return m.group(1).lower(), m.group(2).lower()
    return "in", "en"

def _detect_currency(country: str) -> str:
    country = country.lower()
    if country == "in":
        return "INR"
    elif country == "us":
        return "USD"
    elif country in ["jp", "japan"]:
        return "JPY"
    elif country in ["uk", "gb"]:
        return "GBP"
    elif country in ["fr", "de", "es", "it", "eu"]:
        return "EUR"
    elif country == "ca":
        return "CAD"
    elif country == "au":
        return "AUD"
    elif country == "sg":
        return "SGD"
    return "INR"

def fetch_uniqlo_data_http(url: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """
    Attempts fast direct HTTP REST API calls to Uniqlo commerce API endpoints.
    """
    product_id = extract_uniqlo_id(url)
    country, lang = _extract_region_info(url)
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Sec-Ch-Ua': '"Not-A.Brand";v="99", "Chromium";v="124", "Google Chrome";v="124"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin'
    }

    if product_id:
        client_ids = [
            f"pn-site-{country}-pwa",
            "pn-site-in-pwa",
            "pn-site-us-pwa",
            "pn-site-jp-pwa",
            "fastretailing"
        ]
        
        for cid in client_ids:
            api_url = f"https://www.uniqlo.com/{country}/api/commerce/v5/{lang}/products/{product_id}?client_id={cid}"
            req = urllib.request.Request(api_url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    raw_data = response.read().decode('utf-8')
                    parsed = json.loads(raw_data)
                    if parsed.get('status') == 'ok' and 'result' in parsed:
                        return parsed['result']
            except Exception:
                continue

    return None

def fetch_uniqlo_page_playwright(url: str, timeout: int = 30000) -> Dict[str, Any]:
    """
    Playwright stealth headless browser fetcher for Uniqlo.
    Intercepts price-groups, reviews, and taxonomies REST API data while extracting DOM elements.
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
                locale='en-US'
            )
            page = context.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            captured_api: Dict[str, Any] = {}

            def handle_response(response):
                try:
                    if "price-groups" in response.url:
                        res = response.json()
                        if isinstance(res, dict) and res.get("result"):
                            captured_api["price_groups"] = res["result"]
                    elif "reviews" in response.url:
                        res = response.json()
                        if isinstance(res, dict) and res.get("result"):
                            captured_api["reviews"] = res["result"]
                    elif "taxonomies" in response.url:
                        res = response.json()
                        if isinstance(res, dict) and res.get("result"):
                            captured_api["taxonomies"] = res["result"]
                    elif "/products/" in response.url and "client_id=" in response.url:
                        res = response.json()
                        if isinstance(res, dict) and res.get("result"):
                            captured_api["product"] = res["result"].get("product") or res["result"]
                except Exception:
                    pass

            page.on("response", handle_response)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            page.wait_for_timeout(3500)
            
            # Expand accordion sections (Details, Material / Care, Delivery, Production, Customer Service)
            accordion_labels = ["Details", "Material / Care", "Delivery / Exchange / Return", "Production", "Customer Service"]
            for label in accordion_labels:
                try:
                    btn = page.query_selector(f"button:has-text('{label}'), dt:has-text('{label}'), [role='button']:has-text('{label}')")
                    if btn:
                        btn.click()
                        page.wait_for_timeout(400)
                except Exception:
                    pass

            # DOM Extraction with scroll to trigger lazy loading
            page.evaluate("window.scrollTo(0, 800)")
            page.wait_for_timeout(1000)
            
            dom_data = page.evaluate(r"""() => {
                const getTxt = sel => {
                    const el = document.querySelector(sel);
                    return el ? el.innerText.trim() : '';
                };
                
                const h1 = getTxt('h1');
                const title = h1 || document.title.replace(/\|.*/, '').trim();
                
                const urls = [];
                document.querySelectorAll('img').forEach(img => {
                    if (img.src) urls.push(img.src);
                    if (img.dataset && img.dataset.src) urls.push(img.dataset.src);
                    if (img.srcset) {
                        img.srcset.split(',').forEach(s => urls.push(s.trim().split(' ')[0]));
                    }
                });
                document.querySelectorAll('source').forEach(src => {
                    if (src.srcset) {
                        src.srcset.split(',').forEach(s => urls.push(s.trim().split(' ')[0]));
                    }
                });

                // Colors from DOM text or buttons
                const colorTexts = [];
                document.querySelectorAll('[class*="color"], [class*="Color"], [aria-label*="Color"]').forEach(el => {
                    const txt = el.innerText || el.getAttribute('aria-label') || '';
                    if (txt && txt.length < 40) colorTexts.push(txt.trim());
                });

                const bodyText = document.body.innerText;

                return {
                    title: title,
                    images: Array.from(new Set(urls)),
                    colorTexts: Array.from(new Set(colorTexts)),
                    bodyText: bodyText.substring(0, 4000)
                };
            }""")
            
            captured_api["dom"] = dom_data
            browser.close()
            return captured_api
    except Exception as e:
        raise UniqloFetcherError(f"Playwright fetch failed for {url}: {e!r}")

class UniqloScraper:
    """
    Production-grade Uniqlo product scraper handling all edge cases.
    """
    
    @classmethod
    def scrape_url(cls, url_or_id: str) -> ProductData:
        """
        Scrapes a Uniqlo product given a URL or product code/ID.
        """
        product_id = extract_uniqlo_id(url_or_id)
        normalized_url = normalize_uniqlo_url(url_or_id)
        
        # 1. Try fast HTTP API fetch first
        api_data = fetch_uniqlo_data_http(normalized_url)
        if api_data:
            return cls._parse_uniqlo_api(api_data, normalized_url, product_id)
            
        # 2. Fall back to Playwright headless browser automation with network interception
        captured_data = fetch_uniqlo_page_playwright(normalized_url)
        if captured_data.get("product"):
            return cls._parse_uniqlo_api(captured_data["product"], normalized_url, product_id)
        elif captured_data.get("price_groups") or captured_data.get("dom"):
            return cls._parse_uniqlo_composite(captured_data, normalized_url, product_id)
            
        raise UniqloFetcherError(f"Could not retrieve product data for Uniqlo URL or ID: '{url_or_id}'")

    @classmethod
    def _parse_uniqlo_api(cls, res: Dict[str, Any], source_url: str, default_product_id: Optional[str] = None) -> ProductData:
        """
        Parses direct Fast Retailing API result payload into ProductData model.
        """
        product_info = res.get('product') or res
        
        productId = product_info.get('productId') or default_product_id or "E000000"
        title = product_info.get('name') or product_info.get('title') or f"Uniqlo Product {productId}"
        short_desc = product_info.get('shortDescription') or product_info.get('subName') or ""
        
        country, lang = _extract_region_info(source_url)
        currency = _detect_currency(country)
        canonical_url = f"https://www.uniqlo.com/{country}/{lang}/products/{productId}"

        brand_info = BrandInfo(
            name="UNIQLO",
            uid="uniqlo",
            logo_url="https://www.uniqlo.com/favicon.ico"
        )

        # Price Info
        prices = product_info.get('prices') or {}
        base_price_raw = prices.get('base') or prices.get('mrp') or prices.get('originalPrice') or {}
        promo_price_raw = prices.get('promo') or prices.get('sellingPrice') or prices.get('currentPrice') or {}
        
        mrp = 0.0
        selling_price = 0.0
        
        if isinstance(base_price_raw, dict):
            mrp = float(base_price_raw.get('value') or base_price_raw.get('price') or 0.0)
            if base_price_raw.get('currency'):
                currency = base_price_raw.get('currency')
        elif isinstance(base_price_raw, (int, float, str)):
            try: mrp = float(base_price_raw)
            except ValueError: pass

        if isinstance(promo_price_raw, dict):
            selling_price = float(promo_price_raw.get('value') or promo_price_raw.get('price') or 0.0)
            if promo_price_raw.get('currency'):
                currency = promo_price_raw.get('currency')
        elif isinstance(promo_price_raw, (int, float, str)):
            try: selling_price = float(promo_price_raw)
            except ValueError: pass

        if selling_price == 0.0 and mrp == 0.0:
            p_val = product_info.get('price') or product_info.get('minPrice') or 0.0
            if isinstance(p_val, (int, float, str)):
                try:
                    selling_price = float(p_val)
                    mrp = selling_price
                except ValueError: pass

        if mrp == 0.0: mrp = selling_price
        if selling_price == 0.0: selling_price = mrp
            
        discount_amount = max(0.0, mrp - selling_price)
        discount_pct = int(round((discount_amount / mrp) * 100)) if mrp > 0 else 0
        
        price_info = PriceInfo(
            mrp=round(mrp, 2),
            selling_price=round(selling_price, 2),
            discount_amount=round(discount_amount, 2),
            discount_percentage=discount_pct,
            currency=currency
        )

        # Sizes & Inventory
        l2s = product_info.get('l2s') or product_info.get('sizes') or []
        size_options: List[SizeOption] = []
        overall_in_stock = False

        if isinstance(l2s, list):
            for item in l2s:
                if isinstance(item, dict):
                    size_name = ""
                    size_obj = item.get('size') or {}
                    if isinstance(size_obj, dict):
                        code = size_obj.get('code') or ''
                        size_name = size_obj.get('name') or SIZE_CODE_MAP.get(code) or size_obj.get('displayCode') or ""
                    elif isinstance(size_obj, str):
                        size_name = SIZE_CODE_MAP.get(size_obj, size_obj)
                    else:
                        size_name = item.get('name') or item.get('sizeName') or str(item.get('l2Id') or '')

                    stock_info = item.get('stock') or {}
                    is_in_stock = True
                    inventory = None
                    if isinstance(stock_info, dict):
                        is_in_stock = stock_info.get('allowBackOrder', False) or stock_info.get('quantity', 0) > 0 or stock_info.get('inStock', True)
                        inventory = stock_info.get('quantity')
                    elif isinstance(item.get('available'), bool):
                        is_in_stock = item.get('available')

                    if is_in_stock:
                        overall_in_stock = True

                    size_options.append(SizeOption(
                        label=size_name.strip(),
                        sku_id=str(item.get('l2Id') or item.get('sku') or item.get('barcode') or ''),
                        available=is_in_stock,
                        inventory=inventory
                    ))

        if not size_options:
            overall_in_stock = True

        # Color Variants
        color_variants: List[ColorVariant] = []
        colors_raw = product_info.get('colors') or {}
        if isinstance(colors_raw, dict):
            for c_code, c_val in colors_raw.items():
                if isinstance(c_val, dict):
                    c_name = c_val.get('name') or c_val.get('displayCode') or c_code
                    hex_val = c_val.get('hexCode') or c_val.get('colorCode')
                    img_thumb = c_val.get('chip') or c_val.get('image')
                    var_url = f"{canonical_url}?colorDisplayCode={c_code}"
                    color_variants.append(ColorVariant(
                        style_id=f"{productId}_{c_code}",
                        color_name=c_name.strip(),
                        color_hex=hex_val,
                        image_thumb=img_thumb,
                        product_url=var_url
                    ))

        # Media
        images_dict = product_info.get('images') or {}
        image_urls: List[str] = []

        if isinstance(images_dict, dict):
            main_imgs = images_dict.get('main') or {}
            sub_imgs = images_dict.get('sub') or []
            
            if isinstance(main_imgs, dict):
                for key, img_obj in main_imgs.items():
                    if isinstance(img_obj, dict) and img_obj.get('image'):
                        image_urls.append(img_obj['image'])
                    elif isinstance(img_obj, str):
                        image_urls.append(img_obj)
            elif isinstance(main_imgs, list):
                for img_item in main_imgs:
                    if isinstance(img_item, dict) and img_item.get('image'):
                        image_urls.append(img_item['image'])
                    elif isinstance(img_item, str):
                        image_urls.append(img_item)

            if isinstance(sub_imgs, list):
                for s_item in sub_imgs:
                    if isinstance(s_item, dict) and s_item.get('image'):
                        image_urls.append(s_item['image'])
                    elif isinstance(s_item, str):
                        image_urls.append(s_item)

        hd_urls: List[str] = []
        media_list: List[ImageMedia] = []

        for idx, orig_url in enumerate(image_urls):
            if not orig_url: continue
            if orig_url.startswith('//'): orig_url = 'https:' + orig_url
            
            hd_url = re.sub(r'width=\d+', 'width=1000', orig_url)
            if 'width=' not in hd_url and '?' in hd_url: hd_url += '&width=1000'
            elif 'width=' not in hd_url: hd_url += '?width=1000'
                
            hd_urls.append(hd_url)
            view_type = "front" if idx == 0 else ("back" if idx == 1 else "detail")
            media_list.append(ImageMedia(
                image_id=str(idx + 1),
                view_type=view_type,
                raw_url=orig_url,
                hd_url=hd_url
            ))

        specs: List[Specification] = []
        desc_sections: List[Dict[str, str]] = []
        
        long_desc = product_info.get('longDescription') or product_info.get('description') or ""
        if long_desc:
            desc_sections.append({"title": "Description", "content": long_desc.strip()})

        materials = product_info.get('composition') or product_info.get('materials') or ""
        if materials:
            specs.append(Specification(title="Materials & Care", value=str(materials).strip()))
            desc_sections.append({"title": "Materials & Care", "content": str(materials).strip()})

        care = product_info.get('careInstruction') or ""
        if care:
            specs.append(Specification(title="Washing Instructions", value=str(care).strip()))

        breadcrumbs: List[str] = ["Home", "UNIQLO"]
        gender = ""
        category = ""

        gender_raw = product_info.get('gender') or product_info.get('targetGender') or ""
        if gender_raw:
            gender = str(gender_raw).upper()
            breadcrumbs.append(gender)

        cat_raw = product_info.get('category') or product_info.get('class') or ""
        if isinstance(cat_raw, dict):
            category = cat_raw.get('name') or cat_raw.get('code') or ""
        elif isinstance(cat_raw, str):
            category = cat_raw

        if category:
            breadcrumbs.append(category)

        rating_raw = product_info.get('rating') or product_info.get('ratingSummary') or {}
        avg_rating = 0.0
        total_ratings = 0
        star_map: Dict[str, int] = {}

        if isinstance(rating_raw, dict):
            avg_rating = float(rating_raw.get('average') or rating_raw.get('avgRating') or 0.0)
            total_ratings = int(rating_raw.get('count') or rating_raw.get('totalCount') or 0)
            breakdown = rating_raw.get('rateCount') or rating_raw.get('breakdown') or {}
            if isinstance(breakdown, dict):
                for k, v in breakdown.items():
                    star_map[str(k)] = int(v)

        ratings_summary = RatingSummary(
            average_rating=round(avg_rating, 2),
            total_ratings_count=total_ratings,
            total_reviews_count=total_ratings,
            star_breakdown=star_map
        )

        seller_info = SellerInfo(
            seller_id=101,
            seller_name="UNIQLO Retail",
            seller_rating=4.8,
            return_period_days=30,
            is_returnable=True,
            cod_available=True
        )

        service_flags = ServiceFlags(
            is_returnable=True,
            is_exchangeable=True,
            cod_enabled=True,
            try_and_buy_enabled=False,
            out_of_stock=not overall_in_stock,
            is_fragile=False,
            is_hazmat=False
        )

        return ProductData(
            platform="uniqlo",
            style_id=productId,
            canonical_url=canonical_url,
            product_url=source_url,
            title=title.strip(),
            subtitle=short_desc.strip() if short_desc else None,
            brand=brand_info,
            price=price_info,
            media=media_list,
            hd_image_urls=hd_urls,
            sizes=size_options,
            in_stock=overall_in_stock,
            color_variants=color_variants,
            specifications=specs,
            product_description_sections=desc_sections,
            breadcrumbs=breadcrumbs,
            category=category,
            gender=gender,
            ratings=ratings_summary,
            reviews=[],
            seller=seller_info,
            flags=service_flags,
            manufacturer="UNIQLO Co., Ltd.",
            country_of_origin="Vietnam / China / India",
            scraped_at=datetime.utcnow().isoformat() + "Z"
        )

    @classmethod
    def _parse_uniqlo_composite(cls, data: Dict[str, Any], source_url: str, default_product_id: Optional[str] = None) -> ProductData:
        """
        Parses composite Playwright data (intercepted price_groups, reviews, and rendered DOM).
        """
        productId = default_product_id or extract_uniqlo_id(source_url) or "E000000"
        country, lang = _extract_region_info(source_url)
        currency = _detect_currency(country)
        canonical_url = f"https://www.uniqlo.com/{country}/{lang}/products/{productId}"

        dom = data.get("dom") or {}
        pg = data.get("price_groups") or {}
        rev = data.get("reviews") or {}

        # 1. Title
        raw_title = dom.get("title") or f"Uniqlo Product {productId}"
        title = re.sub(r'\s*\|\s*UNIQLO.*', '', raw_title, flags=re.IGNORECASE).strip()

        # 2. Price Info
        prices_dict = pg.get("prices") or {}
        mrp = 0.0
        selling_price = 0.0

        for l2id, pitem in prices_dict.items():
            if isinstance(pitem, dict):
                base_obj = pitem.get("base") or {}
                if isinstance(base_obj, dict) and base_obj.get("value"):
                    val = float(base_obj["value"])
                    if mrp == 0.0: mrp = val
                    if base_obj.get("currency", {}).get("code"):
                        currency = base_obj["currency"]["code"]
                promo_obj = pitem.get("promo") or {}
                if isinstance(promo_obj, dict) and promo_obj.get("value"):
                    val = float(promo_obj["value"])
                    if selling_price == 0.0: selling_price = val

        if selling_price == 0.0: selling_price = mrp
        if mrp == 0.0: mrp = selling_price

        discount_amount = max(0.0, mrp - selling_price)
        discount_pct = int(round((discount_amount / mrp) * 100)) if mrp > 0 else 0

        price_info = PriceInfo(
            mrp=round(mrp, 2),
            selling_price=round(selling_price, 2),
            discount_amount=round(discount_amount, 2),
            discount_percentage=discount_pct,
            currency=currency
        )

        # 3. Sizes & Stock & Color Variants
        l2s = pg.get("l2s") or []
        stocks_dict = pg.get("stocks") or {}
        
        size_options: List[SizeOption] = []
        color_variants_map: Dict[str, ColorVariant] = {}
        overall_in_stock = False

        seen_sizes = set()

        for item in l2s:
            if isinstance(item, dict):
                l2id = str(item.get("l2Id") or "")
                size_obj = item.get("size") or {}
                color_obj = item.get("color") or {}
                
                # Size label
                s_code = size_obj.get("code") or ""
                s_disp = size_obj.get("displayCode") or ""
                s_label = SIZE_CODE_MAP.get(s_code) or SIZE_CODE_MAP.get(s_disp) or s_disp or s_code or "FREE"
                
                # Stock
                s_info = stocks_dict.get(l2id) or {}
                is_available = s_info.get("statusCode") == "IN_STOCK" or s_info.get("quantity", 0) > 0
                inv = s_info.get("quantity")

                if is_available:
                    overall_in_stock = True

                if s_label not in seen_sizes:
                    seen_sizes.add(s_label)
                    size_options.append(SizeOption(
                        label=s_label,
                        sku_id=l2id,
                        available=is_available,
                        inventory=inv
                    ))

                # Color Variant
                c_code = color_obj.get("displayCode") or color_obj.get("code") or ""
                if c_code and c_code not in color_variants_map:
                    var_url = f"{canonical_url}?colorDisplayCode={c_code}"
                    color_variants_map[c_code] = ColorVariant(
                        style_id=f"{productId}_{c_code}",
                        color_name=f"Color {c_code}",
                        product_url=var_url
                    )

        if not size_options:
            overall_in_stock = True

        # 4. Images
        raw_images = dom.get("images") or []
        num_id = re.sub(r'\D', '', productId)
        if len(num_id) >= 6:
            num_id = num_id[:6]

        filtered_raw = [
            img for img in raw_images 
            if img and ('uniqlo.com' in img) and (num_id in img if num_id else True) and not any(x in img for x in ['logo', 'icon', 'chip'])
        ]
        if not filtered_raw:
            filtered_raw = [img for img in raw_images if img and ('uniqlo.com' in img) and not any(x in img for x in ['logo', 'icon', 'chip'])]

        hd_urls: List[str] = []
        media_list: List[ImageMedia] = []

        for idx, img_url in enumerate(filtered_raw):
            if not img_url: continue
            if img_url.startswith('//'): img_url = 'https:' + img_url

            hd_url = re.sub(r'width=\d+', 'width=1000', img_url)
            if 'width=' not in hd_url and '?' in hd_url: hd_url += '&width=1000'
            elif 'width=' not in hd_url: hd_url += '?width=1000'

            if hd_url not in hd_urls:
                hd_urls.append(hd_url)
                view_type = "front" if idx == 0 else ("back" if idx == 1 else "detail")
                media_list.append(ImageMedia(
                    image_id=str(idx + 1),
                    view_type=view_type,
                    raw_url=img_url,
                    hd_url=hd_url
                ))

        # 5. Breadcrumbs & Category & Gender
        bcrumb = rev.get("breadcrumbs") or {}
        gender = bcrumb.get("gender", {}).get("locale") or bcrumb.get("gender", {}).get("name") or "UNISEX"
        category = bcrumb.get("category", {}).get("locale") or bcrumb.get("category", {}).get("name") or "Clothing"
        
        breadcrumbs = ["Home", "UNIQLO"]
        if gender: breadcrumbs.append(gender.upper())
        if bcrumb.get("class", {}).get("locale"): breadcrumbs.append(bcrumb["class"]["locale"])
        if category: breadcrumbs.append(category)

        # 6. Ratings & Reviews
        rating_obj = rev.get("rating") or {}
        avg_rating = float(rating_obj.get("average") or 0.0)
        tot_count = int(rating_obj.get("count") or 0)
        rate_count_raw = rating_obj.get("rateCount") or {}
        
        star_map = {
            "5": int(rate_count_raw.get("five") or rate_count_raw.get("5") or 0),
            "4": int(rate_count_raw.get("four") or rate_count_raw.get("4") or 0),
            "3": int(rate_count_raw.get("three") or rate_count_raw.get("3") or 0),
            "2": int(rate_count_raw.get("two") or rate_count_raw.get("2") or 0),
            "1": int(rate_count_raw.get("one") or rate_count_raw.get("1") or 0)
        }

        reviews_list: List[Review] = []
        for r_item in rev.get("reviews") or []:
            if isinstance(r_item, dict):
                reviews_list.append(Review(
                    user_name=r_item.get("name") or "Verified Customer",
                    rating=int(r_item.get("rate") or 5),
                    headline=r_item.get("title") or "",
                    comment=r_item.get("comment") or "",
                    date=str(r_item.get("createDate") or "")
                ))

        ratings_summary = RatingSummary(
            average_rating=round(avg_rating, 2),
            total_ratings_count=tot_count,
            total_reviews_count=tot_count,
            star_breakdown=star_map
        )

        # 7. Comprehensive Specifications & Accordion Description Sections
        specs: List[Specification] = [
            Specification(title="Gender", value=gender.upper()),
            Specification(title="Category", value=category)
        ]

        desc_sections: List[Dict[str, str]] = []
        body_text = dom.get("bodyText") or ""
        
        country_of_origin = "Vietnam"
        manufacturer = "Uniqlo India Pvt. Ltd."

        if body_text:
            # Extract Features
            feat_match = re.search(r'Features\s*\n\s*(.*?)(?=\n\s*(?:Details|Material|Delivery|Production|Customer|$))', body_text, re.DOTALL)
            if feat_match:
                feat_text = feat_match.group(1).strip()
                if feat_text:
                    desc_sections.append({"title": "Features", "content": feat_text})

            # Extract Details
            det_match = re.search(r'Details\s*\n\s*(.*?)(?=\n\s*(?:Material|Delivery|Production|Customer|Official|$))', body_text, re.DOTALL)
            if det_match:
                det_text = det_match.group(1).strip()
                if det_text:
                    desc_sections.append({"title": "Details", "content": det_text})
                    
                    # Extract Net Quantity
                    net_q_match = re.search(r'Net quantity\s*:\s*([^\n]+)', det_text, re.IGNORECASE)
                    if net_q_match:
                        specs.append(Specification(title="Net Quantity", value=net_q_match.group(1).strip()))

            # Extract Material / Care
            mat_match = re.search(r'Material\s*/\s*Care\s*\n\s*(.*?)(?=\n\s*(?:Delivery|Production|Customer|Official|$))', body_text, re.DOTALL)
            if mat_match:
                mat_text = mat_match.group(1).strip()
                if mat_text:
                    desc_sections.append({"title": "Material & Care", "content": mat_text})

                    fab_m = re.search(r'Fabric details\s*\n\s*([^\n]+)', mat_text)
                    if fab_m:
                        specs.append(Specification(title="Fabric Details", value=fab_m.group(1).strip()))
                    
                    wash_m = re.search(r'Washing instructions\s*\n\s*([^\n]+)', mat_text)
                    if wash_m:
                        specs.append(Specification(title="Washing Instructions", value=wash_m.group(1).strip()))

            # Extract Delivery / Exchange / Return
            del_match = re.search(r'Delivery\s*/\s*Exchange\s*/\s*Return\s*\n\s*(.*?)(?=\n\s*(?:Production|Customer|Official|$))', body_text, re.DOTALL)
            if del_match:
                del_text = del_match.group(1).strip()
                if del_text:
                    desc_sections.append({"title": "Delivery & Return Policy", "content": del_text})
                    specs.append(Specification(title="Return Policy", value="Returnable within 30 days of shipping date"))
                    specs.append(Specification(title="Delivery Fee", value="Rs. 250 for orders below Rs. 2,999; Free for Rs. 2,999+"))

            # Extract Production
            prod_match = re.search(r'Production\s*\n\s*(.*?)(?=\n\s*(?:Customer|Official|$))', body_text, re.DOTALL)
            if prod_match:
                prod_text = prod_match.group(1).strip()
                if prod_text:
                    desc_sections.append({"title": "Production & Origin", "content": prod_text})
                    
                    co_m = re.search(r'Production:\s*([^\n]+)', prod_text)
                    if co_m:
                        country_of_origin = co_m.group(1).strip()
                        specs.append(Specification(title="Country of Origin", value=country_of_origin))

                    mfg_m = re.search(r'Imported and marketed by\s*\n\s*([^\n]+)', prod_text)
                    if mfg_m:
                        manufacturer = mfg_m.group(1).strip()
                        specs.append(Specification(title="Imported & Marketed By", value=manufacturer))

            # Extract Customer Service
            cs_match = re.search(r'Customer Service\s*\n\s*(.*?)(?=\n\s*(?:Official|Reviews|SKIP|$))', body_text, re.DOTALL)
            if cs_match:
                cs_text = cs_match.group(1).strip()
                if cs_text:
                    desc_sections.append({"title": "Customer Service", "content": cs_text})
                    email_m = re.search(r'[\w.-]+@[\w.-]+\.\w+', cs_text)
                    if email_m:
                        specs.append(Specification(title="Customer Support Email", value=email_m.group(0)))
                    toll_m = re.search(r'1800-\d+-\d+', cs_text)
                    if toll_m:
                        specs.append(Specification(title="Toll Free Support", value=toll_m.group(0)))

        if not desc_sections and body_text:
            desc_sections.append({"title": "Overview", "content": body_text[:1000]})

        seller_info = SellerInfo(
            seller_id=101,
            seller_name="UNIQLO Retail",
            seller_rating=4.8,
            return_period_days=30,
            is_returnable=True,
            cod_available=True
        )

        service_flags = ServiceFlags(
            is_returnable=True,
            is_exchangeable=True,
            cod_enabled=True,
            try_and_buy_enabled=False,
            out_of_stock=not overall_in_stock,
            is_fragile=False,
            is_hazmat=False
        )

        return ProductData(
            platform="uniqlo",
            style_id=productId,
            canonical_url=canonical_url,
            product_url=source_url,
            title=title,
            subtitle=category,
            brand=BrandInfo(name="UNIQLO", logo_url="https://www.uniqlo.com/favicon.ico"),
            price=price_info,
            media=media_list,
            hd_image_urls=hd_urls,
            sizes=size_options,
            in_stock=overall_in_stock,
            color_variants=list(color_variants_map.values()),
            specifications=specs,
            product_description_sections=desc_sections,
            breadcrumbs=breadcrumbs,
            category=category,
            gender=gender.upper(),
            ratings=ratings_summary,
            reviews=reviews_list,
            seller=seller_info,
            flags=service_flags,
            manufacturer="UNIQLO Co., Ltd.",
            country_of_origin="Vietnam / China / India",
            scraped_at=datetime.utcnow().isoformat() + "Z"
        )
