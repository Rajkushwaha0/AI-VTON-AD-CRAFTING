# AdCraft AI Pipeline Engine

The core compute engine of AdCraft responsible for multi-platform e-commerce scraping, AI garment analysis, parallel Virtual Try-On (VTON) generation, OpenCV geometric image processing, 3-tier forensic QC inspection, and automated video generation.

---

## Architecture & Subsystems

1. **Scraper Ingestion Layer (`src/scrapper/`)**
   - High-throughput scraping across Myntra, Amazon, Flipkart, Ajio, Nykaa, Zara, Uniqlo, and Shopify.
   - Dual-mode fetching: Fast HTTP with anti-bot simulation + Headless Playwright Chromium fallback.
   - Native hydration state parsing (`window.__myx`, `__NEXT_DATA__`, `__PRELOADED_STATE__`) and CDN HD URL rewriting.

2. **Geometric Image Processing (`src/services/image_processing/geometry.py`)**
   - **Aspect Ratio Enforcement (`enforce_aspect_ratio`)**: Center-crops incoming and generated images to standard social media ad aspect ratios (`9:16`, `1:1`, `16:9`) without stretching or distorting model anatomy and garment silhouettes.
   - **Biometric Face Crop (`extract_face_crop`)**: Isolates the facial region (top 38% height, center 58% width) with Lanczos upscaling (minimum 640px) for biometric QA inspection.
   - **Detail Framing Crops (`create_detail_framing_crops`)**: Produces deterministic secondary close-up shots (mid-section fabric weave and torso framing) directly from the hero image.

3. **Parallel VTON & Garment Lock Engine (`src/services/vton_image_gen.py`)**
   - Multi-worker concurrent generation (`ThreadPoolExecutor`) across selected lifestyle styles.
   - Prompt-enforced **Primary & Secondary Garment Locks** ensuring zero hallucination of colors, cuts, buttons, patterns, or fabric textures.
   - Two-piece pairing engine supporting upper and lower garments simultaneously.

4. **Forensic QC Gatekeeper & Self-Healing Loop (`src/services/qc/`)**
   - **Laplacian Blur Detection**: Computes the 2nd spatial derivative variance (`cv2.Laplacian(img, cv2.CV_64F).var()`). Variance below `40.0` flags high-frequency blur.
   - **Hero Quality Inspector**: Audits limb counts (exactly 2 arms, 2 legs, 5 fingers per hand), skin micro-pore texture, and garment silhouette integrity.
   - **Face Identity QA**: Forensic comparison between catalog model and generated scene hero using facial thirds, eye spacing, nose bridge, and jawline geometry.
   - **Scene Realism QA**: Verifies ground contact shadows, human scale proportions relative to background props, and perspective consistency.
   - **Self-Healing Loop**: Up to 3 retry attempts synthesizing targeted repair prompts and dynamic negative constraints.

5. **Cinematic Video Engine (`src/services/video_gen.py` & `src/services/kling/`)**
   - Kling AI image-to-video synthesis generating 9:16 vertical video reels.
   - FFmpeg normalization pipeline conforming frame rates, audio codecs, and pixel formats for Meta Ads.

6. **Social Media & Meta Ads Integration *(In Active Development)* **
   - Automated Meta Marketing API campaign creation, ad set scheduling, creative attachment, and direct social media publishing.
