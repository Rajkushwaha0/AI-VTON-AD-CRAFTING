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
from .url_utils import normalize_amazon_url, extract_amazon_asin, detect_platform
from .loop_policy import ensure_playwright_event_loop_policy

class AmazonFetcherError(Exception):
    pass

def fetch_amazon_html_http(url: str, timeout: int = 12) -> str:
    """
    Fetches raw Amazon HTML using HTTP request with anti-bot headers.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Cache-Control': 'max-age=0',
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
        raise AmazonFetcherError(f"Amazon HTTP fetch failed for {url}: {e}")

def fetch_amazon_html_playwright(url: str, timeout: int = 30000) -> str:
    """
    Fallback Playwright browser fetcher for Amazon pages.
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
        raise AmazonFetcherError(f"Amazon Playwright fetch failed: {e!r}")

def fetch_amazon_page(url: str) -> str:
    """
    Smart hybrid fetcher for Amazon: tries HTTP fast fetch first, falls back to Playwright if Captcha or blocked.
    """
    try:
        html = fetch_amazon_html_http(url)
        if "productTitle" in html and "Robot Check" not in html and "Type the characters you see below" not in html:
            return html
    except Exception as e:
        print(f"Amazon HTTP fetch warning: {e}. Falling back to Playwright...")

    return fetch_amazon_html_playwright(url)


