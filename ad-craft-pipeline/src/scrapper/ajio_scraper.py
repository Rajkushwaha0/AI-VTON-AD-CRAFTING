import re
import json
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple

from bs4 import BeautifulSoup

from .models import (
    ProductData, PriceInfo, BrandInfo, ImageMedia, SizeOption,
    ColorVariant, Specification, RatingSummary, Review, SellerInfo, ServiceFlags
)
from .url_utils import normalize_ajio_url, extract_ajio_code
from .loop_policy import ensure_playwright_event_loop_policy


class AjioFetcherError(Exception):
    pass


# Ajio serves several sized renditions of every image. Highest quality first.
_IMAGE_FORMAT_PRIORITY = [
    "superZoomPdp", "zoom", "xxl", "xl", "product",
    "mobileProductListingImage", "listingImage", "thumbnail", "cartIcon",
]

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

# Ajio sits behind Akamai. Bundled headless Chromium is fingerprinted and blocked
# with "Access Denied", but a real browser channel (Chrome/Edge) passes. We try the
# real channels first and only fall back to bundled Chromium as a last resort.
_BROWSER_CHANNELS: List[Optional[str]] = ["chrome", "msedge", None]


def _abs_ajio_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http"):
        return url
    if url.startswith("/"):
        return "https://assets.ajio.com" + url
    return "https://" + url


def _extract_product_details(html: str) -> Optional[Dict[str, Any]]:
    """
    Extracts the product node from Ajio's Redux state embedded in the PDP HTML
    (window.__PRELOADED_STATE__ -> product.productDetails).
    """
    if not html:
        return None

    idx = html.find("window.__PRELOADED_STATE__")
    if idx != -1:
        start = html.find("{", idx)
        if start != -1:
            try:
                obj, _ = json.JSONDecoder().raw_decode(html[start:])
                product = (obj.get("product") or {}) if isinstance(obj, dict) else {}
                details = product.get("productDetails") if isinstance(product, dict) else None
                if isinstance(details, dict) and (details.get("code") or details.get("name")):
                    return details
            except Exception as e:
                print(f"Ajio: __PRELOADED_STATE__ decode failed: {e!r}")

    return None


def _extract_json_ld_product(html: str) -> Optional[Dict[str, Any]]:
    """
    Fallback: parse a Product / ProductGroup JSON-LD block from the PDP HTML.
    """
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        text = script.string or script.get_text() or ""
        if not text.strip():
            continue
        try:
            data = json.loads(text)
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") in ("Product", "ProductGroup"):
                return item
    return None


def fetch_ajio_product_details(canonical_url: str, code: str, timeout: int = 35000) -> Tuple[Dict[str, Any], str, str]:
    """
    Loads the Ajio PDP in a real browser (to clear Akamai) and returns
    (product_details_dict, source_type, page_html).

    source_type is "state" when the rich Redux product node is found, or
    "json_ld" when only the JSON-LD fallback is available.
    """
    ensure_playwright_event_loop_policy()
    from playwright.sync_api import sync_playwright

    last_err: Optional[Exception] = None

    with sync_playwright() as p:
        for channel in _BROWSER_CHANNELS:
            browser = None
            try:
                launch_kwargs: Dict[str, Any] = {
                    "headless": True,
                    "args": _LAUNCH_ARGS,
                    "ignore_default_args": ["--enable-automation"],
                }
                if channel:
                    launch_kwargs["channel"] = channel
                browser = p.chromium.launch(**launch_kwargs)
            except Exception as e:
                # Channel not installed (e.g. no Chrome/Edge) -> try the next one.
                last_err = e
                continue

            try:
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 768},
                    locale="en-US",
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                )
                context.add_init_script(_STEALTH_SCRIPT)
                page = context.new_page()

                html = ""
                status: Optional[int] = None
                blocked = True
                loaded = False
                # Two attempts: a fresh load, then a reload if Akamai blocked us.
                for _attempt in range(2):
                    try:
                        resp = page.goto(canonical_url, wait_until="domcontentloaded", timeout=timeout)
                        status = resp.status if resp else None
                        loaded = True
                    except Exception as e:
                        last_err = e
                        page.wait_for_timeout(1500)
                        continue

                    page.wait_for_timeout(2200)
                    html = page.content()
                    blocked = "Access Denied" in html or (status is not None and status >= 400)
                    if not blocked:
                        break
                    page.wait_for_timeout(2500)

                details = _extract_product_details(html)
                if details:
                    return details, "state", html

                json_ld = _extract_json_ld_product(html)
                if json_ld:
                    return json_ld, "json_ld", html

                # The page loaded cleanly (not an Akamai block) but carried no product
                # payload -> the product genuinely doesn't exist. Fail fast instead of
                # cycling through the remaining browser channels.
                if loaded and not blocked:
                    is_404 = status == 404 or "PRODUCT NOT FOUND" in html.upper() or "page not found" in html.lower()
                    raise AjioFetcherError(
                        f"Ajio product '{code}' not found or unavailable"
                        + (" (404)" if is_404 else " (no product data on page)")
                    )

                last_err = AjioFetcherError(
                    f"Blocked by Ajio anti-bot via channel={channel or 'chromium'} (status={status})"
                )
            except AjioFetcherError:
                raise
            except Exception as e:
                last_err = e
            finally:
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass

    raise AjioFetcherError(
        f"Could not fetch Ajio product '{code}'. The product may not exist, or Ajio's "
        f"anti-bot protection blocked every browser channel. Last error: {last_err!r}"
    )


