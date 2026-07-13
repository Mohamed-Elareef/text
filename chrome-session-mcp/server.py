"""
Chrome Session MCP Server
=========================

A FastMCP server that drives a real Google Chrome (headful, under Xvfb) via
Playwright, using a *copy* of a host Chrome profile so that every site the user
is logged into in the desktop/GUI Chrome is automatically logged in here too.

Session sync (two levels):
  * soft sync  -> read cookies straight from the (read-only mounted) host
                  profile and inject them into the live browser, no restart,
                  no disruption. Runs on a timer + on demand.
  * hard sync  -> stop the browser, rsync the whole host profile into the live
                  profile (cookies + Local Storage + IndexedDB + everything),
                  then relaunch. Runs at startup + on demand. Use this for
                  sites whose login lives in localStorage/IndexedDB, not cookies.

The server exposes a rich, flexible tool surface (navigation, structure
extraction, clicking/typing/forms, file upload, tabs, cookies, screenshots and
*arbitrary JavaScript execution*) so an agent can operate on any site, including
ones that fight automation.

Transport: streamable HTTP on 0.0.0.0:$MCP_PORT (default 8765), protected by a
static bearer token ($MCP_TOKEN). Add it to Claude Code as a remote connector.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Hash import SHA1, HMAC

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Error as PWError

from fastmcp import FastMCP
from fastmcp.utilities.types import Image

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
HOST_PROFILE = os.environ.get("HOST_PROFILE", "/host-profile")   # read-only mount of the GUI Chrome profile
LIVE_PROFILE = os.environ.get("LIVE_PROFILE", "/profile")        # the separate copy this browser runs on
CHROME_CHANNEL = os.environ.get("CHROME_CHANNEL", "chrome")
MCP_PORT = int(os.environ.get("MCP_PORT", "8765"))
MCP_TOKEN = os.environ.get("MCP_TOKEN", "")
SOFT_SYNC_INTERVAL = int(os.environ.get("SOFT_SYNC_INTERVAL", "300"))  # seconds; 0 disables
DEFAULT_NAV_TIMEOUT = int(os.environ.get("NAV_TIMEOUT_MS", "45000"))
HEADLESS = os.environ.get("HEADLESS", "0") == "1"

# Files/dirs never worth copying during a hard sync (locks + caches).
RSYNC_EXCLUDES = [
    "Singleton*", "*.lock", "lockfile",
    "**/Cache/**", "**/Code Cache/**", "**/GPUCache/**", "**/ShaderCache/**",
    "**/GrShaderCache/**", "**/component_crx_cache/**", "**/Crashpad/**",
    "**/DawnCache/**", "**/DawnGraphiteCache/**", "**/DawnWebGPUCache/**",
    "**/Service Worker/CacheStorage/**",
]

# --------------------------------------------------------------------------- #
# Cookie decryption (Linux "basic" password store == fixed key "peanuts")
# --------------------------------------------------------------------------- #
_CHROME_EPOCH_OFFSET = 11644473600  # seconds between 1601-01-01 and 1970-01-01


def _linux_basic_key() -> bytes:
    return PBKDF2(b"peanuts", b"saltysalt", dkLen=16, count=1,
                  prf=lambda p, s: HMAC.new(p, s, SHA1).digest())


def _decrypt_cookie(encrypted: bytes, key: bytes) -> Optional[str]:
    """Decrypt a Chrome v10 (Linux basic-store) cookie value. Best effort."""
    if not encrypted:
        return ""
    try:
        if encrypted[:3] in (b"v10", b"v11"):
            enc = encrypted[3:]
            iv = b" " * 16
            cipher = AES.new(key, AES.MODE_CBC, iv)
            dec = cipher.decrypt(enc)
            # strip PKCS7 padding
            pad = dec[-1]
            if 1 <= pad <= 16:
                dec = dec[:-pad]
            try:
                return dec.decode("utf-8")
            except UnicodeDecodeError:
                # newer Chrome prepends a 32-byte SHA256(domain) to the value
                return dec[32:].decode("utf-8", "replace")
        # not encrypted (rare) -> plain
        return encrypted.decode("utf-8", "replace")
    except Exception:
        return None


def _host_cookie_db() -> Optional[Path]:
    for cand in (
        Path(HOST_PROFILE) / "Default" / "Network" / "Cookies",
        Path(HOST_PROFILE) / "Default" / "Cookies",
    ):
        if cand.exists():
            return cand
    return None


def _read_host_cookies() -> list[dict[str, Any]]:
    """Read + decrypt cookies from the host profile into Playwright cookie dicts."""
    db = _host_cookie_db()
    if not db:
        return []
    key = _linux_basic_key()
    # copy to a temp file so a WAL-locked live DB can still be read
    tmp = Path(tempfile.mkdtemp()) / "Cookies"
    try:
        tmp.write_bytes(db.read_bytes())
    except Exception:
        # fall back to immutable read-only open on the original
        tmp = db
    cookies: list[dict[str, Any]] = []
    try:
        uri = f"file:{tmp}?mode=ro&immutable=1"
        con = sqlite3.connect(uri, uri=True)
        cur = con.execute(
            "SELECT host_key, name, encrypted_value, value, path, "
            "expires_utc, is_secure, is_httponly, samesite FROM cookies"
        )
        samesite_map = {0: "None", 1: "Lax", 2: "Strict", -1: "Lax"}
        for host_key, name, enc, plain, path, expires_utc, secure, httponly, samesite in cur:
            value = plain if plain else _decrypt_cookie(enc, key)
            if value is None:
                continue
            ck: dict[str, Any] = {
                "name": name,
                "value": value,
                "domain": host_key,
                "path": path or "/",
                "secure": bool(secure),
                "httpOnly": bool(httponly),
                "sameSite": samesite_map.get(samesite, "Lax"),
            }
            if expires_utc and expires_utc > 0:
                exp = expires_utc / 1_000_000 - _CHROME_EPOCH_OFFSET
                if exp > time.time():
                    ck["expires"] = exp
            cookies.append(ck)
        con.close()
    except Exception:
        return cookies
    return cookies


# --------------------------------------------------------------------------- #
# Browser manager
# --------------------------------------------------------------------------- #
STEALTH_INIT_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en','ar']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
"""


