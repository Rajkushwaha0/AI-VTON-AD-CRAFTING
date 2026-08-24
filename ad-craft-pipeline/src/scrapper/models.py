from typing import List, Dict, Optional, Any, Union
from pydantic import BaseModel, Field

class PriceInfo(BaseModel):
    mrp: float = Field(0.0, description="Maximum Retail Price")
    selling_price: float = Field(0.0, description="Actual selling / discounted price")
    discount_amount: float = Field(0.0, description="Discount amount saved")
    discount_percentage: int = Field(0, description="Percentage of discount")
    currency: str = Field("INR", description="Currency symbol/code")

class BrandInfo(BaseModel):
    name: str = ""
    uid: str = ""
    logo_url: Optional[str] = None
    bio: Optional[str] = None

class ImageMedia(BaseModel):
    image_id: Optional[str] = None
    view_type: str = "default" # e.g. front, back, left, right, detail, model
    raw_url: str = ""
    hd_url: str = ""

class SizeOption(BaseModel):
    label: str = ""
    sku_id: Optional[Union[int, str]] = None
    available: bool = True
    inventory: Optional[int] = None
    seller_id: Optional[Union[int, str]] = None

class ColorVariant(BaseModel):
    style_id: Union[int, str]
    color_name: str = ""
    color_hex: Optional[str] = None
    image_thumb: Optional[str] = None
    product_url: str = ""

class Specification(BaseModel):
    title: str = ""
    value: str = ""

class RatingSummary(BaseModel):
    average_rating: float = 0.0
    total_ratings_count: int = 0
    total_reviews_count: int = 0
    star_breakdown: Dict[str, int] = Field(default_factory=dict) # e.g. {"5": 120, "4": 30, ...}

class Review(BaseModel):
    user_name: str = "Verified Customer"
    rating: int = 5
    headline: str = ""
    comment: str = ""
    date: str = ""
    user_photos: List[str] = Field(default_factory=list)

class SellerInfo(BaseModel):
    seller_id: Optional[int] = None
    seller_name: str = ""
    seller_rating: float = 0.0
    return_period_days: int = 14
    is_returnable: bool = True
    cod_available: bool = True

class ServiceFlags(BaseModel):
    is_returnable: bool = True
    is_exchangeable: bool = True
    cod_enabled: bool = True
    try_and_buy_enabled: bool = False
    out_of_stock: bool = False
    is_fragile: bool = False
    is_hazmat: bool = False

class ProductData(BaseModel):
    platform: str = "myntra" # myntra, amazon, flipkart, souledstore, ajio, nykaafashion, nykaa, bewakoof, pantaloons, zara, uniqlo
    style_id: Union[int, str]
    canonical_url: str
    product_url: str
    title: str
    subtitle: Optional[str] = None
    brand: BrandInfo
    price: PriceInfo
    media: List[ImageMedia] = Field(default_factory=list)
    hd_image_urls: List[str] = Field(default_factory=list)
    sizes: List[SizeOption] = Field(default_factory=list)
    in_stock: bool = True
    color_variants: List[ColorVariant] = Field(default_factory=list)
    specifications: List[Specification] = Field(default_factory=list)
    product_description_sections: List[Dict[str, str]] = Field(default_factory=list)
    breadcrumbs: List[str] = Field(default_factory=list)
    category: str = ""
    gender: str = ""
    ratings: RatingSummary
    reviews: List[Review] = Field(default_factory=list)
    seller: SellerInfo
    flags: ServiceFlags
    manufacturer: str = ""
    country_of_origin: str = ""
    scraped_at: str = ""