class AjioScraper:
    """
    Production-grade Ajio Product Scraper.

    Ajio hides behind Akamai and does not expose its /api/p/ JSON endpoint to
    automated clients, so we load the product page in a real browser channel and
    read the product data embedded in window.__PRELOADED_STATE__.
    """

    @classmethod
    def scrape_url(cls, url_or_code: str) -> ProductData:
        code = extract_ajio_code(url_or_code)
        if not code:
            raise ValueError(f"Could not extract Ajio product code from: '{url_or_code}'")

        canonical_url = normalize_ajio_url(url_or_code)
        details, source_type, _html = fetch_ajio_product_details(canonical_url, code)

        if source_type == "json_ld":
            return cls._parse_json_ld(details, canonical_url, code)
        return cls.parse_product_details(details, canonical_url, code)

    # ------------------------------------------------------------------ #
    # Primary parser: window.__PRELOADED_STATE__ product node             #
    # ------------------------------------------------------------------ #
    @classmethod
    def parse_product_details(cls, data: Dict[str, Any], canonical_url: str, code: str) -> ProductData:
        style_id = data.get("code") or code

        title = (data.get("name") or "").strip() or f"Ajio Product {style_id}"
        brand_name = (data.get("brandName") or "").strip() or "AJIO"

        price_info = cls._parse_price(data)
        media_list, hd_urls = cls._parse_images(data)
        sizes_list, sizes_in_stock = cls._parse_sizes(data)
        color_variants = cls._parse_color(data, style_id)
        specs_list, country_of_origin, manufacturer = cls._parse_features(data)
        desc_sections = cls._parse_description(data, specs_list)
        cat_tree, category, gender = cls._parse_breadcrumbs(data)
        rating_summary = cls._parse_ratings(data)

        # Overall stock: prefer explicit size availability, then product-level stock.
        stock_obj = data.get("stock") or {}
        overall_status = stock_obj.get("stockLevelStatus", "")
        overall_level = int(stock_obj.get("stockLevel") or 0)
        purchasable = data.get("purchasable")
        if sizes_list:
            in_stock = sizes_in_stock
        elif purchasable is not None:
            in_stock = bool(purchasable)
        else:
            in_stock = overall_status == "inStock" or overall_level > 0

        is_returnable = bool(data.get("isReturnable", True))

        seller_info = SellerInfo(
            seller_name=brand_name or "AJIO Official",
            seller_rating=rating_summary.average_rating or 0.0,
            return_period_days=30 if is_returnable else 0,
            is_returnable=is_returnable,
            cod_available=True,
        )
        service_flags = ServiceFlags(
            is_returnable=is_returnable,
            is_exchangeable=is_returnable,
            cod_enabled=True,
            out_of_stock=not in_stock,
        )

        return ProductData(
            platform="ajio",
            style_id=style_id,
            canonical_url=canonical_url,
            product_url=canonical_url,
            title=title,
            subtitle=desc_sections[0]["description"] if desc_sections else None,
            brand=BrandInfo(name=brand_name),
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
            manufacturer=manufacturer,
            country_of_origin=country_of_origin,
            scraped_at=datetime.utcnow().isoformat() + "Z",
        )

    # ------------------------------------------------------------------ #
    # Field parsers                                                       #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_float(val: Any) -> float:
        if val is None:
            return 0.0
        try:
            return float(val)
        except (ValueError, TypeError):
            m = re.search(r"[\d.]+", str(val).replace(",", ""))
            return float(m.group()) if m else 0.0

    @classmethod
    def _parse_price(cls, data: Dict[str, Any]) -> PriceInfo:
        # On Ajio, `price` is the current (selling) price and `wasPriceData` is the MRP.
        price_obj = data.get("price") or {}
        was_obj = data.get("wasPriceData") or {}

        selling = cls._to_float(price_obj.get("value"))
        mrp = cls._to_float(was_obj.get("value"))

        if mrp <= 0:
            mrp = selling
        if selling <= 0:
            selling = mrp

        discount_pct = 0
        raw_pct = price_obj.get("discountValue")
        if raw_pct not in (None, "", "-1", -1):
            try:
                discount_pct = int(round(float(raw_pct)))
            except (ValueError, TypeError):
                discount_pct = 0
        if discount_pct <= 0 and mrp > 0 and selling < mrp:
            discount_pct = int(round((mrp - selling) / mrp * 100))

        return PriceInfo(
            mrp=mrp,
            selling_price=selling,
            discount_amount=max(0.0, mrp - selling),
            discount_percentage=max(0, discount_pct),
            currency=price_obj.get("currencyIso") or was_obj.get("currencyIso") or "INR",
        )

    @classmethod
    def _parse_images(cls, data: Dict[str, Any]) -> Tuple[List[ImageMedia], List[str]]:
        images_raw = data.get("images") or []

        by_format: Dict[str, List[str]] = {}
        ordered_all: List[str] = []
        for img in images_raw:
            if not isinstance(img, dict):
                continue
            url = _abs_ajio_url(img.get("url") or "")
            if not url:
                continue
            fmt = img.get("format") or "default"
            by_format.setdefault(fmt, []).append(url)
            ordered_all.append(url)

        chosen: List[str] = []
        for fmt in _IMAGE_FORMAT_PRIORITY:
            if by_format.get(fmt):
                chosen = by_format[fmt]
                break
        if not chosen:
            chosen = ordered_all

        hd_urls: List[str] = []
        media_list: List[ImageMedia] = []
        for url in chosen:
            if url in hd_urls:
                continue
            hd_urls.append(url)
            media_list.append(ImageMedia(
                image_id=str(len(hd_urls)),
                view_type="default",
                raw_url=url,
                hd_url=url,
            ))
        return media_list, hd_urls

    @classmethod
    def _parse_sizes(cls, data: Dict[str, Any]) -> Tuple[List[SizeOption], bool]:
        sizes_list: List[SizeOption] = []
        any_available = False
        seen = set()

        for vo in data.get("variantOptions") or []:
            if not isinstance(vo, dict):
                continue

            size_label = vo.get("scSize") or vo.get("size")
            if not size_label:
                for q in vo.get("variantOptionQualifiers") or []:
                    if isinstance(q, dict) and q.get("qualifier") == "size" and q.get("value"):
                        size_label = q.get("value")
                        break

            stock = vo.get("stock") or {}
            level = int(stock.get("stockLevel") or 0)
            status = stock.get("stockLevelStatus", "")
            available = level > 0 or status == "inStock"

            label = str(size_label).strip().upper() if size_label else ""
            if not label or label in seen:
                continue
            seen.add(label)
            any_available = any_available or available
            sizes_list.append(SizeOption(
                label=label,
                sku_id=str(vo.get("code", "")),
                available=available,
                inventory=level,
            ))

        return sizes_list, any_available

    @classmethod
    def _parse_color(cls, data: Dict[str, Any], style_id: str) -> List[ColorVariant]:
        color_variants: List[ColorVariant] = []
        seen_codes = set()

        # Strategy A: Parse baseOptions groups for all color variants
        base_options = data.get("baseOptions") or []
        for group in base_options:
            if not isinstance(group, dict):
                continue
            options = group.get("options") or []
            for opt in options:
                if not isinstance(opt, dict):
                    continue
                code = str(opt.get("code") or "")
                c_name = (opt.get("color") or "").strip()
                url_path = opt.get("url") or ""
                
                if code and code not in seen_codes:
                    seen_codes.add(code)
                    model_img = opt.get("modelImage", {}).get("url") if isinstance(opt.get("modelImage"), dict) else None
                    
                    swatch_img = None
                    for q in opt.get("variantOptionQualifiers") or []:
                        if isinstance(q, dict) and isinstance(q.get("swatchImage"), dict) and q["swatchImage"].get("url"):
                            swatch_img = q["swatchImage"]["url"]
                            break
                    
                    final_thumb = swatch_img or model_img
                    clean_url = f"https://www.ajio.com{url_path}" if url_path.startswith("/") else (url_path if url_path.startswith("http") else f"https://www.ajio.com/p/{code}")

                    color_variants.append(ColorVariant(
                        style_id=code,
                        color_name=c_name.title() if c_name else f"Color {code}",
                        image_thumb=final_thumb,
                        product_url=clean_url
                    ))

        # Strategy B: Fallback to fnlColorVariantData
        if not color_variants:
            fcv = data.get("fnlColorVariantData") or {}
            color_name = (fcv.get("color") or fcv.get("colorName") or "").strip()
            if color_name:
                color_variants.append(ColorVariant(style_id=style_id, color_name=color_name))

        return color_variants

    @classmethod
    def _parse_features(cls, data: Dict[str, Any]) -> Tuple[List[Specification], str, str]:
        specs: List[Specification] = []
        country = ""
        manufacturer = ""
        seen = set()

        def add(name: str, value: str):
            name = (name or "").strip()
            value = (value or "").strip()
            if not name or not value:
                return
            key = name.lower()
            if key in seen or key == "product code":
                return
            seen.add(key)
            specs.append(Specification(title=name, value=value))

        for feat in data.get("featureData") or []:
            if not isinstance(feat, dict):
                continue
            fname = feat.get("name", "")
            vals = feat.get("featureValues") or []
            val_str = ", ".join(
                v.get("value", "") for v in vals if isinstance(v, dict) and v.get("value")
            )
            add(fname, val_str)
            low = fname.strip().lower()
            if "country of origin" in low and val_str:
                country = val_str
            if low in ("manufacturer", "manufacturer name", "marketed by", "packed by") and val_str:
                manufacturer = manufacturer or val_str

        return specs, country, manufacturer

    @classmethod
    def _parse_description(cls, data: Dict[str, Any], specs: List[Specification]) -> List[Dict[str, str]]:
        sections: List[Dict[str, str]] = []
        raw = data.get("description") or data.get("summary") or ""
        if raw:
            clean = re.sub(r"<br\s*/?>", "\n", raw)
            clean = BeautifulSoup(clean, "html.parser").get_text().strip()
            if clean:
                sections.append({"title": "Product Details", "description": clean})

        if not sections and specs:
            body = "\n".join(f"• {s.title}: {s.value}" for s in specs[:12])
            if body:
                sections.append({"title": "Product Details", "description": body})
        return sections

    @classmethod
    def _parse_breadcrumbs(cls, data: Dict[str, Any]) -> Tuple[List[str], str, str]:
        cat_tree: List[str] = []
        bc_container = data.get("rilfnlBreadCrumbList") or {}
        crumbs = bc_container.get("rilfnlBreadCrumb") or data.get("breadCrumbs") or []
        for bc in crumbs:
            if isinstance(bc, dict) and bc.get("name"):
                cat_tree.append(bc["name"].strip())

        # Drop the product name itself (usually the last active crumb).
        product_name = (data.get("name") or "").strip()
        if cat_tree and cat_tree[-1] == product_name:
            cat_tree = cat_tree[:-1]

        category = (data.get("brickName") or (cat_tree[-1] if cat_tree else "")).strip()

        gender = (data.get("gender") or "").strip()
        if not gender and cat_tree:
            first = cat_tree[0].lower()
            mapping = {
                "men": "Men", "women": "Women", "boys": "Boys",
                "girls": "Girls", "kids": "Kids", "unisex": "Unisex",
            }
            gender = mapping.get(first, "")

        return cat_tree, category, gender

    @classmethod
    def _parse_ratings(cls, data: Dict[str, Any]) -> RatingSummary:
        rr = data.get("ratingsResponse") or {}
        agg = rr.get("aggregateRating") or {}

        avg = cls._to_float(agg.get("averageRating"))
        count = int(cls._to_float(agg.get("numUserRatings") or agg.get("ratingCount")))
        reviews_count = int(cls._to_float(data.get("numberOfReviews") or agg.get("numReviews") or 0))

        star_breakdown: Dict[str, int] = {}
        for star in range(1, 6):
            pct_raw = agg.get(f"percentage{star}StarRatings")
            if pct_raw and count:
                try:
                    pct = float(str(pct_raw).replace("%", "").strip())
                    star_breakdown[str(star)] = int(round(count * pct / 100))
                except (ValueError, TypeError):
                    pass

        return RatingSummary(
            average_rating=round(avg, 2),
            total_ratings_count=count,
            total_reviews_count=reviews_count,
            star_breakdown=star_breakdown,
        )

    # ------------------------------------------------------------------ #
    # Fallback parser: JSON-LD Product / ProductGroup                     #
    # ------------------------------------------------------------------ #
    @classmethod
    def _parse_json_ld(cls, ld: Dict[str, Any], canonical_url: str, code: str) -> ProductData:
        title = (ld.get("name") or f"Ajio Product {code}").strip()

        brand = ld.get("brand")
        if isinstance(brand, dict):
            brand_name = brand.get("name") or "AJIO"
        else:
            brand_name = brand or "AJIO"

        offers = ld.get("offers") or {}
        if isinstance(offers, list) and offers:
            offers = offers[0]
        selling = cls._to_float(offers.get("price") or offers.get("lowPrice")) if isinstance(offers, dict) else 0.0
        currency = offers.get("priceCurrency", "INR") if isinstance(offers, dict) else "INR"
        availability = str(offers.get("availability", "")) if isinstance(offers, dict) else ""
        in_stock = "InStock" in availability or "in_stock" in availability.lower() or availability == ""

        price_info = PriceInfo(
            mrp=selling, selling_price=selling,
            discount_amount=0.0, discount_percentage=0, currency=currency,
        )

        hd_urls: List[str] = []
        media_list: List[ImageMedia] = []
        imgs = ld.get("image") or []
        if isinstance(imgs, str):
            imgs = [imgs]
        for img in imgs:
            if isinstance(img, str):
                u = _abs_ajio_url(img)
                if u and u not in hd_urls:
                    hd_urls.append(u)
                    media_list.append(ImageMedia(image_id=str(len(hd_urls)), raw_url=u, hd_url=u))

        agg = ld.get("aggregateRating") or {}
        rating_summary = RatingSummary(
            average_rating=round(cls._to_float(agg.get("ratingValue")), 2),
            total_ratings_count=int(cls._to_float(agg.get("ratingCount") or agg.get("reviewCount"))),
        )

        return ProductData(
            platform="ajio",
            style_id=code,
            canonical_url=canonical_url,
            product_url=canonical_url,
            title=title,
            brand=BrandInfo(name=brand_name),
            price=price_info,
            media=media_list,
            hd_image_urls=hd_urls,
            sizes=[],
            in_stock=in_stock,
            ratings=rating_summary,
            seller=SellerInfo(seller_name=brand_name or "AJIO Official"),
            flags=ServiceFlags(out_of_stock=not in_stock),
            scraped_at=datetime.utcnow().isoformat() + "Z",
        )