class BrowserManager:
    def __init__(self) -> None:
        self._pw = None
        self._ctx: Optional[BrowserContext] = None
        self._lock = asyncio.Lock()
        self._sync_started = False
        self.last_soft_sync = 0.0
        self.last_hard_sync = 0.0
        self._last_activity = 0.0

    # -- lifecycle ------------------------------------------------------- #
    def _launch_args(self) -> list[str]:
        return [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--password-store=basic",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,OptimizationHints",
            "--window-size=1920,1080",
        ]

    async def _start_context(self) -> None:
        if self._pw is None:
            self._pw = await async_playwright().start()
        self._ctx = await self._pw.chromium.launch_persistent_context(
            user_data_dir=LIVE_PROFILE,
            channel=CHROME_CHANNEL,
            headless=HEADLESS,
            args=self._launch_args(),
            ignore_default_args=["--enable-automation"],
            viewport=None,
            no_viewport=True,
            accept_downloads=True,
        )
        await self._ctx.add_init_script(STEALTH_INIT_JS)
        self._ctx.set_default_timeout(DEFAULT_NAV_TIMEOUT)
        if not self._ctx.pages:
            await self._ctx.new_page()

    async def ensure(self) -> BrowserContext:
        async with self._lock:
            if self._ctx is None:
                # first boot: full profile copy so localStorage/IndexedDB come across
                await self._hard_copy_profile()
                await self._start_context()
                await self._inject_cookies()
                self.last_hard_sync = time.time()
                if not self._sync_started and SOFT_SYNC_INTERVAL > 0:
                    self._sync_started = True
                    asyncio.create_task(self._sync_loop())
            return self._ctx

    async def page(self) -> Page:
        ctx = await self.ensure()
        self._last_activity = time.time()
        pages = ctx.pages
        if not pages:
            return await ctx.new_page()
        return pages[-1]

    async def close(self) -> None:
        async with self._lock:
            if self._ctx is not None:
                try:
                    await self._ctx.close()
                except Exception:
                    pass
                self._ctx = None

    # -- sync ------------------------------------------------------------ #
    async def _hard_copy_profile(self) -> None:
        """rsync the whole host profile into the live profile (browser must be down)."""
        Path(LIVE_PROFILE).mkdir(parents=True, exist_ok=True)
        excludes = []
        for e in RSYNC_EXCLUDES:
            excludes += ["--exclude", e]
        src = HOST_PROFILE.rstrip("/") + "/"
        dst = LIVE_PROFILE.rstrip("/") + "/"
        cmd = ["rsync", "-a", "--delete"] + excludes + [src, dst]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()

    async def _inject_cookies(self) -> int:
        cookies = await asyncio.get_event_loop().run_in_executor(None, _read_host_cookies)
        if not cookies and self._ctx is not None:
            return 0
        if self._ctx is not None and cookies:
            try:
                await self._ctx.add_cookies(cookies)
            except Exception:
                # add them one-by-one, skipping the malformed ones
                ok = 0
                for c in cookies:
                    try:
                        await self._ctx.add_cookies([c])
                        ok += 1
                    except Exception:
                        continue
                return ok
        return len(cookies)

    async def soft_sync(self) -> int:
        n = await self._inject_cookies()
        self.last_soft_sync = time.time()
        return n

    async def hard_sync(self) -> None:
        async with self._lock:
            if self._ctx is not None:
                try:
                    await self._ctx.close()
                except Exception:
                    pass
                self._ctx = None
            await self._hard_copy_profile()
            await self._start_context()
        await self._inject_cookies()
        self.last_hard_sync = time.time()

    async def _sync_loop(self) -> None:
        while True:
            await asyncio.sleep(SOFT_SYNC_INTERVAL)
            try:
                await self.soft_sync()
            except Exception:
                pass


