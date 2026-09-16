# AdCraft: AI Virtual Try-On (VTON) & Automated Ad Campaign Pipeline

An enterprise-grade, end-to-end AI marketing infrastructure that transforms raw e-commerce product URLs into broadcast-ready, high-converting vertical ad creatives and video campaigns, integrated with an automated Meta Ads and social media posting pipeline.

---

## 🚀 System Overview

AdCraft automates the entire creative and advertising lifecycle for fashion e-commerce:

```
[ Product URL (Myntra / Amazon / Zara / Shopify) ]
                       │
                       ▼
    ┌───────────────────────────────────────┐
    │  1. Multi-Platform Scraper Engine     │ ◄─── Hybrid HTTP + Playwright Stealth + CDN HD
    └──────────────────┬────────────────────┘
                       │
                       ▼
    ┌───────────────────────────────────────┐
    │  2. Multimodal Garment & Style Engine │ ◄─── Gemini 2.5 Vision (Category, Fabric, Pairing)
    └──────────────────┬────────────────────┘
                       │
                       ▼
    ┌───────────────────────────────────────┐
    │  3. Parallel VTON Image Generator     │ ◄─── Gemini 3.1 Flash Image + Garment Lock
    └──────────────────┬────────────────────┘
                       │
                       ▼
    ┌───────────────────────────────────────┐
    │  4. Computer Vision Geometry & Crops  │ ◄─── OpenCV Center-Crop Aspect Ratios & Lanczos Face QA
    └──────────────────┬────────────────────┘
                       │
                       ▼
    ┌───────────────────────────────────────┐
    │  5. 3-Tier QC Gatekeeper & Self-Heal  │ ◄─── Laplacian Sharpness, Face QA, Scene Realism
    └──────────────────┬────────────────────┘
                       │ (Pass / Healed)
                       ▼
    ┌───────────────────────────────────────┐
    │  6. Generative Video Engine           │ ◄─── Kling Omni (Image-to-Video) + FFmpeg
    └──────────────────┬────────────────────┘
                       │
                       ▼
    ┌───────────────────────────────────────┐
    │  7. Meta Ads & Social Media Pipeline  │ ◄─── [Active Development] Automated Campaign Scheduling & Publishing
    └───────────────────────────────────────┘
```

---

## 🛠️ Core Engineering Modules

### 1. Universal E-Commerce Scraper Engine
- **Multi-Platform Support**: Scrapes 11+ platforms including Myntra, Amazon, Flipkart, Ajio, Nykaa, Zara, Uniqlo, and Shopify stores.
- **Hydration-State Extraction**: Directly extracts raw JSON from in-memory JavaScript states (`window.__myx`, `__NEXT_DATA__`, `__PRELOADED_STATE__`, or native `/products.json` endpoints) rather than fragile CSS selectors.
- **Hybrid Fetcher**: Ultra-fast `<500ms` HTTP fetcher with anti-bot header simulation, falling back to headless Playwright Chromium with stealth scripts for Cloudflare/Akamai bypass.
- **HD Image CDN Optimization**: Platform-specific regex transformations rewrite thumbnail URLs to full-resolution uncompressed assets (`w_1080,q_95` on Myntra, `._UX..._.jpg` stripping on Amazon).

### 2. Computer Vision Geometry & Aspect Ratio Engine (`geometry.py`)
- **Strict Social Media Aspect Ratios**: Standardizes all generated media to commercial ad formats:
  - `9:16` (Instagram Reels, Stories, TikTok, YouTube Shorts)
  - `1:1` (Meta Feed, Carousel Ads)
  - `16:9` (Landscape / Display Banners)
- **Distortion-Free Center Cropping**: Enforces target aspect ratios without geometric stretching, warping, or squashing of garments and models via OpenCV slicing.
- **Forensic Face Crop Extraction**: Crops upper-body facial regions (top 38% height, center 58% width) with Lanczos interpolation upscaling (min 640px) to feed the biometric verification auditor.
- **Deterministic Garment Detail Framing**: Generates secondary e-commerce close-up detail crops (mid-section fabric weave and torso framing) directly from the hero image.

### 3. Parallel VTON Image Generation & Garment Lock
- **Garment Preservation Lock**: Multi-part prompt constraints lock authentic commercial garments (collar, seams, hardware, fabric weave, dye hue) to prevent AI redesigns.
- **Two-Piece Garment Pairing**: Supports pairing separate tops and bottoms on the same model simultaneously with natural waistband draping.
- **Multi-Model Fallback Chain**: High-availability execution across `gemini-3.1-flash-image`, `gemini-3.1-flash-image-preview`, and `gemini-2.5-flash-image`.

