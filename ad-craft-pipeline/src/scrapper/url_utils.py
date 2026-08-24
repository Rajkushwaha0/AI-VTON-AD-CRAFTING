import re
from typing import Optional, Tuple

def detect_platform(input_str: str) -> str:
    """
    Detects the platform ('myntra', 'amazon', 'flipkart', 'souledstore', 'ajio', 'nykaafashion', 'nykaa', 'bewakoof', 'pantaloons', 'zara', 'uniqlo') from input URL or ID.
    """
    if not input_str:
        return "myntra"
        
    input_lower = input_str.strip().lower()
    
    if "uniqlo" in input_lower or "uniqlo." in input_lower:
        return "uniqlo"
    if "zara" in input_lower or "-p0" in input_lower or "-p1" in input_lower or "-p2" in input_lower or "-p3" in input_lower or "-p4" in input_lower or "-p5" in input_lower or "zara/" in input_lower:
        return "zara"
    if "pantaloons" in input_lower:
        return "pantaloons"
    if "bewakoof" in input_lower:
        return "bewakoof"
    if "nykaafashion" in input_lower:
        return "nykaafashion"
    if "nykaa" in input_lower or "nykaa." in input_lower:
        return "nykaa"
    if "ajio." in input_lower or "ajio" in input_lower or "/p/4" in input_lower:
        return "ajio"
    if "thesouledstore" in input_lower or "souledstore" in input_lower or "tss" in input_lower:
        return "souledstore"
    if "amazon." in input_lower or "amzn.in" in input_lower or "amzn.to" in input_lower:
        return "amazon"
    if "flipkart." in input_lower or "fkrt.it" in input_lower or "pid=" in input_lower:
        return "flipkart"
    if "myntra." in input_lower or "myntra.com" in input_lower:
        return "myntra"

    # Check for known Shopify domains and generic Shopify pattern
    shopify_known_domains = [
        "xyxxcrew", "myshopify", "snitch", "blissclub", "mokobara",
        "vegnonveg", "superkicks", "beyoung.in", "aloyoga", "gymshark",
        "allbirds", "marchtee", "bombaebase", "dcode", "kyliecosmetics"
    ]
    if any(dom in input_lower for dom in shopify_known_domains):
        return "shopify"
    if input_lower.startswith("shopify/"):
        return "shopify"
    if "/products/" in input_lower and "uniqlo" not in input_lower:
        return "shopify"
        
    # Check for Uniqlo product ID format (e.g., E464850-000, E464850, 464850-000)
    if re.search(r'\bE?\d{6}(?:-\d{3})?\b', input_str.strip(), re.IGNORECASE) and "uniqlo" in input_lower:
        return "uniqlo"

    # Check for Ajio code format (digits_color e.g. 466723223_grey or 466723223)
    if re.search(r'\b\d{9}_[a-z0-9]+\b', input_lower):
        return "ajio"

    # Check for Amazon ASIN format (10-char alphanumeric starting with B0...)
    if re.search(r'\b(B[0-9A-Z]{9})\b', input_str.strip()):
        return "amazon"
        
    # Default to Myntra only for purely numeric style IDs
    if input_str.strip().isdigit():
        return "myntra"

    return "unknown"

def extract_style_id(input_str: str) -> Optional[int]:
    """
    Extracts Myntra numeric Style ID from a URL, short code, or raw ID string.
    """
    if not input_str:
        return None
        
    input_str = input_str.strip()
    
    if input_str.isdigit():
        return int(input_str)
        
    matches = re.findall(r'(\d{5,10})', input_str)
    if matches:
        buy_match = re.search(r'(\d{5,10})/(?:buy)?(?:\?|$)', input_str)
        if buy_match:
            return int(buy_match.group(1))
        return int(matches[-1])
        
    return None

def normalize_myntra_url(input_str: str) -> str:
    """
    Normalizes any Myntra product link or style ID into a clean Myntra URL.
    Preserves full PDP URL paths if provided, or converts raw style IDs into canonical URLs.
    """
    if not input_str:
        raise ValueError("Empty Myntra URL or Style ID")

    input_str = input_str.strip()

    if input_str.startswith("http://") or input_str.startswith("https://"):
        return input_str.split("?")[0]

    if "myntra.com" in input_str:
        clean = input_str.split("?")[0]
        if not clean.startswith("http"):
            clean = "https://" + clean
        return clean

    style_id = extract_style_id(input_str)
    if style_id:
        return f"https://www.myntra.com/{style_id}"

    raise ValueError(f"Invalid Myntra URL or Style ID: '{input_str}'")


