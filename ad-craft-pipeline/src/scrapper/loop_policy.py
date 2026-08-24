import sys
import asyncio


def ensure_playwright_event_loop_policy() -> None:
    """
    Playwright's sync API launches the browser through an asyncio subprocess.
    On Windows, spawning a subprocess requires a ProactorEventLoop; a
    SelectorEventLoop raises a bare NotImplementedError instead.

    Some ASGI servers leave the process on WindowsSelectorEventLoopPolicy
    (notably uvicorn started with reload=True). Because Playwright's sync
    context manager builds its own loop via asyncio.new_event_loop(), which
    honors the process-wide policy, we force a Proactor policy here before
    Playwright creates that loop. This only affects loops created afterwards,
    so an already-running server event loop is left untouched.
    """
    if sys.platform != "win32":
        return
    policy = asyncio.get_event_loop_policy()
    if not isinstance(policy, asyncio.WindowsProactorEventLoopPolicy):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