BM = BrowserManager()

# --------------------------------------------------------------------------- #
# MCP server + tools
# --------------------------------------------------------------------------- #
mcp = FastMCP(
    name="chrome-session",
    instructions=(
        "Drives a real logged-in Google Chrome via Playwright. The browser shares "
        "the user's live login sessions (cookies + storage) from their desktop "
        "Chrome. Use browser_snapshot to understand a page's interactive structure, "
        "then act with browser_click/browser_type/browser_fill_form using the refs "
        "it returns. For anything the structured tools can't express, use "
        "browser_evaluate to run arbitrary JavaScript in the page. If a site appears "
        "logged out, call sync_session(mode='hard')."
    ),
)


def _truncate(text: str, limit: int = 20000) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"
    return text


# ---- navigation ----------------------------------------------------------- #
@mcp.tool
async def browser_navigate(url: str, wait_until: str = "load", timeout_ms: int = 0) -> str:
    """Navigate the active tab to a URL. wait_until: load|domcontentloaded|networkidle|commit."""
    page = await BM.page()
    await page.goto(url, wait_until=wait_until, timeout=timeout_ms or DEFAULT_NAV_TIMEOUT)
    return f"Navigated to {page.url} — title: {await page.title()}"


@mcp.tool
async def browser_back() -> str:
    """Go back in history on the active tab."""
    page = await BM.page()
    await page.go_back()
    return f"Now at {page.url}"


@mcp.tool
async def browser_forward() -> str:
    """Go forward in history on the active tab."""
    page = await BM.page()
    await page.go_forward()
    return f"Now at {page.url}"


@mcp.tool
async def browser_reload() -> str:
    """Reload the active tab."""
    page = await BM.page()
    await page.reload()
    return f"Reloaded {page.url}"


@mcp.tool
async def browser_current_url() -> str:
    """Return the active tab's current URL and title."""
    page = await BM.page()
    return json.dumps({"url": page.url, "title": await page.title()})


@mcp.tool
async def browser_wait_for(
    selector: str = "", text: str = "", state: str = "visible",
    network_idle: bool = False, timeout_ms: int = 30000,
) -> str:
    """Wait for a selector/state, for text to appear, or for network idle."""
    page = await BM.page()
    if selector:
        await page.wait_for_selector(selector, state=state, timeout=timeout_ms)
        return f"Selector ready: {selector}"
    if text:
        await page.get_by_text(text).first.wait_for(state="visible", timeout=timeout_ms)
        return f"Text visible: {text}"
    if network_idle:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
        return "Network idle."
    await page.wait_for_timeout(min(timeout_ms, 10000))
    return "Waited."


