import json
import re
from datetime import datetime
from typing import Dict, Any, Optional, List
from bs4 import BeautifulSoup

from .models import (
    ProductData, PriceInfo, BrandInfo, ImageMedia, SizeOption,
    ColorVariant, Specification, RatingSummary, Review, SellerInfo, ServiceFlags
)
from .image_hd import transform_to_hd_image_url
from .url_utils import normalize_myntra_url, extract_style_id
from .fetcher import fetch_myntra_page

class ParsingError(Exception):
    pass

class MyntraScraper:
    """
    Production-grade Myntra product scraper.
    """
    
    @classmethod
    def scrape_url(cls, url_or_id: str) -> ProductData:
        """
        Scrapes a Myntra product given a URL or Style ID.
        """
        style_id = extract_style_id(url_or_id)
        normalized_url = normalize_myntra_url(url_or_id)
        
        html = fetch_myntra_page(normalized_url)
        return cls.parse_html(html, normalized_url, style_id)

    @classmethod
    def parse_html(cls, html: str, source_url: str, default_style_id: Optional[int] = None) -> ProductData:
        """
        Parses raw Myntra product page HTML using window.__myx JSON state with BeautifulSoup fallbacks.
        """
        # Primary strategy: extract window.__myx script object
        myx_data = cls._extract_window_myx(html)
        if myx_data and 'pdpData' in myx_data:
            return cls._parse_pdp_data(myx_data['pdpData'], source_url, default_style_id)
            
        # Fallback strategy: Meta tags and JSON-LD
        return cls._parse_html_meta(html, source_url, default_style_id)

    @classmethod
    def _extract_window_myx(cls, html: str) -> Optional[Dict[str, Any]]:
        """
        Safely locates and extracts the window.__myx JSON structure.
        """
        idx = html.find('window.__myx =')
        if idx == -1:
            idx = html.find('window.__PRELOADED_STATE__ =')
            
        if idx != -1:
            start_idx = html.find('{', idx)
            if start_idx != -1:
                try:
                    dec = json.JSONDecoder()
                    obj, _ = dec.raw_decode(html[start_idx:])
                    return obj
                except Exception as e:
                    print(f"Warning: JSON raw_decode failed: {e}")
                    
        # Alternative regex scan across all <script> tags
        soup = BeautifulSoup(html, 'html.parser')
        for script in soup.find_all('script'):
            text = script.string or ""
            if 'pdpData' in text and ('window.__myx' in text or 'window.__PRELOADED_STATE__' in text or 'pdpData' in text):
                m = re.search(r'pdpData"\s*:\s*({.*?}),\s*"', text, re.DOTALL)
                if m:
                    try:
                        pdp = json.loads(m.group(1))
                        return {'pdpData': pdp}
                    except Exception:
                        pass
        return None

    @classmethod
    def _parse_pdp_data(cls, pdp: Dict[str, Any], source_url: str, default_style_id: Optional[int] = None) -> ProductData:
        """
        Transforms Myntra pdpData dictionary into ProductData domain model.
        """
        style_id = pdp.get('id') or default_style_id or 0
        
        # 1. Product identity
        title = pdp.get('name', 'Myntra Product')
        landing_page = pdp.get('landingPageUrl', '')
        canonical_url = f"https://www.myntra.com/{landing_page}" if landing_page else f"https://www.myntra.com/{style_id}"
        
        # 2. Brand
        brand_raw = pdp.get('brand') or {}
        brand_info = BrandInfo(
            name=brand_raw.get('name', ''),
            uid=str(brand_raw.get('uidx', '')),
            logo_url=brand_raw.get('image'),
            bio=brand_raw.get('bio')
        )
        
        # 3. Pricing
        price_raw = pdp.get('price') or {}
        mrp = float(price_raw.get('mrp') or pdp.get('mrp') or 0.0)
        discounted = float(price_raw.get('discounted') or pdp.get('discounted') or mrp)
        discount_amount = max(0.0, mrp - discounted)
        discount_pct = int(round((discount_amount / mrp) * 100)) if mrp > 0 else 0
        
        price_info = PriceInfo(
            mrp=mrp,
            selling_price=discounted,
            discount_amount=discount_amount,
            discount_percentage=discount_pct,
            currency="INR"
        )
        
        # 4. Images & Media
        media_list: List[ImageMedia] = []
        hd_urls: List[str] = []
        
        albums = pdp.get('media', {}).get('albums', [])
        for album in albums:
            images = album.get('images', [])
            for img in images:
                raw_src = img.get('src') or img.get('imageURL') or img.get('secure_url') or ""
                view_type = img.get('view', 'default')
                if raw_src:
                    hd_url = transform_to_hd_image_url(raw_src)
                    media_list.append(ImageMedia(
                        image_id=str(img.get('id', '')),
                        view_type=view_type,
                        raw_url=raw_src,
                        hd_url=hd_url
                    ))
                    if hd_url not in hd_urls:
                        hd_urls.append(hd_url)

        # 5. Sizes & Inventory
        sizes_list: List[SizeOption] = []
        raw_sizes = pdp.get('sizes') or []
        for s in raw_sizes:
            sizes_list.append(SizeOption(
                label=s.get('label') or s.get('size') or '',
                sku_id=s.get('skuId'),
                available=s.get('available', True),
                inventory=s.get('inventory'),
                seller_id=s.get('sellerId')
            ))
            
        flags_raw = pdp.get('flags') or {}
        out_of_stock = flags_raw.get('outOfStock', False)
        in_stock = not out_of_stock and any(s.available for s in sizes_list) if sizes_list else not out_of_stock

        # 6. Color Variants
        color_variants: List[ColorVariant] = []
        raw_colours = pdp.get('colours') or []
        for c in raw_colours:
            c_id = c.get('styleId')
            if c_id and c_id != style_id:
                color_variants.append(ColorVariant(
                    style_id=c_id,
                    color_name=c.get('colorName') or c.get('label') or '',
                    color_hex=c.get('hexCode'),
                    image_thumb=transform_to_hd_image_url(c.get('image', ''), width=300),
                    product_url=f"https://www.myntra.com/{c_id}"
                ))

        # 7. Specifications & Description Sections
        specs_list: List[Specification] = []
        article_attrs = pdp.get('articleAttributes') or {}
        if isinstance(article_attrs, dict):
            for k, v in article_attrs.items():
                if v:
                    specs_list.append(Specification(title=str(k), value=str(v)))

        desc_sections: List[Dict[str, str]] = []
        raw_details = pdp.get('productDetails') or []
        for det in raw_details:
            sec_title = det.get('title', 'Details')
            sec_desc = det.get('description', '')
            # Clean HTML tags like <br>
            clean_desc = re.sub(r'<br\s*/?>', '\n', sec_desc)
            clean_desc = BeautifulSoup(clean_desc, 'html.parser').get_text().strip()
            if sec_desc:
                desc_sections.append({
                    'title': sec_title,
                    'description': clean_desc
                })

        # 8. Breadcrumbs & Category
        analytics = pdp.get('analytics') or {}
        cat_tree = []
        for cat_key in ['masterCategory', 'subCategory', 'articleType']:
            if analytics.get(cat_key):
                cat_tree.append(analytics[cat_key])
                
        category = analytics.get('articleType', '')
        gender = analytics.get('gender', '')

        # 9. Ratings & Reviews
        ratings_raw = pdp.get('ratings') or {}
        avg_rating = float(ratings_raw.get('averageRating') or 0.0)
        total_ratings = int(ratings_raw.get('totalCount') or 0)
        
        review_info = ratings_raw.get('reviewInfo') or {}
        total_reviews = int(review_info.get('totalReviewsCount') or 0)
        
        star_breakdown = {}
        rating_info = ratings_raw.get('ratingInfo') or {}
        if isinstance(rating_info, dict):
            for star, count in rating_info.items():
                star_breakdown[str(star)] = int(count)

        reviews_list: List[Review] = []
        top_reviews = review_info.get('topReviews') or []
        for r in top_reviews:
            user_photos = [transform_to_hd_image_url(p) for p in r.get('userPhotos', [])]
            reviews_list.append(Review(
                user_name=r.get('userName') or "Verified Buyer",
                rating=int(r.get('userRating') or 5),
                headline=r.get('headline') or "",
                comment=r.get('reviewText') or "",
                date=r.get('createdAt') or "",
                user_photos=user_photos
            ))

        rating_summary = RatingSummary(
            average_rating=round(avg_rating, 2),
            total_ratings_count=total_ratings,
            total_reviews_count=total_reviews,
            star_breakdown=star_breakdown
        )

        # 10. Seller Information & Serviceability
        sel_raw = pdp.get('selectedSeller') or {}
        service_raw = pdp.get('serviceability') or {}
        
        seller_info = SellerInfo(
            seller_id=sel_raw.get('sellerPartnerId'),
            seller_name=sel_raw.get('sellerName', 'Myntra Preferred Seller'),
            seller_rating=float(sel_raw.get('sellerRating') or 4.5),
            return_period_days=int(service_raw.get('returnPeriod') or 14),
            is_returnable=flags_raw.get('isReturnable', True),
            cod_available=flags_raw.get('codEnabled', True)
        )

        service_flags = ServiceFlags(
            is_returnable=flags_raw.get('isReturnable', True),
            is_exchangeable=flags_raw.get('isExchangeable', True),
            cod_enabled=flags_raw.get('codEnabled', True),
            try_and_buy_enabled=flags_raw.get('tryAndBuyEnabled', False),
            out_of_stock=out_of_stock,
            is_fragile=flags_raw.get('isFragile', False),
            is_hazmat=flags_raw.get('isHazmat', False)
        )

        return ProductData(
            platform="myntra",
            style_id=int(style_id) if str(style_id).isdigit() else style_id,
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
            reviews=reviews_list,
            seller=seller_info,
            flags=service_flags,
            manufacturer=str(pdp.get('manufacturer') or ''),
            country_of_origin=str(pdp.get('countryOfOrigin') or ''),
            scraped_at=datetime.utcnow().isoformat() + "Z"
        )

    @classmethod
    def _parse_html_meta(cls, html: str, source_url: str, default_style_id: Optional[int] = None) -> ProductData:
        """
        Fallback parser using BeautifulSoup & OpenGraph / Schema.org meta tags.
        """
        soup = BeautifulSoup(html, 'html.parser')
        style_id = default_style_id or extract_style_id(source_url) or 0
        
        # Meta title & description
        title = ""
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            title = og_title['content']
        elif soup.title:
            title = soup.title.string or ""
            
        brand_name = ""
        brand_meta = soup.find('meta', property='og:brand') or soup.find('meta', property='product:brand')
        if brand_meta and brand_meta.get('content'):
            brand_name = brand_meta['content']

        # Price
        price_val = 0.0
        price_meta = soup.find('meta', property='product:price:amount') or soup.find('meta', property='og:price:amount')
        if price_meta and price_meta.get('content'):
            try:
                price_val = float(price_meta['content'])
            except ValueError:
                pass
                
        # Images
        hd_urls = []
        for meta_img in soup.find_all('meta', property='og:image'):
            if meta_img.get('content'):
                hd_urls.append(transform_to_hd_image_url(meta_img['content']))

        # Validation: If we have no pdpData AND no meta image/og:title, the page is blocked/invalid.
        if not hd_urls and not (og_title and og_title.get('content')):
            raise ParsingError(f"Myntra parsing failed for {source_url}: page HTML contains no pdpData JSON and no product meta tags or images (likely blocked or empty page).")
                
        return ProductData(
            platform="myntra",
            style_id=int(style_id) if str(style_id).isdigit() else style_id,
            canonical_url=source_url,
            product_url=source_url,
            title=title or f"Product {style_id}",
            brand=BrandInfo(name=brand_name),
            price=PriceInfo(mrp=price_val, selling_price=price_val),
            hd_image_urls=hd_urls,
            media=[ImageMedia(raw_url=u, hd_url=u) for u in hd_urls],
            in_stock=True,
            ratings=RatingSummary(),
            seller=SellerInfo(),
            flags=ServiceFlags(),
            scraped_at=datetime.utcnow().isoformat() + "Z"
        )