class AmazonScraper:
    """
    Production-grade Amazon Product Scraper.
    """

    @classmethod
    def scrape_url(cls, url_or_asin: str) -> ProductData:
        """
        Scrapes Amazon product page given URL or ASIN.
        """
        asin = extract_amazon_asin(url_or_asin)
        normalized_url = normalize_amazon_url(url_or_asin)
        
        html = fetch_amazon_page(normalized_url)
        return cls.parse_html(html, normalized_url, asin)

    @classmethod
    def parse_html(cls, html: str, source_url: str, asin: Optional[str] = None) -> ProductData:
        """
        Parses raw Amazon HTML into ProductData schema.
        """
        soup = BeautifulSoup(html, 'html.parser')
        product_id = asin or extract_amazon_asin(source_url) or "AMZ_PRODUCT"

        # Check for Schema.org JSON-LD
        json_ld = cls._extract_json_ld(soup)

        # 1. Product Title
        title = ""
        title_el = soup.find(id="productTitle")
        if title_el:
            title = title_el.get_text().strip()
        if not title and json_ld:
            title = json_ld.get('name') or ""
        if not title:
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'):
                title = og_title['content'].strip()

        # 2. Brand Name
        brand_name = ""
        brand_el = soup.find(id="bylineInfo") or soup.find('a', id="bylineInfo")
        if brand_el:
            brand_name = brand_el.get_text().strip()
            brand_name = re.sub(r'^(Visit the|Brand:)\s*', '', brand_name, flags=re.IGNORECASE)
            brand_name = re.sub(r'\s*Store$', '', brand_name, flags=re.IGNORECASE).strip()
            
        if not brand_name and json_ld:
            if isinstance(json_ld.get('brand'), dict):
                brand_name = json_ld['brand'].get('name', '')
            elif isinstance(json_ld.get('brand'), str):
                brand_name = json_ld['brand']

        if not brand_name:
            po_brand = soup.find('tr', class_=re.compile(r'po-brand'))
            if po_brand:
                brand_val = po_brand.find('td', class_=re.compile(r'po-break-word'))
                if brand_val:
                    brand_name = brand_val.get_text().strip()

        # 3. Price & MRP
        selling_price = 0.0
        mrp = 0.0

        # Selling Price extraction logic
        price_offscreen = soup.select_one('.a-price .a-offscreen, #priceblock_ourprice, #priceblock_dealprice, .apexPriceToPay .a-offscreen')
        if price_offscreen:
            price_text = price_offscreen.get_text().strip()
            clean_num = re.sub(r'[^\d.]', '', price_text.replace(',', ''))
            if clean_num:
                try:
                    selling_price = float(clean_num)
                except ValueError:
                    pass

        if selling_price == 0.0 and json_ld:
            offers = json_ld.get('offers') or {}
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                price_val = offers.get('price') or offers.get('lowPrice')
                if price_val:
                    try:
                        selling_price = float(price_val)
                    except ValueError:
                        pass

        if selling_price == 0.0:
            price_whole = soup.select_one('span.a-price-whole')
            if price_whole:
                clean_num = re.sub(r'[^\d.]', '', price_whole.get_text().replace(',', ''))
                if clean_num:
                    try:
                        selling_price = float(clean_num)
                    except ValueError:
                        pass

        # MRP / List Price
        list_price_el = soup.select_one('span.a-text-price span.a-offscreen, #listPrice, .a-size-small.a-color-secondary.a-line-through')
        if list_price_el:
            clean_mrp = re.sub(r'[^\d.]', '', list_price_el.get_text().replace(',', ''))
            if clean_mrp:
                try:
                    mrp = float(clean_mrp)
                except ValueError:
                    pass

        if mrp < selling_price:
            mrp = selling_price

        discount_amount = max(0.0, mrp - selling_price)
        discount_pct = int(round((discount_amount / mrp) * 100)) if mrp > 0 else 0

        # Dynamic currency detection
        currency_code = "INR"
        if json_ld and isinstance(json_ld.get('offers'), dict) and json_ld['offers'].get('priceCurrency'):
            currency_code = json_ld['offers']['priceCurrency']
        elif "amazon.com" in source_url:
            currency_code = "USD"
        elif "amazon.co.uk" in source_url:
            currency_code = "GBP"
        elif "amazon.de" in source_url or "amazon.fr" in source_url or "amazon.es" in source_url or "amazon.it" in source_url:
            currency_code = "EUR"
        elif "amazon.ca" in source_url:
            currency_code = "CAD"
        elif "amazon.com.au" in source_url:
            currency_code = "AUD"

        price_info = PriceInfo(
            mrp=mrp,
            selling_price=selling_price,
            discount_amount=discount_amount,
            discount_percentage=discount_pct,
            currency=currency_code
        )

        # 4. Images & High-Res CDN
        hd_urls: List[str] = []
        media_list: List[ImageMedia] = []

        def is_valid_product_image(url_str: str) -> bool:
            if not url_str or not url_str.startswith("http"):
                return False
            bad_keywords = ["grey-pixel", "play-button", "sprite", "play-icon", "overlay", "icon-", "transparent-pixel"]
            return not any(kw in url_str.lower() for kw in bad_keywords)

        # Strategy A: Extract colorImages script object
        idx_ci = html.find("'colorImages':")
        if idx_ci == -1:
            idx_ci = html.find('"colorImages":')
        if idx_ci != -1:
            init_idx = html.find("'initial':", idx_ci)
            if init_idx == -1:
                init_idx = html.find('"initial":', idx_ci)
            if init_idx != -1:
                start_bracket = html.find('[', init_idx)
                if start_bracket != -1:
                    try:
                        decoder = json.JSONDecoder()
                        img_data, _ = decoder.raw_decode(html[start_bracket:])
                        if isinstance(img_data, list):
                            for idx_img, item in enumerate(img_data):
                                if isinstance(item, dict):
                                    hires = item.get('hiRes') or item.get('large') or (
                                        item.get('main', {}).get(next(iter(item.get('main', {})), ''))
                                        if isinstance(item.get('main'), dict) and item.get('main')
                                        else None
                                    )
                                    if hires and is_valid_product_image(hires):
                                        hd_img = transform_to_hd_image_url(hires)
                                        if hd_img and hd_img not in hd_urls:
                                            hd_urls.append(hd_img)
                                            media_list.append(ImageMedia(image_id=str(len(hd_urls)), view_type="default", raw_url=hires, hd_url=hd_img))
                    except Exception:
                        pass

                    # Fallback if raw_decode failed due to single-quoted JS literal
                    if not hd_urls:
                        block = html[start_bracket:start_bracket + 15000]
                        found_urls = re.findall(r'[\'"](?:hiRes|large)[\'"]\s*:\s*[\'"](https?://[^\'"]+)[\'"]', block)
                        for u in found_urls:
                            if is_valid_product_image(u):
                                hd_img = transform_to_hd_image_url(u)
                                if hd_img and hd_img not in hd_urls:
                                    hd_urls.append(hd_img)
                                    media_list.append(ImageMedia(image_id=str(len(hd_urls)), view_type="default", raw_url=u, hd_url=hd_img))

        # Strategy B: JSON-LD images
        if not hd_urls and json_ld:
            ld_imgs = json_ld.get('image') or []
            if isinstance(ld_imgs, str):
                ld_imgs = [ld_imgs]
            for idx, img in enumerate(ld_imgs):
                if isinstance(img, str) and is_valid_product_image(img):
                    hd_img = transform_to_hd_image_url(img)
                    if hd_img and hd_img not in hd_urls:
                        hd_urls.append(hd_img)
                        media_list.append(ImageMedia(image_id=str(len(hd_urls)), view_type="default", raw_url=img, hd_url=hd_img))

        # Strategy C: Fallback DOM image scan
        if not hd_urls:
            landing_img = soup.find(id="landingImage") or soup.find(id="imgBlkFront")
            if landing_img:
                src = landing_img.get('data-old-hires') or landing_img.get('src') or ""
                if src and is_valid_product_image(src):
                    hd_img = transform_to_hd_image_url(src)
                    hd_urls.append(hd_img)
                    media_list.append(ImageMedia(image_id="1", view_type="main", raw_url=src, hd_url=hd_img))

            alt_imgs = soup.select('#altImages img, #imageBlock img')
            for idx, a_img in enumerate(alt_imgs):
                src = a_img.get('src') or ""
                if src and is_valid_product_image(src):
                    hd_img = transform_to_hd_image_url(src)
                    if hd_img and hd_img not in hd_urls:
                        hd_urls.append(hd_img)
                        media_list.append(ImageMedia(image_id=str(len(hd_urls)), view_type="alt", raw_url=src, hd_url=hd_img))

        # 5. Breadcrumbs & Categories
        breadcrumbs = []
        bc_els = soup.select('#wayfinding-breadcrumbs_feature_div ul li span.a-list-item a')
        for bc in bc_els:
            bc_text = bc.get_text().strip()
            if bc_text:
                breadcrumbs.append(bc_text)
        category = breadcrumbs[-1] if breadcrumbs else ""
        gender = "Women" if any("women" in b.lower() for b in breadcrumbs) else ("Men" if any("men" in b.lower() for b in breadcrumbs) else "General")

        # 5.5 Sizes & Color Variants Extraction
        sizes_list: List[SizeOption] = []
        color_variants: List[ColorVariant] = []
        seen_sizes = set()

        # Sizes Strategy A: Script JSON variationValues
        m_var = re.search(r'"variationValues"\s*:\s*(\{.*?\})', html)
        if m_var:
            try:
                var_data = json.loads(m_var.group(1))
                if 'size_name' in var_data and isinstance(var_data['size_name'], list):
                    for sz in var_data['size_name']:
                        if sz and str(sz) not in seen_sizes:
                            seen_sizes.add(str(sz))
                            sizes_list.append(SizeOption(label=str(sz), available=True))
            except Exception:
                pass

        # Sizes Strategy B: DOM elements
        if not sizes_list:
            size_options = soup.select('#native_dropdown_selected_size_name option, #inline_twister_size_name li button, ul.a-button-list li button, li.swatch-list-item-text, span.text-swatch-button')
            for opt in size_options:
                s_text = opt.get_text().strip()
                if s_text and s_text not in ['Select', 'Select Size'] and len(s_text) <= 15:
                    clean_s = s_text.split('\n')[0].strip()
                    if clean_s and clean_s not in seen_sizes:
                        seen_sizes.add(clean_s)
                        sizes_list.append(SizeOption(label=clean_s, available=True))

        # 5.5 Color Swatch Image Map Construction
        swatch_image_map: Dict[str, str] = {}
        swatch_selectors = [
            'li[id*="color_name_"]', 'li.swatchAvailable', 'li.swatchSelect',
            'ul.a-button-list li', 'span[id^="color_name_"]', '#variation_color_name li', '#inline_twister_color_name li'
        ]
        for sel in swatch_selectors:
            for el in soup.select(sel):
                img = el.find('img') if el.name != 'img' else el
                if img:
                    src = img.get('src') or img.get('data-src') or img.get('data-old-hires') or ""
                    alt = (img.get('alt') if img else '') or el.get('title') or el.get('aria-label') or el.get_text().strip()
                    if alt and 'savings' in alt:
                        alt = alt.split(' ')[0]
                    if alt and src and is_valid_product_image(src):
                        clean_name = alt.strip().lower()
                        if clean_name and clean_name not in swatch_image_map:
                            swatch_image_map[clean_name] = transform_to_hd_image_url(src, width=300)
                            swatch_image_map[re.sub(r'\s+', ' ', clean_name)] = transform_to_hd_image_url(src, width=300)

        # Color Variants Strategy A: colorToAsin regex search
        m_color = re.search(r"['\"]colorToAsin['\"]\s*:\s*(\{.*?\})", html)
        if m_color:
            raw_c = m_color.group(1)
            for m in re.finditer(r"['\"]?([A-Za-z0-9_ -]+)['\"]?\s*:\s*\{[^}]*['\"]?asin['\"]?\s*:\s*['\"]([A-Z0-9]{10})['\"]", raw_c):
                c_name, v_asin = m.group(1).strip(), m.group(2).strip()
                if c_name and not any(v.color_name.lower() == c_name.lower() for v in color_variants):
                    c_key = c_name.lower()
                    thumb_img = swatch_image_map.get(c_key) or swatch_image_map.get(re.sub(r'\s+', ' ', c_key))
                    domain_dp = "amazon.in" if "amazon.in" in source_url else ("amazon.co.uk" if "amazon.co.uk" in source_url else "amazon.com")
                    color_variants.append(ColorVariant(
                        style_id=v_asin,
                        color_name=c_name,
                        image_thumb=thumb_img,
                        product_url=f"https://www.{domain_dp}/dp/{v_asin}"
                    ))

        # Color Variants Strategy B: variationValues JSON color_name list
        if not color_variants and m_var:
            try:
                var_data = json.loads(m_var.group(1))
                if 'color_name' in var_data and isinstance(var_data['color_name'], list):
                    for idx, c_name in enumerate(var_data['color_name']):
                        c_str = str(c_name).strip()
                        if c_str and not any(v.color_name.lower() == c_str.lower() for v in color_variants):
                            c_key = c_str.lower()
                            thumb_img = swatch_image_map.get(c_key) or swatch_image_map.get(re.sub(r'\s+', ' ', c_key))
                            color_variants.append(ColorVariant(
                                style_id=f"{product_id}_C{idx+1}",
                                color_name=c_str,
                                image_thumb=thumb_img,
                                product_url=source_url
                            ))
            except Exception:
                pass

        # Color Variants Strategy C: DOM swatches
        if not color_variants:
            for cs in soup.select('#variation_color_name li, #inline_twister_color_name li, li.swatch-list-item-image, div#color_name_0_desktop img, span[id^="color_name_"]'):
                img = cs.find('img')
                txt = cs.get_text().strip() or (img.get('alt') if img else '')
                if txt and 'savings' in txt:
                    txt = txt.split(' ')[0]
                v_asin = cs.get('data-defaultasin') or cs.get('data-dp-url') or ''
                if txt and not any(v.color_name.lower() == txt.lower() for v in color_variants):
                    c_key = txt.lower()
                    thumb_img = swatch_image_map.get(c_key) or (transform_to_hd_image_url(img.get('src'), width=300) if img and is_valid_product_image(img.get('src')) else None)
                    domain_dp = "amazon.in" if "amazon.in" in source_url else "amazon.com"
                    color_variants.append(ColorVariant(
                        style_id=v_asin or f"AMZ_{len(color_variants)+1}",
                        color_name=txt,
                        image_thumb=thumb_img,
                        product_url=f"https://www.{domain_dp}/dp/{v_asin}" if v_asin else source_url
                    ))

        # 6. Specifications & Bullet Points
        specs_dict: Dict[str, str] = {}

        # Strategy A: div.product-facts-detail (Amazon mobile/desktop top highlights & additional facts)
        for fact in soup.select('div.product-facts-detail'):
            lines = [t.strip() for t in fact.get_text(separator='\n').split('\n') if t.strip()]
            if len(lines) >= 2:
                k = lines[0]
                v = ' '.join(lines[1:])
                if len(k) < 60 and len(v) < 250:
                    specs_dict[k] = v

        # Strategy B: productOverview_feature_div (Overview table)
        for row in soup.select('div#productOverview_feature_div tr, div#productOverview_feature_div div.a-row'):
            cols = row.select('td, span.a-text-bold, span.po-break-word')
            if len(cols) >= 2:
                k = cols[0].get_text().strip().rstrip(':')
                v = cols[1].get_text().strip()
                if k and v and len(k) < 60 and k not in specs_dict:
                    specs_dict[k] = v

        # Strategy C: Tech specs table, po-table, detailBullets
        for row in soup.select('#productDetails_techSpec_section_1 tr, #detailBullets_feature_div li, table.prodDetTable tr, table.po-table tr'):
            th = row.find(['th', 'td', 'span'], class_=re.compile(r'po-label|a-text-bold|prodDetSectionEntry')) or row.find('th')
            td = row.find(['td', 'span'], class_=re.compile(r'po-value|prodDetAttrValue')) or row.find('td')
            if th and td and th != td:
                k = th.get_text().strip().rstrip(':').replace('\u200f', '').replace('\u200e', '')
                v = td.get_text().strip().replace('\u200f', '').replace('\u200e', '')
                if k and v and len(k) < 60 and k not in specs_dict and k not in ['Customer Reviews', 'Best Sellers Rank']:
                    specs_dict[k] = v

        specs_list = [Specification(title=k, value=v) for k, v in specs_dict.items()]

        # Description Sections / Bullets
        desc_sections: List[Dict[str, str]] = []
        bullets: List[str] = []

        # Bullet search strategy A: feature-bullets & unordered list items
        for b in soup.select('#feature-bullets ul li span.a-list-item, div#featurebullets_feature_div li span, ul.a-unordered-list.a-vertical li'):
            txt = b.get_text().strip()
            if txt and txt not in bullets and not txt.startswith('About this item'):
                if len(txt) > 15 and not any(k in txt for k in ['Share', 'Report', 'Feedback', 'Returns', 'Delivery', 'Add to Wish', 'Video Player']):
                    bullets.append(txt)

        if bullets:
            desc_sections.append({
                'title': 'About this item',
                'description': "\n".join([f"• {b}" for b in bullets])
            })

        # Product description paragraph
        p_desc = soup.select_one('#productDescription p, #productDescription span, div#productDescription')
        if p_desc:
            d_txt = p_desc.get_text().strip()
            if d_txt and len(d_txt) > 30:
                desc_sections.append({
                    'title': 'Product Description',
                    'description': d_txt
                })

        # 7. Ratings & Reviews
        avg_rating = 0.0
        total_ratings = 0

        rating_popover = soup.select_one('#acrPopover span.a-icon-alt, i.a-icon-star span')
        if rating_popover:
            m = re.search(r'([0-9.]+)', rating_popover.get_text())
            if m:
                try:
                    avg_rating = float(m.group(1))
                except ValueError:
                    pass

        if avg_rating == 0.0 and json_ld:
            agg = json_ld.get('aggregateRating') or {}
            if isinstance(agg, dict):
                try:
                    avg_rating = float(agg.get('ratingValue') or 0.0)
                    total_ratings = int(agg.get('ratingCount') or 0)
                except ValueError:
                    pass

        rating_count_el = soup.find(id="acrCustomerReviewText")
        if rating_count_el and total_ratings == 0:
            m = re.search(r'([0-9,]+)', rating_count_el.get_text())
            if m:
                try:
                    total_ratings = int(m.group(1).replace(',', ''))
                except ValueError:
                    pass

        reviews_list: List[Review] = []
        review_cards = soup.select('div[data-hook="review"], div.review, div.cr-review-card, div._Y3Itd_review')
        if not review_cards:
            review_titles = soup.select('h5._Y3Itd_single-review-title_2aKRE, [data-hook="review-title"]')
            for rt in review_titles:
                p_card = rt.find_parent(lambda tag: tag.name == 'div' and len(tag.get_text()) > 50)
                if p_card and p_card not in review_cards:
                    review_cards.append(p_card)

        for card in review_cards[:8]:
            user_el = card.select_one('.a-profile-name')
            headline_el = card.select_one('[data-hook="review-title"] span, .review-title, h5._Y3Itd_single-review-title_2aKRE')
            body_el = card.select_one('[data-hook="review-body"] span, .review-text')
            date_el = card.select_one('[data-hook="review-date"]')
            rating_el = card.select_one('i[data-hook="review-star-rating"] span, i.a-icon-star span')

            user_name = user_el.get_text().strip() if user_el else "Amazon Customer"
            headline = headline_el.get_text().strip() if headline_el else ""
            comment = body_el.get_text().strip() if body_el else ""
            date_str = date_el.get_text().strip() if date_el else ""

            if not comment:
                card_txt = card.get_text(separator=' | ').strip()
                lines = [l.strip() for l in card_txt.split(' | ') if l.strip()]
                for line in lines:
                    if line in ['Verified Purchase', 'Helpful', 'Report', 'Read more', 'Read less'] or 'out of 5 stars' in line or 'Reviewed in' in line:
                        continue
                    if len(line) < 50 and not headline and not line.startswith('Size:') and not line.startswith('Colour:'):
                        headline = line
                    elif len(line) > 30 and not comment:
                        comment = line

            r_val = 5
            if rating_el:
                rm = re.search(r'([0-9.]+)', rating_el.get_text())
                if rm:
                    try:
                        r_val = int(float(rm.group(1)))
                    except ValueError:
                        pass

            if comment or headline:
                reviews_list.append(Review(
                    user_name=user_name,
                    rating=r_val,
                    headline=headline,
                    comment=comment,
                    date=date_str
                ))

        rating_summary = RatingSummary(
            average_rating=round(avg_rating, 2),
            total_ratings_count=total_ratings,
            total_reviews_count=len(reviews_list)
        )

        # 8. Seller & Stock Info
        avail_el = soup.find(id="availability")
        avail_text = avail_el.get_text().strip() if avail_el else ""
        in_stock = "currently unavailable" not in avail_text.lower() and "out of stock" not in avail_text.lower()

        seller_el = soup.find(id="merchant-info") or soup.find(id="sellerProfileTriggerId")
        seller_name = seller_el.get_text().strip() if seller_el else "Amazon Fulfilled Seller"
        m_seller = re.search(r'Sold by\s+([^.\n]+)', seller_name, re.IGNORECASE)
        if m_seller:
            seller_name = m_seller.group(1).split(' and ')[0].strip()

        seller_info = SellerInfo(
            seller_name=seller_name,
            is_returnable=True,
            cod_available=True
        )

        flags = ServiceFlags(
            is_returnable=True,
            is_exchangeable=True,
            cod_enabled=True,
            out_of_stock=not in_stock
        )

        canonical_url = f"https://www.amazon.in/dp/{product_id}" if asin else source_url

        return ProductData(
            platform="amazon",
            style_id=product_id,
            canonical_url=canonical_url,
            product_url=source_url,
            title=title or f"Amazon Product {product_id}",
            brand=BrandInfo(name=brand_name or specs_dict.get("Brand Name") or "Amazon Brand"),
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
            seller=seller_info,
            flags=flags,
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