# ---- structure / reading -------------------------------------------------- #
@mcp.tool
async def browser_snapshot() -> str:
    """Return a compact structural snapshot of interactive elements with stable
    refs (data-mcp-ref=N). Use those refs with browser_click/browser_type."""
    page = await BM.page()
    js = r"""
    () => {
      const out = [];
      let i = 0;
      const sel = 'a,button,input,textarea,select,[role=button],[role=link],[role=tab],[role=menuitem],[onclick],[contenteditable=true],summary,label';
      const nodes = document.querySelectorAll(sel);
      for (const el of nodes) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;
        const style = getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') continue;
        el.setAttribute('data-mcp-ref', String(i));
        const label = (el.getAttribute('aria-label') || el.innerText || el.value ||
                       el.getAttribute('placeholder') || el.getAttribute('name') ||
                       el.getAttribute('title') || '').trim().slice(0, 120);
        out.push({
          ref: i, tag: el.tagName.toLowerCase(),
          type: el.getAttribute('type') || undefined,
          role: el.getAttribute('role') || undefined,
          name: el.getAttribute('name') || undefined,
          text: label,
        });
        i++;
      }
      return {url: location.href, title: document.title, count: out.length, elements: out};
    }
    """
    data = await page.evaluate(js)
    return _truncate(json.dumps(data, ensure_ascii=False, indent=1), 25000)


@mcp.tool
async def browser_get_html(selector: str = "", max_chars: int = 20000) -> str:
    """Return outerHTML of the page (or of `selector` if given), truncated."""
    page = await BM.page()
    if selector:
        el = await page.query_selector(selector)
        if not el:
            return f"No element matches: {selector}"
        html = await el.evaluate("e => e.outerHTML")
    else:
        html = await page.content()
    return _truncate(html, max_chars)


@mcp.tool
async def browser_get_text(selector: str = "body", max_chars: int = 20000) -> str:
    """Return the visible innerText of the page or of a selector."""
    page = await BM.page()
    el = await page.query_selector(selector)
    if not el:
        return f"No element matches: {selector}"
    txt = await el.evaluate("e => e.innerText")
    return _truncate(txt, max_chars)


@mcp.tool
async def browser_query(selector: str, limit: int = 30) -> str:
    """Return details (text, key attributes) of elements matching a CSS selector."""
    page = await BM.page()
    js = """
    ([selector, limit]) => {
      const els = Array.from(document.querySelectorAll(selector)).slice(0, limit);
      return els.map(el => ({
        tag: el.tagName.toLowerCase(),
        text: (el.innerText || el.value || '').trim().slice(0, 200),
        href: el.getAttribute('href') || undefined,
        name: el.getAttribute('name') || undefined,
        id: el.id || undefined,
        type: el.getAttribute('type') || undefined,
      }));
    }
    """
    data = await page.evaluate(js, [selector, limit])
    return _truncate(json.dumps(data, ensure_ascii=False, indent=1))


# ---- interaction ---------------------------------------------------------- #
async def _locator(page: Page, ref: str, selector: str):
    if ref != "":
        return page.locator(f"[data-mcp-ref='{ref}']")
    return page.locator(selector)


@mcp.tool
async def browser_click(ref: str = "", selector: str = "", button: str = "left",
                        click_count: int = 1, timeout_ms: int = 15000) -> str:
    """Click an element by snapshot ref or CSS selector."""
    page = await BM.page()
    loc = await _locator(page, ref, selector)
    await loc.first.click(button=button, click_count=click_count, timeout=timeout_ms)
    return f"Clicked {'ref='+ref if ref else selector}"


@mcp.tool
async def browser_type(text: str, ref: str = "", selector: str = "",
                       clear: bool = True, submit: bool = False,
                       timeout_ms: int = 15000) -> str:
    """Type text into an input/textarea/contenteditable. Optionally clear first
    and press Enter after (submit)."""
    page = await BM.page()
    loc = (await _locator(page, ref, selector)).first
    if clear:
        await loc.fill("", timeout=timeout_ms)
    await loc.click(timeout=timeout_ms)
    await loc.type(text, delay=25)
    if submit:
        await loc.press("Enter")
    return f"Typed into {'ref='+ref if ref else selector}"


@mcp.tool
async def browser_fill_form(fields: list[dict], submit_ref: str = "",
                            submit_selector: str = "") -> str:
    """Fill multiple fields at once. Each field: {ref|selector, value, [type]}.
    type can be 'fill' (default), 'select', or 'check'. Optionally click a submit
    control afterwards."""
    page = await BM.page()
    results = []
    for f in fields:
        ref = f.get("ref", "")
        selector = f.get("selector", "")
        value = f.get("value", "")
        kind = f.get("type", "fill")
        loc = (await _locator(page, ref, selector)).first
        try:
            if kind == "select":
                await loc.select_option(value)
            elif kind == "check":
                if value in (True, "true", "1", 1):
                    await loc.check()
                else:
                    await loc.uncheck()
            else:
                await loc.fill(str(value))
            results.append({"field": ref or selector, "ok": True})
        except Exception as e:
            results.append({"field": ref or selector, "ok": False, "error": str(e)[:200]})
    if submit_ref or submit_selector:
        try:
            loc = (await _locator(page, submit_ref, submit_selector)).first
            await loc.click()
            results.append({"submit": True, "ok": True})
        except Exception as e:
            results.append({"submit": True, "ok": False, "error": str(e)[:200]})
    return json.dumps(results, ensure_ascii=False)