def extract_amazon_asin(input_str: str) -> Optional[str]:
    """
    Extracts Amazon 10-character ASIN from URL or raw ASIN string.
    Examples:
    - "https://www.amazon.in/dp/B08X6VF3M7" -> "B08X6VF3M7"
    - "https://www.amazon.in/product-title/dp/B08X6VF3M7/ref=..." -> "B08X6VF3M7"
    - "B08X6VF3M7" -> "B08X6VF3M7"
    """
    if not input_str:
        return None
        
    s = input_str.strip()
    
    dp_match = re.search(r'/(?:dp|gp/product|ASIN)/([A-Z0-9]{10})', s, re.IGNORECASE)
    if dp_match:
        return dp_match.group(1).upper()
        
    asin_match = re.search(r'\b([B0-9][A-Z0-9]{9})\b', s, re.IGNORECASE)
    if asin_match:
        return asin_match.group(1).upper()
        
    return None

def normalize_amazon_url(input_str: str) -> str:
    """
    Normalizes Amazon product link or ASIN into clean canonical URL.
    Preserves original domain (e.g. amazon.com, amazon.in, amazon.co.uk) if provided in input.
    """
    if not input_str:
        raise ValueError("Empty Amazon URL or ASIN provided")
        
    s = input_str.strip()
    
    # Extract domain if full URL provided
    domain = "https://www.amazon.in"
    m_domain = re.search(r'(https?://(?:www\.)?amazon\.[a-z.]+)', s, re.IGNORECASE)
    if m_domain:
        domain = m_domain.group(1)
        if not domain.startswith("http"):
            domain = "https://" + domain.lstrip("/")

    asin = extract_amazon_asin(s)
    if asin:
        return f"{domain}/dp/{asin}"
        
    if s.startswith("http://") or s.startswith("https://"):
        return s.split("?")[0]
        
    if "amazon." in s:
        clean = s.split("?")[0]
        if not clean.startswith("http"):
            clean = "https://" + clean
        return clean
        
    raise ValueError(f"Invalid Amazon URL or ASIN: '{input_str}'")

def extract_flipkart_pid(input_str: str) -> Optional[str]:
    """
    Extracts Flipkart Product ID (PID) from URL or raw string.
    Examples:
    - "https://www.flipkart.com/tshirt/p/itm123456?pid=TSHGHYZ789" -> "TSHGHYZ789"
    - "https://www.flipkart.com/item/p/itm123456" -> "itm123456"
    """
    if not input_str:
        return None
        
    s = input_str.strip()
    
    pid_match = re.search(r'[?&]pid=([A-Za-z0-9]+)', s)
    if pid_match:
        return pid_match.group(1)
        
    itm_match = re.search(r'/p/(itm[A-Za-z0-9]+)', s)
    if itm_match:
        return itm_match.group(1)
        
    return None

def normalize_flipkart_url(input_str: str) -> str:
    """
    Normalizes Flipkart product link or PID into clean URL.
    """
    pid = extract_flipkart_pid(input_str)
    s = input_str.strip()
    
    if s.startswith("http://") or s.startswith("https://"):
        return s
        
    if "flipkart.com" in s:
        if not s.startswith("http"):
            return "https://" + s
        return s
        
    if pid:
        return f"https://www.flipkart.com/product/p/itm?pid={pid}"
        
    raise ValueError(f"Invalid Flipkart URL or PID: '{input_str}'")

def extract_souledstore_slug(input_str: str) -> Optional[str]:
    """
    Extracts The Souled Store product slug from URL or raw slug string.
    Examples:
    - "https://www.thesouledstore.com/product/oversized-tshirt-mechasoul-men?gte=1" -> "oversized-tshirt-mechasoul-men"
    - "https://www.thesouledstore.com/product/marvel-spidey-hulk-men-oversized-t-shirts" -> "marvel-spidey-hulk-men-oversized-t-shirts"
    - "oversized-tshirt-mechasoul-men" -> "oversized-tshirt-mechasoul-men"
    """
    if not input_str:
        return None
        
    s = input_str.strip()
    
    # Check for URL pattern /product/<slug> or /public/product/<slug>
    slug_match = re.search(r'/(?:public/)?product/([^/?#]+)', s)
    if slug_match:
        return slug_match.group(1)
        
    if not s.startswith("http://") and not s.startswith("https://") and "/" not in s and "." not in s:
        return s
        
    return None

