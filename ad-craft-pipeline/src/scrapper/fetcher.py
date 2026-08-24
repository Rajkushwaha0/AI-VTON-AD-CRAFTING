import os
import sys
import urllib.request
import zlib
import gzip
import time
import re
from typing import Optional, Tuple, List, Dict, Any

from .loop_policy import ensure_playwright_event_loop_policy

try:
    import brotli
    HAS_BROTLI = True
except ImportError:
    HAS_BROTLI = False

class FetcherError(Exception):
    pass

_STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = { runtime: {} };
"""

_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]

_BROWSER_CHANNELS: List[Optional[str]] = ["chrome", "msedge", None]

def _get_user_agents() -> List[str]:
    """
    Returns OS-matched User-Agent strings so OS TCP stack signature matches User-Agent.
    Prevents Akamai Bot Manager OS fingerprint mismatch blocks on Linux servers.
    """
    if sys.platform.startswith("linux"):
        return [
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        ]
    else:
        return [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        ]


def _has_valid_product_markers(html: str) -> bool:
    """
    Checks if fetched HTML contains valid Myntra PDP data structures or metadata.
    """
    if not html:
        return False
    return "window.__myx" in html or "pdpData" in html or "og:image" in html


def _decompress_response_body(raw_data: bytes, encoding_header: Optional[str], charset: str = "utf-8") -> str:
    """
    Safely decompresses raw HTTP response bytes based on Content-Encoding header.
    Handles gzip, deflate, and br (Brotli).
    """
    if not encoding_header:
        return raw_data.decode(charset, errors="replace")

    enc = encoding_header.lower().strip()
    if enc == "gzip":
        try:
            return gzip.decompress(raw_data).decode(charset, errors="replace")
        except Exception:
            return raw_data.decode(charset, errors="replace")
    elif enc == "br" or enc == "brotli":
        if HAS_BROTLI:
            try:
                return brotli.decompress(raw_data).decode(charset, errors="replace")
            except Exception:
                return raw_data.decode(charset, errors="replace")
        else:
            return raw_data.decode(charset, errors="replace")
    elif enc == "deflate":
        try:
            return zlib.decompress(raw_data, -zlib.MAX_WBITS).decode(charset, errors="replace")
        except Exception:
            try:
                return zlib.decompress(raw_data).decode(charset, errors="replace")
            except Exception:
                return raw_data.decode(charset, errors="replace")
    else:
        return raw_data.decode(charset, errors="replace")


class PreserveHeadersRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req:
            for k, v in req.headers.items():
                new_req.add_header(k, v)
        return new_req

try:
    import ssl
    _ssl_ctx = ssl.create_default_context()
    _http_opener = urllib.request.build_opener(
        PreserveHeadersRedirectHandler,
        urllib.request.HTTPSHandler(context=_ssl_ctx)
    )
except Exception:
    _http_opener = urllib.request.build_opener(PreserveHeadersRedirectHandler)


_SHARED_SESSION: Optional[Any] = None

def _get_warm_session(ua: str) -> Any:
    """
    Returns a warmed-up requests Session with valid Myntra homepage Akamai cookies.
    """
    global _SHARED_SESSION
    import requests
    if _SHARED_SESSION is None:
        _SHARED_SESSION = requests.Session()
    
    _SHARED_SESSION.headers.update({
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate',
        'Cache-Control': 'no-cache',
        'Upgrade-Insecure-Requests': '1'
    })
    
    if not _SHARED_SESSION.cookies:
        try:
            _SHARED_SESSION.get("https://www.myntra.com/", timeout=6)
        except Exception as e:
            print(f"Session warmup warning: {e}")
            
    return _SHARED_SESSION


def fetch_myntra_html_http(url: str, timeout: int = 12) -> str:
    """
    Fetches raw HTML using HTTP request with anti-bot headers, user-agent rotation,
    Akamai session cookie warmup, and stream decompression.
    """
    accept_encoding = "gzip, deflate, br" if HAS_BROTLI else "gzip, deflate"
    last_err = "No attempt made"

    user_agents = _get_user_agents()
    for ua in user_agents:
        headers = {
            'User-Agent': ua,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': accept_encoding,
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'Upgrade-Insecure-Requests': '1'
        }

        # 1. Try urllib with redirect header preservation
        try:
            req = urllib.request.Request(url, headers=headers)
            with _http_opener.open(req, timeout=timeout) as response:
                encoding = response.headers.get_param('charset') or 'utf-8'
                content_encoding = response.info().get('Content-Encoding')
                raw_data = response.read()
                html = _decompress_response_body(raw_data, content_encoding, encoding)
                if _has_valid_product_markers(html):
                    return html
                last_err = f"urllib 200 OK missing PDP markers (len={len(html)})"
        except Exception as e:
            last_err = f"urllib: {e}"
            print(f"urllib fetch warning (UA={ua[:30]}...): {e}")

        # 2. Try warmed-up requests Session library
        try:
            session = _get_warm_session(ua)
            resp = session.get(url, timeout=timeout)
            if resp.status_code == 200 and _has_valid_product_markers(resp.text):
                return resp.text
            
            # If Akamai returned 483-byte Access Denied, force cookie refresh and retry once
            if len(resp.text) < 2000 or "Access Denied" in resp.text:
                print(f"Akamai block detected (len={len(resp.text)}). Refreshing session cookies...")
                session.cookies.clear()
                session.get("https://www.myntra.com/", timeout=6)
                retry_resp = session.get(url, timeout=timeout)
                if retry_resp.status_code == 200 and _has_valid_product_markers(retry_resp.text):
                    return retry_resp.text
                last_err = f"requests {retry_resp.status_code} (len={len(retry_resp.text)})"
            else:
                last_err = f"requests {resp.status_code} (len={len(resp.text)})"

        except Exception as e:
            last_err = f"requests: {e}"
            print(f"requests fetch warning (UA={ua[:30]}...): {e}")

    raise FetcherError(f"HTTP fetch failed to retrieve valid Myntra PDP markers for {url}. Last error: {last_err}")





def fetch_myntra_html_playwright(url: str, timeout: int = 25000) -> str:
    """
    Fallback browser fetcher using Playwright headless browser with multi-channel and stealth script support.
    """
    ensure_playwright_event_loop_policy()
    from playwright.sync_api import sync_playwright

    last_error: Optional[Exception] = None
    user_agents = _get_user_agents()

    with sync_playwright() as p:
        for channel in _BROWSER_CHANNELS:
            try:
                launch_kwargs = {
                    "headless": True,
                    "args": _LAUNCH_ARGS,
                    "ignore_default_args": ["--enable-automation"],
                }
                if channel:
                    launch_kwargs["channel"] = channel

                browser = p.chromium.launch(**launch_kwargs)
                context = browser.new_context(
                    user_agent=user_agents[0],
                    viewport={'width': 1280, 'height': 800},
                    locale='en-US',
                    extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'}
                )


                page = context.new_page()
                page.add_init_script(_STEALTH_SCRIPT)
                
                page.goto(url, wait_until='domcontentloaded', timeout=timeout)
                # Wait for JS hydration state window.__myx or pdpData
                for _ in range(12):
                    has_myx = page.evaluate("() => typeof window.__myx !== 'undefined' || (document.body && document.body.innerHTML.includes('pdpData'))")
                    if has_myx:
                        break
                    page.wait_for_timeout(500)
                content = page.content()
                browser.close()
                if _has_valid_product_markers(content):
                    return content
                else:
                    last_error = FetcherError(f"Playwright fetch via channel={channel or 'bundled'} returned page without product markers")
            except Exception as e:
                last_error = e
                if "is not found" not in str(e):
                    print(f"Myntra Playwright channel '{channel}' failed: {e!r}")
                continue

    raise FetcherError(f"Playwright browser fetch failed across all channels: {last_error!r}")


def _check_js_redirect(html: str) -> Optional[str]:
    """
    Detects client-side JavaScript redirect URLs in small response pages.
    """
    if not html or len(html) > 5000:
        return None
    m = re.search(r'location(?:\.href|\.replace)?\s*=\s*[\'"]([^\'"]+)[\'"]', html)
    if m:
        target = m.group(1).strip()
        if target.startswith("/"):
            return "https://www.myntra.com" + target
        elif target.startswith("http"):
            return target
    return None


def fetch_myntra_page(url: str) -> str:
    """
    Smart hybrid fetcher: tries HTTP fast fetch first, follows JS redirects,
    falls back to candidate URLs and Playwright, and validates PDP markers.
    """
    from .url_utils import extract_style_id
    style_id = extract_style_id(url)

    candidates = []
    if style_id:
        candidates.append(f"https://www.myntra.com/{style_id}")
    if url not in candidates:
        candidates.append(url)


    # 1. Try HTTP fetch across candidate URLs
    for candidate_url in candidates:
        try:
            html = fetch_myntra_html_http(candidate_url)
            
            # Check for JS redirect inside HTML
            redirect_url = _check_js_redirect(html)
            if redirect_url:
                print(f"Following JS redirect to: {redirect_url}")
                html = fetch_myntra_html_http(redirect_url)

            if _has_valid_product_markers(html):
                return html
        except Exception as e:
            print(f"HTTP fetch warning for '{candidate_url}': {e}")

    print(f"HTTP fetch returned HTML without valid Myntra PDP markers. Falling back to Playwright...")

    # 2. Fallback to Playwright
    for candidate_url in candidates:
        try:
            html = fetch_myntra_html_playwright(candidate_url)
            redirect_url = _check_js_redirect(html)
            if redirect_url:
                html = fetch_myntra_html_playwright(redirect_url)

            if _has_valid_product_markers(html):
                return html
        except Exception as e:
            print(f"Playwright fetch warning for '{candidate_url}': {e}")

    raise FetcherError(f"Both HTTP and Playwright failed to fetch valid Myntra product HTML for {url} (missing pdpData, window.__myx, and og:image). The request may be blocked by anti-bot protection.")