@mcp.tool
async def browser_select_option(values: list[str], ref: str = "", selector: str = "") -> str:
    """Select option(s) in a <select> element."""
    page = await BM.page()
    loc = (await _locator(page, ref, selector)).first
    await loc.select_option(values)
    return f"Selected {values}"


@mcp.tool
async def browser_hover(ref: str = "", selector: str = "") -> str:
    """Hover over an element."""
    page = await BM.page()
    loc = (await _locator(page, ref, selector)).first
    await loc.hover()
    return "Hovered."


@mcp.tool
async def browser_press_key(key: str, ref: str = "", selector: str = "") -> str:
    """Press a keyboard key (e.g. Enter, Escape, ArrowDown, Control+A). Targets an
    element if ref/selector given, else the page."""
    page = await BM.page()
    if ref or selector:
        loc = (await _locator(page, ref, selector)).first
        await loc.press(key)
    else:
        await page.keyboard.press(key)
    return f"Pressed {key}"


@mcp.tool
async def browser_scroll(direction: str = "down", amount: int = 800,
                         ref: str = "", selector: str = "") -> str:
    """Scroll the page (or an element into view). direction: down|up|to_element."""
    page = await BM.page()
    if direction == "to_element" and (ref or selector):
        loc = (await _locator(page, ref, selector)).first
        await loc.scroll_into_view_if_needed()
        return "Scrolled element into view."
    dy = amount if direction == "down" else -amount
    await page.mouse.wheel(0, dy)
    return f"Scrolled {direction} {amount}px"


@mcp.tool
async def browser_upload_file(paths: list[str], ref: str = "", selector: str = "") -> str:
    """Set files on a file <input>. Paths must exist inside the container."""
    page = await BM.page()
    loc = (await _locator(page, ref, selector)).first
    await loc.set_input_files(paths)
    return f"Uploaded {paths}"


@mcp.tool
async def browser_handle_dialog(accept: bool = True, prompt_text: str = "") -> str:
    """Arm a handler for the next JS dialog (alert/confirm/prompt)."""
    page = await BM.page()

    async def _h(dialog):
        try:
            if accept:
                await dialog.accept(prompt_text)
            else:
                await dialog.dismiss()
        except Exception:
            pass

    page.once("dialog", lambda d: asyncio.create_task(_h(d)))
    return f"Next dialog will be {'accepted' if accept else 'dismissed'}."


# ---- arbitrary JS (the power tool) --------------------------------------- #
@mcp.tool
async def browser_evaluate(script: str, arg: Any = None) -> str:
    """Run arbitrary JavaScript in the active page and return the JSON result.
    `script` should be a JS function expression, e.g. "() => document.title" or
    "(arg) => { ... return ...; }". Use this for anything the structured tools
    can't do: reading complex DOM, reverse-engineering a form's structure and
    submitting it, calling site APIs with the page's own credentials, etc."""
    page = await BM.page()
    try:
        result = await page.evaluate(script, arg)
    except PWError as e:
        return f"JS error: {e}"
    try:
        return _truncate(json.dumps(result, ensure_ascii=False, default=str))
    except TypeError:
        return _truncate(str(result))


@mcp.tool
async def browser_evaluate_on_element(script: str, ref: str = "", selector: str = "") -> str:
    """Run JS with a specific element as the first argument. `script` e.g.
    "el => el.getAttribute('href')"."""
    page = await BM.page()
    loc = (await _locator(page, ref, selector)).first
    handle = await loc.element_handle()
    if handle is None:
        return "Element not found."
    result = await page.evaluate(script, handle)
    try:
        return _truncate(json.dumps(result, ensure_ascii=False, default=str))
    except TypeError:
        return _truncate(str(result))


# ---- screenshot ----------------------------------------------------------- #
@mcp.tool
async def browser_screenshot(full_page: bool = False, ref: str = "",
                             selector: str = "") -> Image:
    """Take a PNG screenshot of the viewport, the full page, or one element."""
    page = await BM.page()
    if ref or selector:
        loc = (await _locator(page, ref, selector)).first
        data = await loc.screenshot(type="png")
    else:
        data = await page.screenshot(full_page=full_page, type="png")
    return Image(data=data, format="png")


