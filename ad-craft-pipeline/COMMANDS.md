# AdCraft Multi-Garment Pipeline Commands Cheatsheet

This document contains ready-to-run commands for each stage of the pipeline:
1. **Scrape** (Product Ingestion)
2. **Style** (Gemini Garment & Accessory Pairing)
3. **Image** (VTON Generation + Multi-Stage QC V1 / V2)
4. **Video** (Kling Multi-Shot Generation + FFmpeg Merge)

---

## 🚀 Quick Run (demo.json Workflow)

### Stage 1: Scrape
Scrapes primary and secondary products, downloads images, and stops.
```bash
python3 cli_runner.py --demo demo.json --stage scrape
```

### Stage 2: Style
Scrapes products and runs Gemini multimodal style & auto-accessory analysis.
```bash
python3 cli_runner.py --demo demo.json --stage style
```

### Stage 3: Image (VTON Generation + QC)
Generates Hero, Detail, and Back shots; runs QC gatekeeper and self-healing retries.

* **Using V1 QC (Legacy baseline):**
  ```bash
  python3 cli_runner.py --demo demo.json --stage image --qc-version v1
  ```

* **Using V2 QC (New 4-Tier: OpenCV + Color/Texture + Gemini Judge):**
  ```bash
  python3 cli_runner.py --demo demo.json --stage image --qc-version v2
  ```

### Stage 4: Video (End-to-End Pipeline)
Scrapes $\rightarrow$ Styles $\rightarrow$ VTON Images $\rightarrow$ Parallel Kling Videos $\rightarrow$ FFmpeg Merge.

* **Using V1 QC:**
  ```bash
  python3 cli_runner.py --demo demo.json --stage video --qc-version v1
  ```

* **Using V2 QC:**
  ```bash
  python3 cli_runner.py --demo demo.json --stage video --qc-version v2
  ```

---

## 💻 Direct CLI Workflow (Custom Product URLs)

### Stage 1: Scrape
```bash
python3 cli_runner.py \
  --url "https://www.myntra.com/tops/fable+street/fable-street-livin-v-neck-regular-top/14785506/buy" \
  --secondary-url "https://www.myntra.com/jeans/glitchez/glitchez-women-baggy-pure-cotton-heavy-fade-wide-leg-jeans/31363104/buy" \
  --stage scrape
```

### Stage 2: Style
```bash
python3 cli_runner.py \
  --url "https://www.myntra.com/tops/fable+street/fable-street-livin-v-neck-regular-top/14785506/buy" \
  --secondary-url "https://www.myntra.com/jeans/glitchez/glitchez-women-baggy-pure-cotton-heavy-fade-wide-leg-jeans/31363104/buy" \
  --aspect-ratio "9:16" \
  --stage style
```

### Stage 3: Image
* **With V1 QC:**
  ```bash
  python3 cli_runner.py \
    --url "https://www.myntra.com/tops/fable+street/fable-street-livin-v-neck-regular-top/14785506/buy" \
    --secondary-url "https://www.myntra.com/jeans/glitchez/glitchez-women-baggy-pure-cotton-heavy-fade-wide-leg-jeans/31363104/buy" \
    --aspect-ratio "9:16" \
    --stage image \
    --qc-version v1
  ```

* **With V2 QC:**
  ```bash
  python3 cli_runner.py \
    --url "https://www.myntra.com/tops/fable+street/fable-street-livin-v-neck-regular-top/14785506/buy" \
    --secondary-url "https://www.myntra.com/jeans/glitchez/glitchez-women-baggy-pure-cotton-heavy-fade-wide-leg-jeans/31363104/buy" \
    --aspect-ratio "9:16" \
    --stage image \
    --qc-version v2
  ```

### Stage 4: Video
* **With V1 QC:**
  ```bash
  python3 cli_runner.py \
    --url "https://www.myntra.com/tops/fable+street/fable-street-livin-v-neck-regular-top/14785506/buy" \
    --secondary-url "https://www.myntra.com/jeans/glitchez/glitchez-women-baggy-pure-cotton-heavy-fade-wide-leg-jeans/31363104/buy" \
    --aspect-ratio "9:16" \
    --duration 10 \
    --merge-video \
    --merge-video-idea "smooth" \
    --stage video \
    --qc-version v1
  ```

* **With V2 QC:**
  ```bash
  python3 cli_runner.py \
    --url "https://www.myntra.com/tops/fable+street/fable-street-livin-v-neck-regular-top/14785506/buy" \
    --secondary-url "https://www.myntra.com/jeans/glitchez/glitchez-women-baggy-pure-cotton-heavy-fade-wide-leg-jeans/31363104/buy" \
    --aspect-ratio "9:16" \
    --duration 10 \
    --merge-video \
    --merge-video-idea "smooth" \
    --user-prompt "Cinematic elegant tracking shot with confident model stride" \
    --stage video \
    --qc-version v2
  ```

---

## 👗 Single Garment Mode (No Secondary Pairing)
If you only have a single garment (dress, jacket, top):
```bash
python3 cli_runner.py \
  --url "https://www.myntra.com/tops/fable+street/fable-street-livin-v-neck-regular-top/14785506/buy" \
  --stage image \
  --qc-version v2
```

---

## 🧪 Testing QC V2 Directly
Run the standalone test suite:
```bash
python3 test_qc_v2.py
```