def normalize_souledstore_url(input_str: str) -> str:
    """
    Normalizes The Souled Store product link or slug into a clean canonical URL.
    """
    slug = extract_souledstore_slug(input_str)
    if slug:
        return f"https://www.thesouledstore.com/product/{slug}"
        
    s = input_str.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s.split("?")[0]
        
    if "thesouledstore.com" in s or "souledstore.com" in s:
        clean = s.split("?")[0]
        if not clean.startswith("http"):
            clean = "https://" + clean
        return clean
        
    raise ValueError(f"Invalid The Souled Store URL or slug: '{input_str}'")

def extract_ajio_code(input_str: str) -> Optional[str]:
    """
    Extracts Ajio product code from URL or raw code string.
    Examples:
    - "https://www.ajio.com/bullmer-printed-oversized-fit-drop-shoulder-t-shirt/p/466723223_grey" -> "466723223_grey"
    - "https://www.ajio.com/p/466723223_grey" -> "466723223_grey"
    - "466723223_grey" -> "466723223_grey"
    """
    if not input_str:
        return None
        
    s = input_str.strip()
    
    code_match = re.search(r'/p/([A-Za-z0-9_]+)', s)
    if code_match:
        return code_match.group(1)
        
    if re.search(r'\b\d{9}_[A-Za-z0-9]+\b', s):
        m = re.search(r'\b(\d{9}_[A-Za-z0-9]+)\b', s)
        if m:
            return m.group(1)
            
    if not s.startswith("http://") and not s.startswith("https://") and "/" not in s and "." not in s:
        return s
        
    return None

def normalize_ajio_url(input_str: str) -> str:
    """
    Normalizes Ajio product link or code into clean canonical URL.
    """
    code = extract_ajio_code(input_str)
    if code:
        return f"https://www.ajio.com/p/{code}"
        
    s = input_str.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s.split("?")[0]
        
    if "ajio.com" in s:
        clean = s.split("?")[0]
        if not clean.startswith("http"):
            clean = "https://" + clean
        return clean
        
    raise ValueError(f"Invalid Ajio URL or product code: '{input_str}'")

def extract_nykaa_id(input_str: str) -> Optional[str]:
    """
    Extracts Nykaa Fashion product ID from URL or raw ID string.
    Examples:
    - "https://www.nykaafashion.com/aravalii-deep-purple-solid-kurta/p/16802995" -> "16802995"
    - "https://www.nykaafashion.com/p/16802995" -> "16802995"
    - "16802995" -> "16802995"
    """
    if not input_str:
        return None
        
    s = input_str.strip()
    
    # Match /p/<id> in URL
    p_match = re.search(r'/p/(\d+)', s)
    if p_match:
        return p_match.group(1)
        
    # Match productId=<id> or optionId=<id> in query params
    query_match = re.search(r'[?&](?:productId|optionId|skuId)=(\d+)', s)
    if query_match:
        return query_match.group(1)
        
    if s.isdigit():
        return s
        
    digits = re.findall(r'(\d{5,10})', s)
    if digits:
        return digits[-1]
        
    return None

def normalize_nykaa_url(input_str: str) -> str:
    """
    Normalizes Nykaa Fashion product link or ID into a clean canonical URL.
    """
    nykaa_id = extract_nykaa_id(input_str)
    s = input_str.strip()
    
    if s.startswith("http://") or s.startswith("https://"):
        return s
        
    if "nykaafashion.com" in s or "nykaa.com" in s:
        clean = s
        if not clean.startswith("http"):
            clean = "https://" + clean
        return clean
        
    if nykaa_id:
        return f"https://www.nykaafashion.com/p/{nykaa_id}"
        
    raise ValueError(f"Invalid Nykaa Fashion URL or ID: '{input_str}'")