# ---- tabs ----------------------------------------------------------------- #
@mcp.tool
async def browser_tabs_list() -> str:
    """List open tabs with their index, url and title."""
    ctx = await BM.ensure()
    tabs = []
    for i, p in enumerate(ctx.pages):
        try:
            tabs.append({"index": i, "url": p.url, "title": await p.title()})
        except Exception:
            tabs.append({"index": i, "url": p.url})
    return json.dumps(tabs, ensure_ascii=False)


@mcp.tool
async def browser_tab_new(url: str = "") -> str:
    """Open a new tab (optionally navigating to url) and make it active."""
    ctx = await BM.ensure()
    page = await ctx.new_page()
    if url:
        await page.goto(url)
    return f"Opened tab {len(ctx.pages) - 1} — {page.url}"


@mcp.tool
async def browser_tab_select(index: int) -> str:
    """Bring a tab to the front by index."""
    ctx = await BM.ensure()
    if index < 0 or index >= len(ctx.pages):
        return f"No tab at index {index}"
    await ctx.pages[index].bring_to_front()
    return f"Selected tab {index} — {ctx.pages[index].url}"


@mcp.tool
async def browser_tab_close(index: int) -> str:
    """Close a tab by index."""
    ctx = await BM.ensure()
    if index < 0 or index >= len(ctx.pages):
        return f"No tab at index {index}"
    await ctx.pages[index].close()
    return f"Closed tab {index}"


# ---- cookies / session ---------------------------------------------------- #
@mcp.tool
async def browser_get_cookies(url: str = "") -> str:
    """List cookies in the live browser (optionally filtered to a URL)."""
    ctx = await BM.ensure()
    cookies = await ctx.cookies(url) if url else await ctx.cookies()
    slim = [{"domain": c["domain"], "name": c["name"],
             "value": (c["value"][:40] + "…") if len(c["value"]) > 40 else c["value"]}
            for c in cookies]
    return _truncate(json.dumps(slim, ensure_ascii=False))


@mcp.tool
async def browser_set_cookies(cookies: list[dict]) -> str:
    """Add cookies to the live browser. Each: {name,value,domain,path,...} or url."""
    ctx = await BM.ensure()
    await ctx.add_cookies(cookies)
    return f"Added {len(cookies)} cookies."


@mcp.tool
async def sync_session(mode: str = "soft") -> str:
    """Refresh login state from the host (desktop) Chrome profile.
    mode='soft' -> inject latest cookies without disrupting browsing (fast).
    mode='hard' -> full profile refresh incl. localStorage/IndexedDB, relaunches
    the browser (use when a site uses non-cookie auth or still looks logged out)."""
    if mode == "hard":
        await BM.hard_sync()
        return "Hard sync complete: full profile refreshed and browser relaunched."
    n = await BM.soft_sync()
    return f"Soft sync complete: injected {n} cookies from the host profile."


@mcp.tool
async def session_status() -> str:
    """Report sync timestamps and a sample of domains the browser holds cookies for."""
    ctx = await BM.ensure()
    cookies = await ctx.cookies()
    domains = sorted({c["domain"].lstrip(".") for c in cookies})
    return json.dumps({
        "cookie_count": len(cookies),
        "distinct_domains": len(domains),
        "sample_domains": domains[:40],
        "last_soft_sync_epoch": int(BM.last_soft_sync),
        "last_hard_sync_epoch": int(BM.last_hard_sync),
        "soft_sync_interval_s": SOFT_SYNC_INTERVAL,
    }, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Entrypoint: streamable HTTP with static bearer-token auth
# --------------------------------------------------------------------------- #
def build_app():
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse, PlainTextResponse
    from starlette.routing import Route

    class TokenAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.url.path == "/health":
                return await call_next(request)
            if MCP_TOKEN:
                auth = request.headers.get("authorization", "")
                token = auth[7:] if auth.lower().startswith("bearer ") else ""
                if not token:
                    token = request.query_params.get("token", "")
                if token != MCP_TOKEN:
                    return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    async def health(_request):
        return PlainTextResponse("ok")

    app = mcp.http_app(
        path="/mcp",
        middleware=[Middleware(TokenAuth)],
    )
    app.router.routes.append(Route("/health", health))
    return app


if __name__ == "__main__":
    import uvicorn
    app = build_app()
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT, log_level="info")
