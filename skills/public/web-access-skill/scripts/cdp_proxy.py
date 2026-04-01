#!/usr/bin/env python3
import argparse
import asyncio
import base64
import json
import time
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from playwright.async_api import Browser, Page, async_playwright


STATE: Dict[str, Any] = {
    "loop": None,
    "browser": None,
    "pages": {},
    "startup_error": None,
}


def _json_response(handler: BaseHTTPRequestHandler, payload: Dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    length = int(handler.headers.get("Content-Length", "0"))
    return handler.rfile.read(length) if length > 0 else b""


async def _connect_browser(cdp_url: str) -> Browser:
    playwright = await async_playwright().start()
    browser = await playwright.chromium.connect_over_cdp(cdp_url)
    STATE["playwright"] = playwright
    STATE["browser"] = browser
    return browser


async def _ensure_browser(cdp_url: str) -> Browser:
    browser = STATE.get("browser")
    if browser is not None and browser.is_connected():
        return browser
    return await _connect_browser(cdp_url)


async def _get_context(cdp_url: str):
    browser = await _ensure_browser(cdp_url)
    if browser.contexts:
        return browser.contexts[0]
    return await browser.new_context()


async def _new_page(cdp_url: str, url: str) -> Dict[str, Any]:
    context = await _get_context(cdp_url)
    page = await context.new_page()
    await page.goto(url, wait_until="domcontentloaded")
    target_id = str(uuid.uuid4())
    STATE["pages"][target_id] = page
    return {"targetId": target_id, "url": page.url, "title": await page.title()}


def _page_for(target_id: str) -> Page:
    page = STATE["pages"].get(target_id)
    if page is None:
        raise KeyError(f"Unknown target: {target_id}")
    return page


async def _close_page(target_id: str) -> Dict[str, Any]:
    page = _page_for(target_id)
    await page.close()
    STATE["pages"].pop(target_id, None)
    return {"closed": True, "targetId": target_id}


async def _eval(target_id: str, expression: str) -> Dict[str, Any]:
    page = _page_for(target_id)
    result = await page.evaluate(expression)
    return {"result": result}


async def _text(target_id: str) -> Dict[str, Any]:
    page = _page_for(target_id)
    text = await page.evaluate(
        """() => {
            const candidates = [
              document.querySelector('main'),
              document.querySelector('[role="main"]'),
              document.body,
            ].filter(Boolean);
            return (candidates[0]?.innerText || '').trim();
        }"""
    )
    return {"text": text}


async def _click(target_id: str, selector: str) -> Dict[str, Any]:
    page = _page_for(target_id)
    locator = page.locator(selector).first
    await locator.wait_for(timeout=5000)
    await locator.click()
    return {"clicked": True, "selector": selector}


async def _click_at(target_id: str, selector: str) -> Dict[str, Any]:
    page = _page_for(target_id)
    locator = page.locator(selector).first
    await locator.wait_for(timeout=5000)
    box = await locator.bounding_box()
    if not box:
        raise RuntimeError(f"Could not resolve bounding box for selector: {selector}")
    await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    return {"clicked": True, "selector": selector, "mode": "pointer"}


async def _fill(target_id: str, selector: str, value: str) -> Dict[str, Any]:
    page = _page_for(target_id)
    locator = page.locator(selector).first
    await locator.wait_for(timeout=5000)
    await locator.fill(value)
    return {"filled": True, "selector": selector}


async def _set_files(target_id: str, selector: str, files: list[str]) -> Dict[str, Any]:
    page = _page_for(target_id)
    locator = page.locator(selector).first
    await locator.wait_for(timeout=5000)
    await locator.set_input_files(files)
    return {"uploaded": True, "selector": selector, "files": files}


async def _scroll(target_id: str, direction: str) -> Dict[str, Any]:
    page = _page_for(target_id)
    if direction == "top":
        await page.evaluate("window.scrollTo(0, 0)")
    else:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    return {"scrolled": True, "direction": direction}


async def _screenshot(target_id: str, file_path: Optional[str]) -> Dict[str, Any]:
    page = _page_for(target_id)
    if file_path:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path), full_page=True)
        return {"saved": True, "file": str(path)}
    raw = await page.screenshot(full_page=True)
    return {"base64": base64.b64encode(raw).decode("ascii")}


