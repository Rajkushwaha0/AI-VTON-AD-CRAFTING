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
from .image_hd import transform_to_hd_image_url
from .url_utils import normalize_flipkart_url, extract_flipkart_pid
from .loop_policy import ensure_playwright_event_loop_policy

class FlipkartFetcherError(Exception):
    pass

def fetch_flipkart_html_playwright(url: str, timeout: int = 30000) -> str:
    """
    Playwright headless browser fetcher for Flipkart.
    Waits for dynamic DOM hydration and network requests.
    """
    try:
        ensure_playwright_event_loop_policy()
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                viewport={'width': 1366, 'height': 768},
                extra_http_headers={
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
                }
            )
            page = context.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            page.goto(url, wait_until='domcontentloaded', timeout=timeout)
            page.wait_for_timeout(2500)
            content = page.content()
            browser.close()
            return content
    except Exception as e:
        raise FlipkartFetcherError(f"Flipkart Playwright fetch failed: {e!r}")

def fetch_flipkart_html_http(url: str, timeout: int = 12) -> str:
    """
    Fetches raw Flipkart HTML using HTTP request.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br'
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
        raise FlipkartFetcherError(f"Flipkart HTTP fetch failed for {url}: {e}")

def fetch_flipkart_page(url: str) -> str:
    """
    Smart hybrid fetcher for Flipkart: tries HTTP fast fetch first, falls back to Playwright if needed.
    """
    try:
        html = fetch_flipkart_html_http(url)
        if "window.__INITIAL_STATE__" in html or "schema.org" in html or "B_NuT2" in html or "Nx9bqj" in html:
            return html
    except Exception as e:
        print(f"Flipkart HTTP fetch warning: {e}. Falling back to Playwright...")

    return fetch_flipkart_html_playwright(url)


class FlipkartScraper:
    """
    Production-grade Flipkart Product Scraper.
    """

    @classmethod
    def scrape_url(cls, url_or_pid: str) -> ProductData:
        """
        Scrapes Flipkart product page given URL or PID.
        """
        pid = extract_flipkart_pid(url_or_pid)
        normalized_url = normalize_flipkart_url(url_or_pid)
        
        html = fetch_flipkart_page(normalized_url)
        return cls.parse_html(html, normalized_url, pid)

    @classmethod
    def parse_html(cls, html: str, source_url: str, pid: Optional[str] = None) -> ProductData:
        """
        Parses Flipkart product page using JSON-LD, window.__INITIAL_STATE__, or BeautifulSoup DOM.
        """
        soup = BeautifulSoup(html, 'html.parser')
        product_id = pid or extract_flipkart_pid(source_url) or "FK_PRODUCT"

        # Strategy 1: JSON-LD Schema.org extraction
        json_ld_data = cls._extract_json_ld(soup)

        # Basic defaults
        title = ""
        brand_name = ""
        selling_price = 0.0
        mrp = 0.0
        hd_urls: List[str] = []
        media_list: List[ImageMedia] = []
        sizes_list: List[SizeOption] = []
        color_variants: List[ColorVariant] = []
        specs_dict: Dict[str, str] = {}
        reviews_list: List[Review] = []
        breadcrumbs: List[str] = []
        avg_rating = 0.0
        total_ratings = 0
        total_reviews = 0
        in_stock = True
        return_days = 10
        seller_name = "Flipkart Seller"

        # 1. Process JSON-LD if available
        if json_ld_data:
            title = json_ld_data.get('name') or title
            if isinstance(json_ld_data.get('brand'), dict):
                brand_name = json_ld_data['brand'].get('name', '')
            elif isinstance(json_ld_data.get('brand'), str):
                brand_name = json_ld_data['brand']

            # Price from JSON-LD
            offers = json_ld_data.get('offers') or {}
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                price_val = offers.get('price') or offers.get('lowPrice')
                if price_val:
                    try:
                        selling_price = float(price_val)
                    except ValueError:
                        pass
                if offers.get('availability'):
                    in_stock = "InStock" in str(offers.get('availability'))

                # Return policy from JSON-LD
                return_pol = offers.get('hasMerchantReturnPolicy') or {}
                if isinstance(return_pol, dict) and return_pol.get('merchantReturnDays'):
                    try:
                        return_days = int(return_pol['merchantReturnDays'])
                    except ValueError:
                        pass

            # Images from JSON-LD
            ld_images = json_ld_data.get('image') or []
            if isinstance(ld_images, str):
                ld_images = [ld_images]
            for img in ld_images:
                if isinstance(img, str) and img.startswith("http"):
                    hd = transform_to_hd_image_url(img)
                    if hd and hd not in hd_urls:
                        hd_urls.append(hd)
                        media_list.append(ImageMedia(image_id=str(len(hd_urls)), view_type="default", raw_url=img, hd_url=hd))

            # Aggregate Rating
            agg_rating = json_ld_data.get('aggregateRating') or {}
            if isinstance(agg_rating, dict):
                try:
                    avg_rating = float(agg_rating.get('ratingValue') or 0.0)
                    total_ratings = int(agg_rating.get('ratingCount') or 0)
                    total_reviews = int(agg_rating.get('reviewCount') or 0)
                except ValueError:
                    pass

            # MRP extraction from JSON-LD description (e.g. "For Only Rs. 6999.0")
            desc = json_ld_data.get('description', '')
            m_mrp = re.search(r'For Only Rs\.?\s*([0-9,.]+)', desc, re.IGNORECASE)
            if m_mrp:
                try:
                    mrp = float(m_mrp.group(1).replace(',', ''))
                except ValueError:
                    pass

        # 2. DOM Title Fallback
        if not title or title.strip().lower() in ("image", "product", "item", "null", "undefined"):
            title_el = soup.select_one('span.B_NuT2, h1._2xmZ3w, span.VU-VGd, span._35KyD6, h1, div.v1zwn21l')
            if title_el:
                title = title_el.get_text().strip()
            else:
                og_title = soup.find('meta', property='og:title')
                if og_title and og_title.get('content'):
                    title = og_title['content'].strip()

        # 2b. URL Slug Title Fallback
        if not title or title.strip().lower() in ("image", "product", "item", "null", "undefined"):
            if source_url:
                clean_path = source_url.split('?')[0].rstrip('/')
                parts = [p for p in clean_path.split('/') if p and p.lower() not in ('http:', 'https:', 'www.flipkart.com', 'flipkart.com', 'p', 'dp')]
                for part in reversed(parts):
                    if '-' in part and not part.startswith('itm') and not part.startswith('pid'):
                        words = part.replace('-', ' ').title().split()
                        if len(words) > 1:
                            title = " ".join(words)
                            break

        if not brand_name and title:
            brand_name = title.split()[0]


        # 3. DOM Selling Price Fallback
        if selling_price == 0.0:
            price_el = soup.select_one('div._30jeq3, div.Nx9bqj, div._16Jk6d, div.hlCw05')
            if price_el:
                clean_p = re.sub(r'[^\d.]', '', price_el.get_text().replace(',', ''))
                if clean_p:
                    try:
                        selling_price = float(clean_p)
                    except ValueError:
                        pass

        # 4. DOM MRP Fallback
        if mrp == 0.0:
            mrp_el = soup.select_one('div._3I9_wc, div.yRaYx, div._2p6l2c, div.yR5Bfl, span.yRaYx')
            if mrp_el:
                clean_m = re.sub(r'[^\d.]', '', mrp_el.get_text().replace(',', ''))
                if clean_m:
                    try:
                        mrp = float(clean_m)
                    except ValueError:
                        pass

        if mrp < selling_price:
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

        # 5. DOM Images Fallback
        if not hd_urls:
            img_tags = soup.find_all('img')
            for img in img_tags:
                src = img.get('src') or img.get('data-src') or ""
                if ("flixcart.com/image/" in src or "rukminim" in src) and not src.endswith(".svg"):
                    if "spotlight" not in src and "logo" not in src and "batman-returns" not in src:
                        hd = transform_to_hd_image_url(src)
                        if hd and hd not in hd_urls:
                            hd_urls.append(hd)
                            media_list.append(ImageMedia(image_id=str(len(hd_urls)), view_type="default", raw_url=src, hd_url=hd))

        # 6. Sizes Extraction
        # Strategy A: SwatchAttr size links
        seen_size_labels = set()
        for a in soup.find_all('a'):
            href = a.get('href') or ''
            txt = a.get_text().strip()
            if ('swatchAttr=size' in href or 'swatchAttr=Size' in href) and txt:
                if txt not in seen_size_labels and len(txt) <= 12:
                    seen_size_labels.add(txt)
                    sizes_list.append(SizeOption(label=txt, available=True))

        # Strategy B: Selector classes
        if not sizes_list:
            size_nodes = soup.select('a._1fGeB5, li._3V_oH7, div._3O_RAK, a.CDPmo+div, div.CDPmo')
            for s_node in size_nodes:
                lbl = s_node.get_text().strip()
                if lbl and len(lbl) <= 8 and lbl not in seen_size_labels:
                    seen_size_labels.add(lbl)
                    sizes_list.append(SizeOption(label=lbl, available=True))

        # 7. Color Variants Extraction (3-Strategy Extraction Engine)
        seen_pids = {product_id}

        # Strategy A: Parse window.__staticRouterHydrationData & __INITIAL_STATE__
        hydration_matches = re.findall(r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\(("(?:[^"\\]|\\.)*")\);', html)
        for h_match in hydration_matches:
            try:
                raw_h_json = json.loads(h_match)
                found_h_pids = set(re.findall(r'pid=([A-Z0-9]{16})', raw_h_json))
                for vp in found_h_pids:
                    if vp not in seen_pids:
                        seen_pids.add(vp)
                        color_variants.append(ColorVariant(
                            style_id=vp,
                            color_name=f"Color Variant ({vp[:6]})",
                            image_thumb=None,
                            product_url=f"https://www.flipkart.com/product/p/itm?pid={vp}"
                        ))
            except Exception:
                pass

        # Strategy B: Swatch Containers in DOM
        swatch_containers = soup.select('div[class*="swatch"], div[class*="Swatch"], div[class*="option"], div[class*="Option"], ul[class*="swatch"], div._2C222o, div._30Je3n, div._1q8v1, div.CDPmo')
        for container in swatch_containers:
            for a in container.find_all('a'):
                href = a.get('href') or ''
                v_pid = extract_flipkart_pid(href)
                if v_pid and v_pid not in seen_pids:
                    seen_pids.add(v_pid)
                    img_child = a.find('img')
                    thumb = img_child.get('src') or img_child.get('data-src') if img_child else None
                    v_title = a.get('title') or a.get('aria-label') or (img_child.get('alt') if img_child else '') or a.get_text().strip()
                    raw_v_title = v_title if (v_title and v_title.lower() not in ['image', 'product image', 'thumbnail', 'photo', 'product', 'item', 'null', 'undefined']) else f"Color {v_pid[:6]}"
                    color_variants.append(ColorVariant(
                        style_id=v_pid,
                        color_name=raw_v_title,
                        image_thumb=transform_to_hd_image_url(thumb, width=300) if thumb else None,
                        product_url=f"https://www.flipkart.com{href}" if href.startswith('/') else href
                    ))

        # Strategy C: All <a> tags with pid= parameter
        for a in soup.find_all('a'):
            href = a.get('href') or ''
            if ('pid=' in href or 'swatchAttr=' in href) and ('/p/' in href or 'pid=' in href):
                v_pid = extract_flipkart_pid(href)
                if v_pid and v_pid not in seen_pids:
                    parent_classes = " ".join(a.parent.get('class', [])) if a.parent else ""
                    if not any(x in href.lower() or x in parent_classes.lower() for x in ['recommend', 'similar', 'footer', 'cart', 'write-question', 'review']):
                        seen_pids.add(v_pid)
                        img_child = a.find('img')
                        thumb = img_child.get('src') or img_child.get('data-src') if img_child else None
                        v_title = a.get('title') or a.get('aria-label') or (img_child.get('alt') if img_child else '') or a.get_text().strip()
                        raw_v_title = v_title if (v_title and v_title.lower() not in ['image', 'product image', 'thumbnail', 'photo', 'product', 'item', 'null', 'undefined']) else f"Color {v_pid[:6]}"
                        color_variants.append(ColorVariant(
                            style_id=v_pid,
                            color_name=raw_v_title,
                            image_thumb=transform_to_hd_image_url(thumb, width=300) if thumb else None,
                            product_url=f"https://www.flipkart.com{href}" if href.startswith('/') else href
                        ))



        # 8. Product Highlights & Specifications Extraction
        ignore_spec_terms = [
            '10-day', 'cash on', 'customer', 'support', 'return', 'delivery',
            'location not set', 'ask a question', 'be the first to ask', 'add to cart',
            'buy now', 'fabric quality', 'stitching quality', 'value for money',
            'comfort', 'true to specs', 'ratings & reviews', 'ratings &', 'similar products'
        ]

        def is_valid_spec_kv(k_str: str, v_str: str) -> bool:
            k_clean = k_str.strip()
            v_clean = v_str.strip()
            if not k_clean or not v_clean:
                return False
            if len(k_clean) > 50 or len(v_clean) > 200:
                return False
            k_lower = k_clean.lower()
            v_lower = v_clean.lower()
            if any(ign in k_lower or ign in v_lower for ign in ignore_spec_terms):
                return False
            return True

        # Strategy A: Key Highlights / Highlights header section
        for hl_text in ['Key Highlights', 'Highlights', 'Product highlights', 'Product Details', 'Specifications']:
            hl_headers = soup.find_all(string=re.compile(r'^\s*' + re.escape(hl_text) + r'\s*$', re.IGNORECASE))
            for hl_header in hl_headers:
                p = hl_header.parent
                depth = 0
                while p and p.name not in ['body', 'html'] and depth < 6:
                    # Scan for grid containers within this header block
                    grids = p.find_all('div', class_=re.compile(r'grid-formation|grid-column-2|_1psv1z'))
                    if grids:
                        for g in grids:
                            texts = [t.strip() for t in g.get_text(separator='\n').split('\n') if t.strip()]
                            if len(texts) == 2:
                                if is_valid_spec_kv(texts[0], texts[1]) and texts[0] not in specs_dict:
                                    specs_dict[texts[0]] = texts[1]
                    p = p.parent
                    depth += 1

        # Strategy B: Dynamic Grid Formation elements
        for div in soup.find_all('div', class_=re.compile(r'grid-formation|_1psv1zei0|grid-column-2')):
            texts = [t.strip() for t in div.get_text(separator='\n').split('\n') if t.strip()]
            if len(texts) == 2:
                k, v = texts[0], texts[1]
                if is_valid_spec_kv(k, v) and k not in specs_dict:
                    specs_dict[k] = v

        # Strategy C: Standard HTML tables and spec rows
        spec_rows = soup.select('div._14cfVK table tr, tr._1s5BxE, div.row._1hKmbr, div._2k6WMG, div._3k-BhJ')
        for row in spec_rows:
            cols = row.find_all(['td', 'th', 'div'])
            if len(cols) >= 2:
                k = cols[0].get_text().strip()
                v = cols[1].get_text().strip()
                if is_valid_spec_kv(k, v) and k not in specs_dict:
                    specs_dict[k] = v

        # Strategy D: Bullet highlights list
        hl_bullets = soup.select('div._21AqiI li, div._2c21Ym li, ul._35jC4_ li, div.x-R37C li')
        for li in hl_bullets:
            txt = li.get_text().strip()
            if txt and len(txt) < 150:
                if ':' in txt:
                    parts = txt.split(':', 1)
                    if is_valid_spec_kv(parts[0], parts[1]) and parts[0].strip() not in specs_dict:
                        specs_dict[parts[0].strip()] = parts[1].strip()
                elif txt not in specs_dict and not any(ign in txt.lower() for ign in ignore_spec_terms):
                    specs_dict[f"Highlight {len(specs_dict)+1}"] = txt

        specs_list = [Specification(title=k, value=v) for k, v in specs_dict.items()]

        # Description Sections
        desc_sections = []
        if specs_dict:
            hl_summary = "\n".join([f"• {k}: {v}" for k, v in list(specs_dict.items())[:12]])
            desc_sections.append({
                'title': 'Product Highlights',
                'description': hl_summary
            })

        # Check for description text block
        desc_el = soup.select_one('div._1mXEkf, div._3la3Fn, div.R9F3_l, div._1Vqf_1')
        if desc_el:
            desc_text = desc_el.get_text().strip()
            if desc_text and len(desc_text) > 10:
                desc_sections.append({
                    'title': 'Description',
                    'description': desc_text
                })

        # 9. Breadcrumbs, Category & Gender
        for a in soup.find_all('a'):
            href = a.get('href') or ''
            txt = a.get_text().strip()
            if ('/pr?sid=' in href or '/clothing-and-accessories/' in href) and txt:
                if txt not in breadcrumbs and not any(k in txt for k in ['Cart', 'AD', 'OFF', '₹', 'Delivery']):
                    breadcrumbs.append(txt)

        category = breadcrumbs[-1] if breadcrumbs else ""
        gender = "Women" if any("women" in b.lower() for b in breadcrumbs) or specs_dict.get("Ideal For") == "Women" else "General"
        if any("men" in b.lower() for b in breadcrumbs) and "women" not in gender.lower():
            gender = "Men"

        # 10. Ratings & Reviews DOM Fallback
        if avg_rating == 0.0:
            rating_el = soup.select_one('div._3LWZlK, div.XqfiA, div.W_Pres, span._1lV2u')
            if rating_el:
                m = re.search(r'([0-9.]+)', rating_el.get_text())
                if m:
                    try:
                        avg_rating = float(m.group(1))
                    except ValueError:
                        pass

        # Rating counts DOM
        if total_ratings == 0:
            count_el = soup.find(string=re.compile(r'Ratings\s*&', re.IGNORECASE))
            if count_el:
                m_count = re.search(r'([0-9,]+)\s*Ratings', count_el, re.IGNORECASE)
                if m_count:
                    try:
                        total_ratings = int(m_count.group(1).replace(',', ''))
                    except ValueError:
                        pass

        rating_summary = RatingSummary(
            average_rating=round(avg_rating, 2),
            total_ratings_count=total_ratings,
            total_reviews_count=total_reviews
        )

        # 11. Seller & Service Info
        seller_el = soup.select_one('div._1RL2W, span._1RL2W, a._1RL2W, #sellerName')
        if seller_el:
            seller_name = seller_el.get_text().strip()

        return ProductData(
            platform="flipkart",
            style_id=product_id,
            canonical_url=source_url,
            product_url=source_url,
            title=title or f"Flipkart Product {product_id}",
            brand=BrandInfo(name=brand_name or specs_dict.get("Brand") or "Flipkart Brand"),
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
            reviews=reviews_list,
            seller=SellerInfo(
                seller_name=seller_name,
                return_period_days=return_days,
                is_returnable=True,
                cod_available=True
            ),
            flags=ServiceFlags(out_of_stock=not in_stock),
            scraped_at=datetime.utcnow().isoformat() + "Z"
        )

    @classmethod
    def _extract_json_ld(cls, soup: BeautifulSoup) -> Optional[Dict[str, Any]]:
        """
        Extracts Schema.org Product JSON-LD from script tags.
        """
        for script in soup.find_all('script', type='application/ld+json'):
            text = script.string or ""
            if '"Product"' in text or '"@type":"Product"' in text or 'schema.org' in text:
                try:
                    data = json.loads(text)
                    if isinstance(data, list):
                        for item in data:
                            if item.get('@type') == 'Product':
                                return item
                    elif isinstance(data, dict):
                        if data.get('@type') == 'Product':
                            return data
                except Exception:
                    pass
        return None

    @classmethod
    def _extract_initial_state(cls, html: str) -> Optional[Dict[str, Any]]:
        """
        Extracts window.__INITIAL_STATE__ JSON object.
        """
        idx = html.find('window.__INITIAL_STATE__ =')
        if idx != -1:
            start_idx = html.find('{', idx)
            if start_idx != -1:
                try:
                    dec = json.JSONDecoder()
                    obj, _ = dec.raw_decode(html[start_idx:])
                    return obj
                except Exception:
                    pass
        return None