def extract_beauty_nykaa_id(input_str: str) -> Optional[str]:
    """
    Extracts Nykaa Beauty product ID from URL or raw ID string.
    Examples:
    - "https://www.nykaa.com/kay-beauty-matte-action-lip-liner/p/561339" -> "561339"
    - "https://www.nykaa.com/p/561339" -> "561339"
    - "561339" -> "561339"
    """
    if not input_str:
        return None
        
    s = input_str.strip()
    
    # Match /p/<id> in URL
    p_match = re.search(r'/p/(\d+)', s)
    if p_match:
        return p_match.group(1)
        
    # Match skuId=<id> or productId=<id> in query params
    query_match = re.search(r'[?&](?:productId|skuId|ptype)=(\d+)', s)
    if query_match:
        return query_match.group(1)
        
    if s.isdigit():
        return s
        
    digits = re.findall(r'(\d{5,10})', s)
    if digits:
        return digits[-1]
        
    return None

def normalize_nykaa_beauty_url(input_str: str) -> str:
    """
    Normalizes Nykaa Beauty product link or ID into a clean canonical URL.
    """
    nykaa_id = extract_beauty_nykaa_id(input_str)
    s = input_str.strip()
    
    if s.startswith("http://") or s.startswith("https://"):
        return s
        
    if "nykaa.com" in s and "nykaafashion" not in s:
        clean = s
        if not clean.startswith("http"):
            clean = "https://" + clean
        return clean
        
    if nykaa_id:
        return f"https://www.nykaa.com/p/{nykaa_id}"
        
    raise ValueError(f"Invalid Nykaa Beauty URL or ID: '{input_str}'")

def extract_bewakoof_slug_or_id(input_str: str) -> Optional[str]:
    """
    Extracts Bewakoof product slug or numeric ID from URL or raw string.
    Examples:
    - "https://www.bewakoof.com/p/men-beast-within-3-0-oversized-fit-t-shirt" -> "men-beast-within-3-0-oversized-fit-t-shirt"
    - "https://www.bewakoof.com/p/620155" -> "620155"
    - "bewakoof/620155" -> "620155"
    - "620155" -> "620155"
    """
    if not input_str:
        return None
        
    s = input_str.strip()
    
    p_match = re.search(r'/p/([^/?#]+)', s)
    if p_match:
        return p_match.group(1)
        
    if s.lower().startswith("bewakoof/"):
        return s[9:].strip()
        
    if not s.startswith("http://") and not s.startswith("https://") and "/" not in s and "." not in s:
        return s
        
    return None

def normalize_bewakoof_url(input_str: str) -> str:
    """
    Normalizes Bewakoof product link or slug/ID into a clean canonical URL.
    """
    slug = extract_bewakoof_slug_or_id(input_str)
    if slug:
        return f"https://www.bewakoof.com/p/{slug}"

    s = input_str.strip()
    
    if s.startswith("http://") or s.startswith("https://"):
        return s.split("?")[0]
        
    if "bewakoof.com" in s:
        clean = s.split("?")[0]
        if not clean.startswith("http"):
            clean = "https://" + clean
        return clean
        
    raise ValueError(f"Invalid Bewakoof URL or product slug/ID: '{input_str}'")

def extract_pantaloons_id(input_str: str) -> Optional[str]:
    """
    Extracts Pantaloons product ID from URL or raw ID string.
    Examples:
    - "https://www.pantaloons.com/p/black-solid-full-length-casual-men-slim-fit-jeans-1157625" -> "1157625"
    - "https://www.pantaloons.com/p/1157625" -> "1157625"
    - "pantaloons/1157625" -> "1157625"
    - "1157625" -> "1157625"
    """
    if not input_str:
        return None
        
    s = input_str.strip()
    
    # Extract trailing ID from /p/slug-123456 or /p/123456
    p_match = re.search(r'/p/(?:.*?-)?(\d+)(?:[?#.]|$)', s)
    if p_match:
        return p_match.group(1)
        
    query_match = re.search(r'[?&](?:productId|id)=(\d+)', s)
    if query_match:
        return query_match.group(1)
        
    if s.isdigit():
        return s
        
    if s.lower().startswith("pantaloons/"):
        val = s[11:].strip()
        if val.isdigit():
            return val
            
    digits = re.findall(r'(\d{5,10})', s)
    if digits:
        return digits[-1]
        
    return None

