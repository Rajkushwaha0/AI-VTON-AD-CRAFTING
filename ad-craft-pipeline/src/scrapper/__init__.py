"""
Multi-Platform E-Commerce Product Scraper Engine (Myntra, Amazon, Flipkart, The Souled Store, Ajio, Nykaa Fashion, Nykaa Beauty, Bewakoof, Pantaloons, Zara, Uniqlo)
"""
from .parser import MyntraScraper, UniversalScraper, scrape_product
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
from .models import ProductData
from .url_utils import (
    detect_platform,
    normalize_myntra_url, extract_style_id,
    normalize_amazon_url, extract_amazon_asin,
    normalize_flipkart_url, extract_flipkart_pid,
    normalize_souledstore_url, extract_souledstore_slug,
    normalize_ajio_url, extract_ajio_code,
    normalize_nykaa_url, extract_nykaa_id,
    normalize_nykaa_beauty_url, extract_beauty_nykaa_id,
    normalize_bewakoof_url, extract_bewakoof_slug_or_id,
    normalize_pantaloons_url, extract_pantaloons_id,
    normalize_zara_url, extract_zara_id,
    normalize_uniqlo_url, extract_uniqlo_id,
    normalize_shopify_url, extract_shopify_handle
)

__all__ = [
    "UniversalScraper",
    "scrape_product",
    "MyntraScraper",
    "AmazonScraper",
    "FlipkartScraper",
    "SouledStoreScraper",
    "AjioScraper",
    "NykaaFashionScraper",
    "NykaaScraper",
    "BewakoofScraper",
    "PantaloonsScraper",
    "ZaraScraper",
    "UniqloScraper",
    "ShopifyScraper",
    "ProductData",
    "detect_platform",
    "normalize_myntra_url",
    "extract_style_id",
    "normalize_amazon_url",
    "extract_amazon_asin",
    "normalize_flipkart_url",
    "extract_flipkart_pid",
    "normalize_souledstore_url",
    "extract_souledstore_slug",
    "normalize_ajio_url",
    "extract_ajio_code",
    "normalize_nykaa_url",
    "extract_nykaa_id",
    "normalize_nykaa_beauty_url",
    "extract_beauty_nykaa_id",
    "normalize_bewakoof_url",
    "extract_bewakoof_slug_or_id",
    "normalize_pantaloons_url",
    "extract_pantaloons_id",
    "normalize_zara_url",
    "extract_zara_id",
    "normalize_uniqlo_url",
    "extract_uniqlo_id",
    "normalize_shopify_url",
    "extract_shopify_handle"
]






