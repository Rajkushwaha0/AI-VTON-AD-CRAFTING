import io
import os
from typing import Tuple, Union, Optional
from PIL import Image

try:
    import cv2
    import numpy as np
    HAS_CV2_NUMPY = True
except ImportError:
    HAS_CV2_NUMPY = False

ALLOWED_IMAGE_ASPECT_RATIOS: tuple[str, ...] = ("9:16", "1:1", "16:9", "3:4", "4:3")

def normalize_aspect_ratio(aspect_ratio: Optional[str], default: str = "9:16") -> str:
    """Normalizes and validates aspect ratio string."""
    ar = (aspect_ratio or default or "9:16").strip()
    if ar in ALLOWED_IMAGE_ASPECT_RATIOS:
        return ar
    # Match standard approximations
    if ar in ("vertical", "portrait", "story", "reels", "9/16"):
        return "9:16"
    if ar in ("square", "feed", "1/1"):
        return "1:1"
    if ar in ("horizontal", "landscape", "16/9"):
        return "16:9"
    return default if default in ALLOWED_IMAGE_ASPECT_RATIOS else "9:16"

def aspect_ratio_to_wh(aspect_ratio: str = "9:16") -> Tuple[int, int]:
    """Returns (width_ratio, height_ratio) integer tuple."""
    normalized = normalize_aspect_ratio(aspect_ratio)
    parts = normalized.split(":")
    if len(parts) == 2:
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            pass
    return 9, 16

def _to_pil_image(image_input: Union[bytes, str, Image.Image]) -> Image.Image:
    if isinstance(image_input, Image.Image):
        return image_input.convert("RGB")
    if isinstance(image_input, str):
        if os.path.exists(image_input):
            return Image.open(image_input).convert("RGB")
        raise FileNotFoundError(f"Image path not found: {image_input}")
    if isinstance(image_input, (bytes, bytearray)):
        return Image.open(io.BytesIO(image_input)).convert("RGB")
    raise TypeError(f"Unsupported image input type: {type(image_input)}")

def enforce_aspect_ratio(
    image_input: Union[bytes, str, Image.Image],
    target_ratio: str = "9:16"
) -> bytes:
    """
    Enforces exact target aspect ratio via center cropping without distortion.
    Uses OpenCV if available, or pure PIL.
    Returns encoded PNG bytes.
    """
    rw, rh = aspect_ratio_to_wh(target_ratio)
    target = rw / rh

    if HAS_CV2_NUMPY:
        try:
            import cv2
            import numpy as np
            if isinstance(image_input, np.ndarray):
                img = image_input
            elif isinstance(image_input, str):
                img = cv2.imread(image_input, cv2.IMREAD_COLOR)
            elif isinstance(image_input, (bytes, bytearray)):
                nparr = np.frombuffer(image_input, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            elif isinstance(image_input, Image.Image):
                img = cv2.cvtColor(np.array(image_input.convert("RGB")), cv2.COLOR_RGB2BGR)
            else:
                img = None

            if img is not None:
                h, w, _ = img.shape
                current = w / h
                if abs(current - target) > 1e-3:
                    if current > target:
                        new_w = int(round(h * target))
                        left = (w - new_w) // 2
                        img = img[:, left:left + new_w]
                    else:
                        new_h = int(round(w / target))
                        top = (h - new_h) // 2
                        img = img[top:top + new_h, :]
                _, encoded = cv2.imencode(".png", img)
                return encoded.tobytes()
        except Exception:
            pass

    # Pure PIL Fallback (always works)
    pil_img = _to_pil_image(image_input)
    w, h = pil_img.size
    current = w / h
    if abs(current - target) > 1e-3:
        if current > target:
            new_w = int(round(h * target))
            left = (w - new_w) // 2
            pil_img = pil_img.crop((left, 0, left + new_w, h))
        else:
            new_h = int(round(w / target))
            top = (h - new_h) // 2
            pil_img = pil_img.crop((0, top, w, top + new_h))

    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    return buf.getvalue()

def extract_face_crop(
    image_input: Union[bytes, str, Image.Image],
    min_size_px: int = 640
) -> bytes:
    """
    Extracts upper body / face region and applies Lanczos upscaling for forensic QA inspection.
    """
    img = _to_pil_image(image_input)
    w, h = img.size
    
    # Heuristic top-band crop (top 38% height, centered 58% width)
    crop_h = max(1, int(h * 0.38))
    crop_w = max(1, int(w * 0.58))
    left = max(0, (w - crop_w) // 2)
    cropped = img.crop((left, 0, left + crop_w, crop_h))
    
    # Upscale with Lanczos interpolation if lower than threshold to reveal micro pores and facial geometry
    if min(cropped.size) < min_size_px:
        scale = min_size_px / max(1, min(cropped.size))
        new_w = max(1, int(cropped.width * scale))
        new_h = max(1, int(cropped.height * scale))
        cropped = cropped.resize((new_w, new_h), Image.Resampling.LANCZOS)
        
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()

def create_detail_framing_crops(
    hero_input: Union[bytes, str, Image.Image],
    target_ratio: str = "9:16"
) -> Tuple[bytes, bytes]:
    """
    Constructs secondary detail/garment close-up crops deterministically from the hero image
    using vertical center bias (36% top) and garment crop fraction (68%).
    Returns (detail_crop_bytes, zoom_crop_bytes).
    """
    img = _to_pil_image(hero_input)
    w, h = img.size
    
    # Detail Crop (focused on garment mid-section & torso fabric texture)
    detail_top = int(h * 0.20)
    detail_h = int(h * 0.60)
    detail_w = int(w * 0.85)
    detail_left = (w - detail_w) // 2
    detail_crop = img.crop((detail_left, detail_top, detail_left + detail_w, detail_top + detail_h))
    
    # Torso close-up crop
    torso_top = int(h * 0.15)
    torso_h = int(h * 0.50)
    torso_w = int(w * 0.70)
    torso_left = (w - torso_w) // 2
    torso_crop = img.crop((torso_left, torso_top, torso_left + torso_w, torso_top + torso_h))
    
    buf_detail = io.BytesIO()
    detail_crop.save(buf_detail, format="JPEG", quality=95)
    
    buf_torso = io.BytesIO()
    torso_crop.save(buf_torso, format="JPEG", quality=95)
    
    # Enforce aspect ratio on generated crops
    clean_detail = enforce_aspect_ratio(buf_detail.getvalue(), target_ratio)
    clean_torso = enforce_aspect_ratio(buf_torso.getvalue(), target_ratio)
    
    return clean_detail, clean_torso