def normalize_pantaloons_url(input_str: str) -> str:
    """
    Normalizes Pantaloons product link or ID into a clean canonical URL.
    """
    s = input_str.strip()
    
    if s.startswith("http://") or s.startswith("https://"):
        return s.split("?")[0]
        
    if "pantaloons.com" in s:
        clean = s.split("?")[0]
        if not clean.startswith("http"):
            clean = "https://" + clean
        return clean
        
    pid = extract_pantaloons_id(s)
    if pid:
        return f"https://www.pantaloons.com/p/{pid}"
        
    raise ValueError(f"Invalid Pantaloons URL or product ID: '{input_str}'")

def extract_zara_id(input_str: str) -> Optional[str]:
    """
    Extracts Zara product ID, reference ID, or variant ID (v1) from URL or raw ID string.
    Examples:
    - "https://www.zara.com/in/en/relaxed-fit-flowing-shirt-p01437336.html?v1=545451447" -> "01437336" (or v1 variant "545451447")
    - "https://www.zara.com/in/en/product-title-p03433301.html" -> "03433301"
    - "zara/01437336" -> "01437336"
    - "01437336" -> "01437336"
    """
    if not input_str:
        return None
        
    s = input_str.strip()
    
    # Check for v1 variant ID in query string
    v1_match = re.search(r'[?&]v1=(\d+)', s)
    if v1_match:
        return v1_match.group(1)
        
    # Check for -p<id>.html pattern
    p_match = re.search(r'-p([0-9a-zA-Z]+)(?:\.html|$)', s)
    if p_match:
        return p_match.group(1)
        
    # Check for /p/ or /product/ or /id/ in URL
    id_match = re.search(r'/(?:p|product|id)/([0-9a-zA-Z]+)', s)
    if id_match:
        return id_match.group(1)
        
    if s.lower().startswith("zara/"):
        return s[5:].strip()
        
    if s.isdigit():
        return s
        
    digits = re.findall(r'(\d{5,10})', s)
    if digits:
        return digits[-1]
        
    return None

def normalize_zara_url(input_str: str) -> str:
    """
    Normalizes Zara product link or ID into a clean canonical URL.
    """
    if not input_str:
        raise ValueError("Empty Zara URL or ID provided")
        
    s = input_str.strip()
    
    if s.startswith("http://") or s.startswith("https://"):
        return s
        
    if "zara.com" in s:
        clean = s
        if not clean.startswith("http"):
            clean = "https://" + clean
        return clean
        
    zid = extract_zara_id(s)
    if zid:
        return f"https://www.zara.com/in/en/product-p{zid}.html"
        
    raise ValueError(f"Invalid Zara URL or product ID: '{input_str}'")

def extract_uniqlo_id(input_str: str) -> Optional[str]:
    """
    Extracts Uniqlo product ID (e.g. E464850-000, E464850, 464850) from URL or ID string.
    Examples:
    - "https://www.uniqlo.com/in/en/products/E464850-000/00?colorCode=COL09" -> "E464850-000"
    - "https://www.uniqlo.com/us/en/products/E464850-000" -> "E464850-000"
    - "https://www.uniqlo.com/in/en/products/464850" -> "464850"
    - "E464850-000" -> "E464850-000"
    - "464850" -> "464850"
    """
    if not input_str:
        return None
        
    s = input_str.strip()
    
    if s.lower().startswith("uniqlo/"):
        s = s[7:].strip()

    # Pattern 1: E-code with suffix e.g., E464850-000
    e_code_suffix = re.search(r'\b(E\d{6}-\d{3})\b', s, re.IGNORECASE)
    if e_code_suffix:
        return e_code_suffix.group(1).upper()

    # Pattern 2: E-code without suffix e.g., E464850
    e_code = re.search(r'\b(E\d{6})\b', s, re.IGNORECASE)
    if e_code:
        return e_code.group(1).upper()

    # Pattern 3: Standard product ID in URL path e.g. /products/464850-000 or /products/464850
    path_match = re.search(r'/products/([A-Za-z0-9-]+?)(?:/\d{2}|[?#]|$)', s)
    if path_match:
        pid = path_match.group(1).strip('/')
        # Clean trailing /00 sub-code if attached
        if '/' in pid:
            pid = pid.split('/')[0]
        return pid.upper()

    # Pattern 4: Raw numeric ID with dash e.g. 464850-000
    dash_digits = re.search(r'\b(\d{6}-\d{3})\b', s)
    if dash_digits:
        return dash_digits.group(1)

    # Pattern 5: 6-digit numeric product ID e.g., 464850
    six_digits = re.search(r'\b(\d{6})\b', s)
    if six_digits:
        return six_digits.group(1)

    return None