### 4. 3-Tier Quality Control (QC) & Self-Healing Feedback Loop
- **Laplacian Edge Variance (`cv2.Laplacian`)**: Fast OpenCV filter computing the 2nd spatial derivative. Scores $< 40.0$ trigger blur defects and negative prompt penalties.
- **Hero Anatomy & Skin Texture Auditor**: Enforces exact human anatomy (2 arms, 2 legs, 5 distinct fingers per hand) and natural micro-pore skin texture (rejecting over-smoothed plastic AI skin).
- **Face Identity QA**: 8-checkpoint biometric inspection comparing reference model face crops with generated scene heroes.
- **Scene Realism QA**: Physical realism audit checking floor contact shadows, realistic human scale proportions relative to furniture, and reflection consistency.
- **Closed-Loop Self-Healing**: Automatically triggers up to 3 retry attempts, synthesizing targeted repair instructions and negative prompt constraints (`"deformed fingers, extra digits, waxy skin, blurry edges"`).

### 5. Kling Omni Video Generation & Video Processing
- **Image-to-Video Synthesis**: Generates 9:16 vertical video reels from validated hero images using Kling AI.
- **FFmpeg Post-Processing Engine**: Normalizes frame rates, audio codecs, pixel formats (`yuv420p`), and aspect ratios for social media ad compliance.

### 6. Meta Ads & Social Media Posting Pipeline *(Active Development)*
- **Campaign & Ad Set Orchestration**: Automated creation of Meta Campaign, Ad Set, and Creative entities via Meta Marketing Graph API.
- **Targeting & Placement Optimization**: Automatic asset routing for Reels, Feed, and Stories.
- **Publishing & Scheduling Scheduler**: Automated cron-based scheduling, draft mode staging, and budget pacing controls for direct ad activation.

---

## 📂 Project Architecture

```
adcraft/
├── README.md                           # Main Project Documentation
├── docs/
│   ├── architecture.md                 # System Architecture & Service Topology
│   └── AI_PIPELINE_HLD_LLD.md          # Comprehensive AI Pipeline Design Document
└── ad-craft-pipeline/                  # Core Python AI Engine
    ├── cli_runner.py                   # CLI Orchestrator Entrypoint
    ├── runs/                           # Execution Traces, Logs & Saved Artifacts
    ├── prompts/                        # Master Prompts (VTON, Pairing, Styling)
    └── src/
        ├── core/                       # Domain Models & Interfaces (ProductData, PipelineContext)
        ├── pipeline/                   # Orchestrator & Multi-Stage Workflow
        ├── scrapper/                   # Universal 11+ Platform E-Commerce Scraper
        └── services/
            ├── dynamic_style_service.py # Gemini Lifestyle Style Generator
            ├── vton_image_gen.py        # Parallel VTON Image Generator & Self-Heal Loop
            ├── qc_inspector.py          # Unified QC Gatekeeper Orchestrator
            ├── qc/
            │   ├── hero_quality_inspector.py # Laplacian Sharpness & Anatomy Auditor
            │   ├── face_identity_qa.py       # Biometric Face Verification
            │   └── scene_realism_qa.py       # Spatial & Physical Realism Auditor
            ├── image_processing/
            │   └── geometry.py          # OpenCV Aspect Ratio & Crop Enforcement
            ├── video_gen.py             # Video Generation Orchestrator
            ├── kling/                   # Kling AI Image2Video Client & Auth
            └── video_processing/
                └── ffmpeg_service.py    # FFmpeg Video Normalization & Conformance
```

---

## ⚡ Quickstart & Execution

### Prerequisites
- Python 3.10+
- FFmpeg installed on system PATH
- OpenCV & PIL
- Google Gemini API Key (`GEMINI_API_KEY`)
- Kling AI Credentials (`KLING_ACCESS_KEY`, `KLING_SECRET_KEY`)

### Installation
```bash
git clone https://github.com/Rajkushwaha0/AI-VTON-AD-CRAFTING.git
cd AI-VTON-AD-CRAFTING/ad-craft-pipeline
pip install -r requirements.txt
playwright install chromium
```

### Running the End-to-End Pipeline
```bash
python cli_runner.py \
  --url "https://www.myntra.com/ethnic-dresses/..." \
  --aspect-ratio "9:16" \
  --max-parallel 4 \
  --stage all
```

### Pipeline Execution Flags
- `--stage image`: Runs scraping, style analysis, VTON image generation, and QC audits.
- `--stage video`: Runs downstream video generation from validated hero images.
- `--aspect-ratio`: Target format (`9:16`, `1:1`, `16:9`).
- `--max-parallel`: Number of parallel style generations (default: 4).

---

## 🛡️ License & Contact

Developed as part of the AdCraft AI Infrastructure project.
