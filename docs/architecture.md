# AdCraft Architecture and Codebase Breakdown

Based on the architecture and codebase of your project, here is an in-depth breakdown of what AdCraft is, how its architecture is designed, and how its core engines—Scraper, Style Pipeline, and Video Generation—function.

## 1. High-Level Overview of the Project
AdCraft is an automated, AI-powered marketing tool that transforms standard e-commerce product links into fully polished Meta Ad campaign videos.

The core user journey follows a Product → Styles → Video → Meta Ad pipeline:

- A user inputs a product link (e.g., from Myntra, Amazon).
- The scraper extracts high-resolution product images, pricing, and metadata.
- The AI pipeline generates several stylized lifestyle or model images (e.g., Festive, Casual, Night) from the raw product images.
- The user selects a "hero" style, and the system generates a 9:16 vertical video for social media.
- The system can optionally publish this video directly as a Meta Ad.

### Architecture
The system uses a highly decoupled, state-based microservices architecture:

- **Frontend (React/Vite - Port 5173)**: The UI where users manage their studio, workflows, and Meta account connections. It talks only to the Express backend.
- **Express Backend (BFF - Port 4090)**: The primary orchestrator. It manages JWT authentication, user credits (a ledger system where actions cost credits), workflows, database models (MongoDB), and cron jobs that dispatch tasks to the Python Pipeline.
- **Python Pipeline (FastAPI - Port 8000)**: The heavy-lifting engine responsible for scraping, AI image generation, and AI video generation. It runs tasks asynchronously and uses callbacks/polling to update the Express backend.
- **MongoDB (Port 27017)**: The central source of truth for users, credits, jobs, and extracted product data.

## 2. How the Scraper Engine Works
The scraper is a Universal Multi-Platform E-Commerce Product Scraper built in Python. It supports 9 major platforms (Myntra, Amazon, Flipkart, Ajio, Nykaa, etc.). It is highly advanced and avoids traditional, brittle DOM scraping (like CSS selectors).

Here is how it works in depth:

- **Platform Detection & Routing**: When a URL is provided, `url_utils.py` detects the platform and normalizes the URL. It then routes the request to a platform-specific strategy (e.g., `MyntraScraper`, `AmazonScraper`).
- **Data-Hydration & API-First Extraction**: Instead of parsing HTML tags, the scraper hunts for raw JSON hydration states injected by modern JavaScript frameworks (like `window.__myx`, `__NEXT_DATA__`, or `__PRELOADED_STATE__`), or it hits the vendor's internal REST APIs directly. This ensures 100% structural stability even if the UI changes.
- **Hybrid Fetch Architecture**:
  - It first attempts an ultra-fast HTTP request using `urllib` with spoofed browser headers (completes in <500ms).
  - If it encounters a Cloudflare challenge, 403 Forbidden, or needs to execute JavaScript, it automatically falls back to a Playwright headless Chromium browser to render the DOM and intercept network requests.
- **HD Image Transformation**: E-commerce sites often serve low-res thumbnails. The scraper features an engine (`image_hd.py`) that uses platform-specific Regex to rewrite CDN URLs, forcing them to return maximum-resolution images (e.g., changing `/image/128/128/` to `/image/1080/1080/`).
- **Unified Domain Model**: All extracted data (price, size, stock, HD images, brand) is mapped to a standardized `ProductData` dataclass, ensuring the rest of the pipeline always receives data in the exact same format regardless of the source website.

## 3. How the Pipeline Works for Style Images
Once the product images are scraped, the Express backend requests the Python Pipeline to generate lifestyle versions of the product.

Here is how it works under the hood (`generate_styles.py`):

- **Asynchronous Job Initialization**: When the endpoint is hit, the `GenerateStyles` use case creates a `StyleJob` in the database with a `PENDING` status and returns a Job ID immediately. The actual generation runs in the background using `asyncio.create_task()`.
- **AI Generation via Engine**: The background task triggers the `StyleEngine`. It passes the scraped high-res product images, the product title, an aspect ratio (default 9:16), and the requested number of styles (e.g., 4 styles like Festive, Casual, Formal, Night).
- **Progress Tracking & Callbacks**: Because AI generation takes time, the engine utilizes a `sync_progress` callback. As the AI engine works, it updates the `StyleJob` status in MongoDB.
- **Completion**: Once the AI finishes generating the images, they are saved locally to an `OUTPUT_DIR` mapped to the Job ID. The job is marked as `COMPLETED`, which the Express backend will detect (either via a cron-polling mechanism or a webhook callback).

## 4. How Video Generation Works
After the user reviews the generated styles on the frontend and selects a "hero image", the video generation process is triggered.

Here is how it is executed (`generate_video.py`):

- **Job Creation**: Similar to styles, the `GenerateVideo` use case creates a `VideoJob` in the database with a `PENDING` status.
- **Asset Preparation**: The background task downloads the selected hero image (and optionally up to 2 extra supplementary images) locally using the `MediaStorage` interface. It saves them as `frame_0.png`, `frame_1.png`, etc., in a dedicated job folder.
- **AI Video Engine Execution**: These frames, along with the product title and style category, are passed to the `VideoEngine`. The engine stitches these images together, likely applying AI transitions, effects, and text overlays, resulting in a cohesive 9:16 vertical video tailored for Meta Ads (Instagram Reels/Facebook Stories).
- **Final Delivery**: The resulting video is saved locally, and its URL is attached to the `VideoJob`. The status updates to `COMPLETED`. The Express backend can now take this video and, if the user has authenticated their Meta account, use a cron job to automatically publish it to the Meta Ads API.

## 5. What We Have To Build (v1 Flow)
For the initial v1 flow, our focus is on establishing the core "happy path" user journey with essential features:

1. **Frontend (React/Vite)**: 
   - A simple interface for users to paste a product URL.
   - A loading state screen that polls the backend for scraping and style generation progress.
   - A gallery view to select the best "hero" style image.
   - A final preview screen showing the generated 9:16 video.
   
2. **Backend (Express + MongoDB)**: 
   - Basic REST endpoints to receive the product URL, fetch jobs, and select hero images.
   - Database schemas for `User`, `Job`, and `ProductData`.
   - Polling mechanism to sync statuses with the Python Pipeline.
   
3. **Python Pipeline (FastAPI)**:
   - **Scraper Service**: Implement support for at least 1-2 primary platforms (e.g., Amazon, Myntra) using the fallback HTTP-to-Playwright architecture.
   - **Style Generation (`generate_styles.py`)**: Connect to a base generative AI API (e.g., Gemini/Stable Diffusion API) to create 2-4 lifestyle variations from the scraped image.
   - **Video Generation (`generate_video.py`)**: Implement a simplified pipeline that stitches the selected image into a basic animated 9:16 video file using standard transitions.
   
4. **Integration**: End-to-end local deployment combining all three services and ensuring robust job state tracking (`PENDING` -> `PROCESSING` -> `COMPLETED`).

---

## 6. Detailed AI Pipeline HLD & LLD Reference
For the comprehensive technical specification covering OpenCV geometry algorithms, 3-tier forensic QC inspection (Laplacian variance, Face QA, Scene QA), self-healing loops, and Kling Omni video generation, refer to:
- [AI Pipeline High-Level & Low-Level Design (HLD & LLD)](file:///Users/flixstock/Desktop/adcraft%20/docs/AI_PIPELINE_HLD_LLD.md)
