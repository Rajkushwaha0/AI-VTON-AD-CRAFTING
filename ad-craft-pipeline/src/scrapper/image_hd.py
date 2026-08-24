import os
import re
import urllib.request
from typing import List

def transform_to_hd_image_url(url: str, width: int = 1080, quality: int = 95) -> str:
    """
    Transforms CDN image URLs from Myntra, Amazon, Flipkart, Ajio, Souled Store, Nykaa, or Bewakoof into clean high-resolution URLs.
    """
    if not url:
        return ""
        
    # --- BEWAKOOF CDN TRANSFORM ---
    if "bewakoof" in url or (not url.startswith("http") and not url.startswith("/") and not url.startswith("//")):
        if not url.startswith("http"):
            filename = url.lstrip("/")
            return f"https://images.bewakoof.com/original/{filename}"
        url = re.sub(r'/t\d+/', '/original/', url)
        url = re.sub(r'/compressed/', '/original/', url)
        return url

    # Ensure HTTPS protocol
    if url.startswith("http://"):
        url = "https://" + url[7:]
    elif url.startswith("//"):
        url = "https:" + url
    elif not url.startswith("http"):
        url = "https://" + url.lstrip("/")

    # --- AMAZON CDN TRANSFORM ---
    if "amazon" in url or "media-amazon.com" in url or "ssl-images-amazon.com" in url:
        url = re.sub(r'\._[A-Z0-9,_\-]+\.(jpg|jpeg|png|webp)', r'.\1', url, flags=re.IGNORECASE)
        return url

    # --- FLIPKART CDN TRANSFORM ---
    if "flixcart.com" in url or "flipkart" in url:
        url = re.sub(r'/image/\d+/\d+/', f'/image/{width}/{width}/', url)
        return url

    # --- NYKAA CDN TRANSFORM ---
    if "nykaa" in url or "nykdesignstudio" in url or "adn-static" in url or "images-static" in url:
        url = re.sub(r'tr:[^/]+/', f'tr:w-1080,q-{quality}/', url)
        return url

    # --- PANTALOONS CDN TRANSFORM ---
    if "pantaloons" in url:
        url = re.sub(r'w=\d+', f'w={width}', url)
        return url

    # --- SHOPIFY CDN TRANSFORM ---
    if "shopify" in url or "cdn/shop/" in url:
        # Strip size suffixes before extension e.g. image_small.jpg -> image.jpg, image_1024x1024.webp -> image.webp
        url = re.sub(r'_(?:pico|icon|thumb|small|compact|medium|large|grande|original|\d+x\d+)(?=\.[a-zA-Z0-9]+(?:\?|$))', '', url)
        # Update or add width param
        if "width=" in url:
            url = re.sub(r'width=\d+', f'width={width}', url)
        elif "?" in url:
            url = url + f"&width={width}"
        else:
            url = url + f"?width={width}"
        return url

    # --- ZARA CDN TRANSFORM ---
    if "zara" in url or "static.zara.net" in url:
        # Replace {width} placeholder or adjust numeric width param (e.g. w=563 -> w=2048)
        if "{width}" in url:
            url = url.replace("{width}", str(width))
        elif "w=" in url:
            url = re.sub(r'w=\d+', f'w={width}', url)
        elif "/w/" in url:
            url = re.sub(r'/w/\d+/', f'/w/{width}/', url)
        return url

    # --- MYNTRA CDN TRANSFORM ---
    # Replace variable placeholder blocks: h_(...),q_(...),w_(...)
    url = re.sub(r'h_\(.*?\),q_\(.*?\),w_\(.*?\)', f'w_{width},q_{quality}', url)
    
    # Clean any leftover individual ($...) tags if formatted differently
    url = re.sub(r'h_\(\$[^)]*\),?', '', url)
    url = re.sub(r'q_\(\$[^)]*\),?', '', url)
    url = re.sub(r'w_\(\$[^)]*\),?', '', url)

    # Replace existing numeric sizing parameters if present (e.g. w_360, q_60)
    if "w_" in url and f"w_{width}" not in url:
        url = re.sub(r'w_\d+', f'w_{width}', url)
    if "q_" in url and f"q_{quality}" not in url:
        url = re.sub(r'q_\d+', f'q_{quality}', url)
        
    return url

def download_product_images(image_urls: List[str], output_dir: str) -> List[str]:
    """
    Downloads product images to local storage directory.
    Returns list of saved file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    saved_paths = []
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'
    }
    
    for idx, img_url in enumerate(image_urls):
        if not img_url:
            continue
        try:
            filename = f"image_{idx + 1}.jpg"
            filepath = os.path.join(output_dir, filename)
            req = urllib.request.Request(img_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                with open(filepath, 'wb') as f:
                    f.write(resp.read())
            saved_paths.append(filepath)
        except Exception as e:
            print(f"Warning: Failed to download image {img_url}: {e}")
            
    return saved_paths