from .url_utils import detect_platform
from .amazon_scraper import AmazonScraper
from .flipkart_scraper import FlipkartScraper
from .souledstore_scraper import SouledStoreScraper
from .ajio_scraper import AjioScraper
from .nykaafashion_scraper import NykaaFashionScraper
from .nykaa_scraper import NykaaScraper
from .bewakoof_scraper import BewakoofScraper
from .pantaloons_scraper import PantaloonsScraper
from .zara_scraper import ZaraScraper
from .uniqlo_scraper import UniqloScraper
from .shopify_scraper import ShopifyScraper

class UniversalScraper:
    """
    Universal multi-platform product scraper router for Myntra, Amazon, Flipkart, The Souled Store, Ajio, Nykaa Fashion, Nykaa Beauty, Bewakoof, Pantaloons, Zara, Uniqlo, and Shopify stores.
    """
    @classmethod
    def scrape_url(cls, url_or_id: str) -> ProductData:
        platform = detect_platform(url_or_id)
        if platform == "shopify":
            return ShopifyScraper.scrape_url(url_or_id)
        elif platform == "uniqlo":
            return UniqloScraper.scrape_url(url_or_id)
        elif platform == "amazon":
            return AmazonScraper.scrape_url(url_or_id)
        elif platform == "flipkart":
            return FlipkartScraper.scrape_url(url_or_id)
        elif platform == "souledstore":
            return SouledStoreScraper.scrape_url(url_or_id)
        elif platform == "ajio":
            return AjioScraper.scrape_url(url_or_id)
        elif platform == "nykaafashion":
            return NykaaFashionScraper.scrape_url(url_or_id)
        elif platform == "nykaa":
            return NykaaScraper.scrape_url(url_or_id)
        elif platform == "bewakoof":
            return BewakoofScraper.scrape_url(url_or_id)
        elif platform == "pantaloons":
            return PantaloonsScraper.scrape_url(url_or_id)
        elif platform == "zara":
            return ZaraScraper.scrape_url(url_or_id)
        elif platform == "myntra":
            return MyntraScraper.scrape_url(url_or_id)
        else:
            # Dynamic probing fallback for unlisted / unknown domains that might be powered by Shopify
            if isinstance(url_or_id, str) and url_or_id.startswith("http"):
                try:
                    if ShopifyScraper.probe_is_shopify(url_or_id):
                        return ShopifyScraper.scrape_url(url_or_id)
                except Exception:
                    pass
            raise ValueError(f"No custom scraper available for URL: '{url_or_id}' (platform='{platform}')")

def scrape_product(url_or_id: str) -> ProductData:
    """
    Universal top-level helper function to scrape product details from Myntra, Amazon, Flipkart, The Souled Store, Ajio, Nykaa Fashion, Nykaa Beauty, Bewakoof, Pantaloons, Zara, Uniqlo, or Shopify.
    """
    return UniversalScraper.scrape_url(url_or_id)