def _run_async(coro):
    future = asyncio.run_coroutine_threadsafe(coro, STATE["loop"])
    return future.result(timeout=60)


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "WebAccessCDPProxy/1.0"

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        try:
            if parsed.path == "/ping":
                _json_response(self, {"status": "ok"})
                return
            if parsed.path == "/new":
                url = params.get("url", ["about:blank"])[0]
                _json_response(self, _run_async(_new_page(self.server.cdp_url, url)))
                return
            if parsed.path == "/close":
                target_id = params["target"][0]
                _json_response(self, _run_async(_close_page(target_id)))
                return
            if parsed.path == "/text":
                target_id = params["target"][0]
                _json_response(self, _run_async(_text(target_id)))
                return
            if parsed.path == "/scroll":
                target_id = params["target"][0]
                direction = params.get("direction", ["bottom"])[0]
                _json_response(self, _run_async(_scroll(target_id, direction)))
                return
            if parsed.path == "/screenshot":
                target_id = params["target"][0]
                file_path = params.get("file", [None])[0]
                _json_response(self, _run_async(_screenshot(target_id, file_path)))
                return
            _json_response(self, {"error": "Unknown endpoint"}, status=404)
        except Exception as exc:
            _json_response(self, {"error": str(exc)}, status=500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        body = _read_body(self)
        try:
            if parsed.path == "/eval":
                target_id = params["target"][0]
                expression = body.decode("utf-8")
                _json_response(self, _run_async(_eval(target_id, expression)))
                return
            if parsed.path == "/click":
                target_id = params["target"][0]
                selector = body.decode("utf-8").strip()
                _json_response(self, _run_async(_click(target_id, selector)))
                return
            if parsed.path == "/clickAt":
                target_id = params["target"][0]
                selector = body.decode("utf-8").strip()
                _json_response(self, _run_async(_click_at(target_id, selector)))
                return
            if parsed.path == "/fill":
                target_id = params["target"][0]
                payload = json.loads(body.decode("utf-8"))
                _json_response(self, _run_async(_fill(target_id, payload["selector"], payload["value"])))
                return
            if parsed.path == "/setFiles":
                target_id = params["target"][0]
                payload = json.loads(body.decode("utf-8"))
                _json_response(self, _run_async(_set_files(target_id, payload["selector"], payload["files"])))
                return
            _json_response(self, {"error": "Unknown endpoint"}, status=404)
        except Exception as exc:
            _json_response(self, {"error": str(exc)}, status=500)


def _start_loop(cdp_url: str) -> None:
    loop = asyncio.new_event_loop()
    STATE["loop"] = loop
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_ensure_browser(cdp_url))
        loop.run_forever()
    except Exception as exc:
        STATE["startup_error"] = str(exc)


def _assert_cdp_available(cdp_url: str) -> None:
    version_url = cdp_url.rstrip("/") + "/json/version"
    with urlopen(version_url, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"CDP endpoint unhealthy: {response.status}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local CDP proxy for web-access-skill")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3456)
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    args = parser.parse_args()

    _assert_cdp_available(args.cdp_url)

    loop_thread = threading.Thread(target=_start_loop, args=(args.cdp_url,), daemon=True)
    loop_thread.start()

    while STATE["loop"] is None and STATE["startup_error"] is None:
        time.sleep(0.05)
    if STATE["startup_error"] is not None:
        raise RuntimeError(STATE["startup_error"])

    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    server.cdp_url = args.cdp_url
    print(f"Web access CDP proxy listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