def normalize_uniqlo_url(input_str: str) -> str:
    """
    Normalizes any Uniqlo product link or ID into a canonical Uniqlo URL while preserving region if present.
    """
    if not input_str:
        raise ValueError("Empty Uniqlo URL or ID provided")
        
    s = input_str.strip()
    
    # Extract region and language from URL if available (e.g. /in/en/, /us/en/, /jp/ja/)
    region = "in/en"
    region_match = re.search(r'uniqlo\.com/([a-z]{2}/[a-z]{2})', s, re.IGNORECASE)
    if region_match:
        region = region_match.group(1).lower()

    if s.startswith("http://") or s.startswith("https://"):
        return s.split("?")[0]
        
    if "uniqlo.com" in s:
        clean = s.split("?")[0]
        if not clean.startswith("http"):
            clean = "https://" + clean
        return clean
        
    uid = extract_uniqlo_id(s)
    if uid:
        return f"https://www.uniqlo.com/{region}/products/{uid}"
        
    raise ValueError(f"Invalid Uniqlo URL or product ID: '{input_str}'")


def extract_shopify_handle(input_str: str) -> Optional[str]:
    """
    Extracts Shopify product handle (slug) from a URL or raw handle string.
    Examples:
    - "https://xyxxcrew.com/products/mens-tactel-trunks-ash-grey" -> "mens-tactel-trunks-ash-grey"
    - "https://snitch.co.in/products/washed-straight-fit-jeans-4je084-03?variant=123" -> "washed-straight-fit-jeans-4je084-03"
    - "shopify/mens-tactel-trunks-ash-grey" -> "mens-tactel-trunks-ash-grey"
    - "mens-tactel-trunks-ash-grey" -> "mens-tactel-trunks-ash-grey"
    """
    if not input_str:
        return None

    s = input_str.strip()
    if s.lower().startswith("shopify/"):
        s = s[8:].strip()

    # Match /products/<handle> in URL
    m = re.search(r'/products/([a-zA-Z0-9_\-\.]+?)(?:[?#/\.]|$)', s)
    if m:
        handle = m.group(1).rstrip('/')
        # Remove trailing .js / .json if present
        if handle.endswith('.js'):
            handle = handle[:-3]
        elif handle.endswith('.json'):
            handle = handle[:-5]
        return handle

    # If it's a URL but handle is at end
    if s.startswith("http://") or s.startswith("https://"):
        clean_path = s.split('?')[0].rstrip('/')
        parts = clean_path.split('/')
        if parts:
            return parts[-1]

    # Raw handle slug e.g. "mens-tactel-trunks-ash-grey"
    if re.match(r'^[a-zA-Z0-9_\-]+$', s):
        return s

    return None


def normalize_shopify_url(input_str: str) -> str:
    """
    Normalizes any Shopify product link or handle into a clean canonical URL.
    Preserves original domain if full URL provided.
    """
    if not input_str:
        raise ValueError("Empty Shopify URL or handle provided")

    s = input_str.strip()
    if s.lower().startswith("shopify/"):
        s = s[8:].strip()

    # If full HTTP URL, clean query params but preserve domain and path
    if s.startswith("http://") or s.startswith("https://"):
        clean_url = s.split('?')[0].rstrip('/')
        if not clean_url.startswith("https://"):
            clean_url = "https://" + clean_url[7:]
        # Remove trailing .js or .json if present in input URL
        if clean_url.endswith('.js'):
            clean_url = clean_url[:-3]
        elif clean_url.endswith('.json'):
            clean_url = clean_url[:-5]
        return clean_url

    # If domain without scheme
    if any(s.startswith(dom) for dom in ["xyxxcrew.com", "snitch.co.in", "blissclub.com", "myshopify.com"]):
        clean = s.split('?')[0].rstrip('/')
        return f"https://{clean}"

    handle = extract_shopify_handle(s)
    if handle:
        return f"https://xyxxcrew.com/products/{handle}"

    raise ValueError(f"Invalid Shopify URL or handle: '{input_str}'")





