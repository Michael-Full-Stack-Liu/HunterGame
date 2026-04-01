import os
import json
import asyncio
import shutil
import re
import socket
import uuid
from asyncio.subprocess import PIPE
from contextlib import asynccontextmanager
import aiosmtplib
import aioimaplib
from email.message import EmailMessage
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from urllib.parse import quote_plus, urlparse, parse_qs
from playwright.async_api import async_playwright

from harness_engine.core.logger import logger, tool_monitor
from harness_engine.config import config
from langchain_core.tools import tool

class EmailActuator:
    """Handles creating email drafts via IMAP or sending via SMTP."""
    
    def __init__(self):
        # SMTP (for sending if needed later)
        self.smtp_server = config.get("email.smtp_server")
        self.smtp_port = int(config.get("email.smtp_port", 587))
        # IMAP (for Drafts)
        self.imap_server = config.get("email.imap_server", "imap.gmail.com")
        self.imap_port = int(config.get("email.imap_port", 993))
        self.user = config.get("email.user")
        self.password = config.get("email.password")
        self.sender_name = config.get("email.sender_name", "Job Hunter")
        self.drafts_folder = config.get("email.drafts_folder", "[Gmail]/Drafts")
        self._resolved_drafts_folder: Optional[str] = None

    async def _detect_drafts_folder(self, imap) -> str:
        if self._resolved_drafts_folder:
            return self._resolved_drafts_folder

        try:
            resp = await imap.list('""', '*')
            for line in resp.lines:
                text = line.decode("utf-8", errors="ignore") if isinstance(line, bytes) else str(line)
                if "\\Drafts" not in text:
                    continue
                match = re.search(r'"([^"]+)"\s*$', text)
                if match:
                    self._resolved_drafts_folder = match.group(1)
                    return self._resolved_drafts_folder
        except Exception as e:
            logger.warn(f"Failed to auto-detect drafts folder: {e}")

        self._resolved_drafts_folder = self.drafts_folder
        return self._resolved_drafts_folder

    async def create_draft(self, to_email: str, subject: str, body: str) -> str:
        if not self.user or not self.password:
            return "Error: Email credentials not configured in config.yaml"
            
        msg = EmailMessage()
        msg["From"] = f"{self.sender_name} <{self.user}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(body)
        
        try:
            # Connect to IMAP
            imap = aioimaplib.IMAP4_SSL(host=self.imap_server, port=self.imap_port)
            await imap.wait_hello_from_server()
            await imap.login(self.user, self.password)
            drafts_folder = await self._detect_drafts_folder(imap)
            
            # Append message to Drafts
            # Note: The 'APPEND' command is standard IMAP
            # We need to encode the message as bytes
            await imap.append(
                msg.as_bytes(),
                mailbox=drafts_folder,
                flags="\\Draft",
                date=datetime.now().astimezone(),
            )
            
            await imap.logout()
            logger.info(f"Draft created in '{drafts_folder}' for {to_email}")
            return (
                f"Success: Draft created for {to_email}. "
                "Please check your Gmail Drafts/草稿 folder to review and send."
            )
        except Exception as e:
            logger.error(f"Failed to create draft: {e}")
            return f"Error creating draft: {str(e)}"

class BrowserActuator:
    """Handles automated job form filling via Playwright."""

    def __init__(self):
        self.browser_enabled = bool(config.get("automation.browser.enabled", True))
        self.browser_mode = str(config.get("automation.browser.mode", "cdp")).lower()
        self.cdp_url = config.get("automation.browser.cdp_url", "http://127.0.0.1:9222")
        self.headless_fallback = bool(config.get("automation.browser.headless_fallback", True))
        self.auto_start = bool(config.get("automation.browser.auto_start", True))
        self.chrome_command = config.get("automation.browser.chrome_command", shutil.which("google-chrome") or "google-chrome")
        self.remote_debugging_port = int(config.get("automation.browser.remote_debugging_port", 9222))
        self.user_data_dir = config.get("automation.browser.user_data_dir", os.path.expanduser("~/.config/google-chrome-remote-debug"))
        self.startup_wait_seconds = int(config.get("automation.browser.startup_wait_seconds", 8))

    @staticmethod
    def _is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def _cdp_host_port(self) -> Tuple[str, int]:
        target = self.cdp_url.replace("http://", "").replace("https://", "").split("/", 1)[0]
        if ":" in target:
            host, port_text = target.rsplit(":", 1)
            return host, int(port_text)
        return target, 80

    async def bootstrap_browser(self) -> str:
        host, port = self._cdp_host_port()
        if self._is_port_open(host, port):
            return json.dumps(
                {
                    "status": "already_running",
                    "cdp_url": self.cdp_url,
                    "user_data_dir": self.user_data_dir,
                },
                ensure_ascii=False,
            )

        if not self.auto_start:
            return json.dumps(
                {
                    "status": "not_running",
                    "auto_start": False,
                    "cdp_url": self.cdp_url,
                },
                ensure_ascii=False,
            )

        chrome_path = self.chrome_command or shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
        if not chrome_path:
            return json.dumps(
                {
                    "status": "bootstrap_failed",
                    "reason": "chrome_not_found",
                },
                ensure_ascii=False,
            )

        os.makedirs(self.user_data_dir, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            chrome_path,
            f"--remote-debugging-port={self.remote_debugging_port}",
            f"--user-data-dir={self.user_data_dir}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )

        for _ in range(max(self.startup_wait_seconds * 2, 1)):
            await asyncio.sleep(0.5)
            if self._is_port_open(host, port):
                return json.dumps(
                    {
                        "status": "started",
                        "cdp_url": self.cdp_url,
                        "user_data_dir": self.user_data_dir,
                        "pid": process.pid,
                    },
                    ensure_ascii=False,
                )

        return json.dumps(
            {
                "status": "bootstrap_failed",
                "reason": "cdp_not_reachable_after_launch",
                "cdp_url": self.cdp_url,
                "user_data_dir": self.user_data_dir,
                "pid": process.pid,
            },
            ensure_ascii=False,
        )

    @asynccontextmanager
    async def session(self, viewport: Optional[Dict[str, int]] = None):
        async with async_playwright() as p:
            browser = None
            context = None
            use_cdp = self.browser_enabled and self.browser_mode == "cdp"

            if use_cdp:
                await self.bootstrap_browser()
                try:
                    browser = await p.chromium.connect_over_cdp(self.cdp_url)
                    if browser.contexts:
                        context = browser.contexts[0]
                    else:
                        context = await browser.new_context(viewport=viewport or {"width": 1280, "height": 800})
                except Exception as e:
                    logger.warn(f"CDP browser connection failed: {e}")
                    if not self.headless_fallback:
                        raise

            if context is None:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(viewport=viewport or {"width": 1280, "height": 800})

            try:
                yield context
            finally:
                try:
                    if context and not use_cdp:
                        await context.close()
                finally:
                    if browser and not use_cdp:
                        await browser.close()

    @staticmethod
    async def _try_fill_first(page, selectors, value: str) -> bool:
        for sel in selectors:
            try:
                element = await page.wait_for_selector(sel, timeout=1500)
                if element:
                    await element.fill(value)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    async def _try_set_file(page, selectors, file_path: str) -> bool:
        for sel in selectors:
            try:
                element = await page.wait_for_selector(sel, timeout=1500)
                if element:
                    await element.set_input_files(file_path)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    async def _try_click_first(page, selectors) -> bool:
        for sel in selectors:
            try:
                locator = page.locator(sel).first
                await locator.wait_for(timeout=2500)
                await locator.scroll_into_view_if_needed(timeout=1500)
                try:
                    await locator.click(timeout=2000)
                except Exception:
                    await locator.click(timeout=2000, force=True)
                return True
            except Exception:
                continue
        return False

    @staticmethod
    async def _get_linkedin_access_issue(page) -> Optional[str]:
        lowered_url = page.url.lower()
        if "linkedin.com/login" in lowered_url:
            return "auth_wall"
        if "linkedin.com/checkpoint/" in lowered_url:
            return "bot_detection"
        try:
            body = (await page.locator("body").inner_text(timeout=3000)).lower()
        except Exception:
            return None
        auth_hints = [
            "sign in",
            "join now",
            "log in",
            "登录",
        ]
        if any(hint in body for hint in auth_hints):
            return "auth_wall"
        bot_hints = [
            "unusual activity",
            "security verification",
            "let us know you're human",
            "verify your identity",
            "complete a quick security check",
            "captcha",
            "challenge",
        ]
        if any(hint in body for hint in bot_hints):
            return "bot_detection"
        return None

    @staticmethod
    def _linkedin_access_issue_message(issue: str) -> str:
        if issue == "auth_wall":
            return "LinkedIn automation stopped: browser session is not logged into LinkedIn."
        if issue == "bot_detection":
            return (
                "LinkedIn automation stopped: LinkedIn triggered a verification or bot-detection challenge. "
                "Do not continue automated clicks in this cycle."
            )
        return "LinkedIn automation stopped: access to LinkedIn is currently blocked."

    @staticmethod
    async def _dismiss_linkedin_overlays(page) -> None:
        await BrowserActuator._try_click_first(
            page,
            [
                "button[aria-label*='Dismiss' i]",
                "button[aria-label*='Close' i]",
                "button:has-text('Not now')",
                "button:has-text('Close')",
                "button:has-text('Dismiss')",
            ],
        )

    @staticmethod
    def _linkedin_vanity_from_url(profile_url: str) -> str:
        parsed = urlparse(profile_url)
        path = (parsed.path or "").strip("/")
        if "/in/" in f"/{path}":
            slug = path.split("/in/", 1)[-1].split("/", 1)[0]
            if slug:
                return slug
        if path.startswith("in/"):
            slug = path.split("/", 1)[-1]
            if slug:
                return slug
        return ""

    async def _click_top_profile_connect(self, page, profile_url: str) -> Tuple[bool, str]:
        vanity = self._linkedin_vanity_from_url(profile_url)
        if vanity:
            selectors = [
                f"main a[href*='/preload/custom-invite/'][href*='vanityName={vanity}']",
                f"a[href*='/preload/custom-invite/'][href*='vanityName={vanity}']",
                f"main a[aria-label*='Invite'][href*='{vanity}']",
                f"a[aria-label*='Invite'][href*='{vanity}']",
            ]
            if await self._try_click_first(page, selectors):
                await page.wait_for_timeout(900)
                return True, "profile_invite_link"

        clicked = await page.evaluate(
            """
            () => {
              const candidates = Array.from(
                document.querySelectorAll("main a[href*='/preload/custom-invite/'], main button, main a")
              );
              const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
              const matches = candidates
                .map((el) => {
                  const text = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                  const aria = (el.getAttribute('aria-label') || '').trim();
                  const href = (el.getAttribute('href') || '').trim();
                  const rect = el.getBoundingClientRect();
                  return {el, text, aria, href, top: rect.top, left: rect.left};
                })
                .filter((item) =>
                  visible(item.el) &&
                  item.top >= 0 &&
                  item.top < 650 &&
                  item.left < 900 &&
                  (
                    item.text === 'Connect' ||
                    item.aria.toLowerCase().includes('invite') ||
                    item.href.includes('/preload/custom-invite/')
                  )
                )
                .sort((a, b) => a.top - b.top || a.left - b.left);
              if (!matches.length) return false;
              matches[0].el.click();
              return true;
            }
            """
        )
        if clicked:
            await page.wait_for_timeout(900)
            return True, "top_profile_actions"
        return False, "target_profile_connect_not_found"

    async def _open_linkedin_connect_flow(self, page, profile_url: str) -> Tuple[bool, str]:
        await self._dismiss_linkedin_overlays(page)

        clicked_target, target_path = await self._click_top_profile_connect(page, profile_url)
        if clicked_target:
            return True, target_path

        direct_clicked = await self._try_click_first(
            page,
            [
                "button:has-text('Connect')",
                "button[aria-label*='Connect' i]",
                "[aria-label*='Invite' i]",
                "div[role='button']:has-text('Connect')",
                "span:has-text('Connect')",
            ],
        )
        if direct_clicked:
            await page.wait_for_timeout(800)
            return True, "direct"

        more_clicked = await self._try_click_first(
            page,
            [
                "button[aria-label*='More actions' i]",
                "button[aria-label*='Open actions overflow menu' i]",
                "button[aria-label*='More' i]",
                "button:has-text('More')",
            ],
        )
        if not more_clicked:
            return False, "connect_button_not_found"

        await page.wait_for_timeout(600)
        menu_clicked = await self._try_click_first(
            page,
            [
                "div[role='menuitem']:has-text('Connect')",
                "li:has-text('Connect')",
                "button:has-text('Connect')",
                "span:has-text('Connect')",
            ],
        )
        if not menu_clicked:
            return False, "connect_menu_item_not_found"

        await page.wait_for_timeout(800)
        return True, "overflow_menu"

    async def _send_linkedin_connection(self, page, note: str = "") -> Tuple[bool, str]:
        if note.strip():
            await self._try_click_first(
                page,
                [
                    "button:has-text('Add a note')",
                    "button[aria-label*='Add a note' i]",
                ],
            )
            await page.wait_for_timeout(300)
            filled = await self._try_fill_first(
                page,
                [
                    "textarea[name='message']",
                    "div[role='dialog'] textarea",
                    "textarea",
                ],
                note.strip(),
            )
            if not filled:
                return False, "note_requested_but_textarea_not_found"

        sent = await self._try_click_first(
            page,
            [
                "button:has-text('Send without a note')",
                "button[aria-label*='Send invitation' i]",
                "div[role='dialog'] button:has-text('Send')",
                "button:has-text('Send')",
            ],
        )
        if not sent:
            return False, "send_button_not_found"
        return True, "sent"

    async def _open_page(self, url: str):
        context_manager = self.session()
        context = await context_manager.__aenter__()
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(1200)
            return context_manager, page
        except Exception:
            await context_manager.__aexit__(None, None, None)
            raise

    @staticmethod
    async def _detect_linkedin_status(page) -> str:
        content = await page.locator("body").inner_text(timeout=5000)
        lowered = content.lower()
        if await page.locator("button:has-text('Message')").count():
            return "connected"
        if "pending" in lowered or await page.locator("button:has-text('Pending')").count():
            return "pending"
        if await page.locator("button:has-text('Connect')").count():
            return "not_connected"
        if "1st" in content:
            return "connected"
        if "2nd" in content or "3rd" in content:
            return "not_connected"
        return "unknown"

    async def linkedin_connection_status(self, profile_url: str) -> str:
        session_manager, page = await self._open_page(profile_url)
        try:
            access_issue = await self._get_linkedin_access_issue(page)
            if access_issue:
                return json.dumps(
                    {
                        "status": "blocked",
                        "reason": access_issue,
                        "url": page.url,
                        "message": self._linkedin_access_issue_message(access_issue),
                    },
                    ensure_ascii=False,
                )
            status = await self._detect_linkedin_status(page)
            return json.dumps({"status": status, "url": page.url}, ensure_ascii=False)
        finally:
            await page.close()
            await session_manager.__aexit__(None, None, None)

    async def linkedin_search_people(
        self,
        company: str,
        role_keywords: str = "",
        location: str = "",
        limit: int = 5,
    ) -> str:
        company_text = company.strip()
        location_text = location.strip()
        role_text = role_keywords.strip()
        query_variants = []
        if company_text and role_text:
            query_variants.append(f'"{company_text}" {role_text}')
        query_variants.append(" ".join(part for part in [company_text, role_text, location_text] if part))
        if company_text and location_text:
            query_variants.append(f'"{company_text}" {location_text}')
        query_variants = [q.strip() for q in query_variants if q.strip()]

        company_pattern = re.compile(rf"\\b{re.escape(company_text)}\\b", re.IGNORECASE) if company_text else None
        attempts = []

        for query in query_variants:
            search_url = f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(query)}"
            session_manager, page = await self._open_page(search_url)
            try:
                access_issue = await self._get_linkedin_access_issue(page)
                if access_issue:
                    return json.dumps(
                        {
                            "query": query,
                            "search_url": page.url,
                            "candidates": [],
                            "attempts": attempts,
                            "blocked": True,
                            "reason": access_issue,
                            "note": self._linkedin_access_issue_message(access_issue),
                        },
                        ensure_ascii=False,
                    )
                await page.wait_for_timeout(2500)
                for _ in range(3):
                    await page.mouse.wheel(0, 1800)
                    await page.wait_for_timeout(900)
                raw_candidates = await page.evaluate(
                    """(maxItems) => {
                        const results = [];
                        const seen = new Set();
                        const anchors = Array.from(document.querySelectorAll('a[href*="/in/"]'));
                        for (const anchor of anchors) {
                          const href = anchor.href || '';
                          if (!href.includes('/in/')) continue;
                          const normalized = href.split('?')[0];
                          if (seen.has(normalized)) continue;
                          const summary = (anchor.innerText || '').replace(/\\s+/g, ' ').trim();
                          if (!summary) continue;
                          results.push({
                            name: summary,
                            profile_url: normalized,
                            snippet: summary.slice(0, 400),
                          });
                          seen.add(normalized);
                          if (results.length >= maxItems) break;
                        }
                        return results;
                    }""",
                    max(limit * 10, 40),
                )
                candidates = raw_candidates
                if company_pattern:
                    candidates = [
                        item for item in raw_candidates
                        if company_pattern.search(str(item.get("name") or ""))
                        or company_pattern.search(str(item.get("snippet") or ""))
                    ]
                deduped = []
                seen_urls = set()
                for item in candidates:
                    profile = str(item.get("profile_url") or "")
                    if not profile or profile in seen_urls:
                        continue
                    seen_urls.add(profile)
                    deduped.append(item)
                    if len(deduped) >= limit:
                        break
                attempts.append(
                    {
                        "query": query,
                        "search_url": page.url,
                        "candidate_count": len(deduped),
                        "raw_candidate_count": len(raw_candidates),
                    }
                )
                if deduped:
                    return json.dumps(
                        {
                            "query": query,
                            "search_url": page.url,
                            "candidates": deduped,
                            "attempts": attempts,
                        },
                        ensure_ascii=False,
                    )
            finally:
                await page.close()
                await session_manager.__aexit__(None, None, None)

        return json.dumps(
            {
                "query": query_variants[0] if query_variants else "",
                "search_url": "",
                "candidates": [],
                "attempts": attempts,
                "note": "No LinkedIn people results matched the target company text.",
            },
            ensure_ascii=False,
        )

    async def linkedin_connect(self, profile_url: str, note: str = "") -> str:
        session_manager, page = await self._open_page(profile_url)
        try:
            access_issue = await self._get_linkedin_access_issue(page)
            if access_issue:
                return self._linkedin_access_issue_message(access_issue)

            status = await self._detect_linkedin_status(page)
            status_raw = json.dumps({"status": status, "url": page.url}, ensure_ascii=False)
            if status == "connected":
                return f"LinkedIn connection already exists. Status: {status_raw}"
            if status == "pending":
                return f"LinkedIn connection request is already pending. Status: {status_raw}"

            opened, path = await self._open_linkedin_connect_flow(page, profile_url)
            if not opened:
                return f"LinkedIn browser connect failed: {path}."

            sent, send_reason = await self._send_linkedin_connection(page, note=note)
            if not sent:
                return f"LinkedIn browser connect failed: {send_reason}."
            return f"LinkedIn connection request sent via {path}."
        finally:
            await page.close()
            await session_manager.__aexit__(None, None, None)

    async def linkedin_send_message(self, profile_url: str, message: str) -> str:
        session_manager, page = await self._open_page(profile_url)
        try:
            status = await self._detect_linkedin_status(page)
            status_raw = json.dumps({"status": status, "url": page.url}, ensure_ascii=False)
            if status != "connected":
                return (
                    "LinkedIn message blocked: target is not a first-degree connection yet. "
                    f"Current status: {status}. Raw status: {status_raw}"
                )

            opened = await self._try_click_first(
                page,
                [
                    "button:has-text('Message')",
                    "[aria-label*='Message' i]",
                ],
            )
            if not opened:
                return "LinkedIn browser message failed: could not open the message composer."

            await self._try_fill_first(
                page,
                ["div[contenteditable='true']", "textarea"],
                message.strip(),
            )
            sent = await self._try_click_first(
                page,
                [
                    "button:has-text('Send')",
                    "button[aria-label*='Send' i]",
                ],
            )
            if not sent:
                return "LinkedIn browser message failed: composer opened, but send button was not found."
            return "LinkedIn message sent."
        finally:
            await page.close()
            await session_manager.__aexit__(None, None, None)

    async def linkedin_connect_preview(self, profile_url: str) -> str:
        session_manager, page = await self._open_page(profile_url)
        try:
            access_issue = await self._get_linkedin_access_issue(page)
            if access_issue:
                return json.dumps(
                    {
                        "status": "blocked",
                        "reason": access_issue,
                        "connect_flow_opened": False,
                        "add_note_available": False,
                        "send_available": False,
                        "url": page.url,
                        "message": self._linkedin_access_issue_message(access_issue),
                    },
                    ensure_ascii=False,
                )

            status = await self._detect_linkedin_status(page)
            status_raw = json.dumps({"status": status, "url": page.url}, ensure_ascii=False)
            if status == "connected":
                return f"LinkedIn connect preview: already connected. Status: {status_raw}"
            if status == "pending":
                return f"LinkedIn connect preview: request already pending. Status: {status_raw}"

            opened, path = await self._open_linkedin_connect_flow(page, profile_url)
            if not opened:
                return f"LinkedIn connect preview failed: {path}."

            add_note_available = await page.locator("button:has-text('Add a note')").count() > 0
            send_available = (
                await page.locator("button:has-text('Send')").count() > 0
                or await page.locator("button:has-text('Send without a note')").count() > 0
                or await page.locator("button[aria-label*='Send invitation' i]").count() > 0
            )
            await self._dismiss_linkedin_overlays(page)
            await self._try_click_first(page, ["button:has-text('Cancel')"])
            return json.dumps(
                {
                    "status": status,
                    "connect_flow_opened": True,
                    "add_note_available": bool(add_note_available),
                    "send_available": bool(send_available),
                    "path": path,
                    "url": page.url,
                },
                ensure_ascii=False,
            )
        finally:
            await page.close()
            await session_manager.__aexit__(None, None, None)

    async def fill_form(self, url: str, profile_data: Dict[str, Any], submit: bool = False, force_submit: bool = False) -> str:
        async with self.session(viewport={"width": 1280, "height": 800}) as context:
            page = await context.new_page()
            
            try:
                logger.info(f"Navigating to {url} for form filling...")
                await page.goto(url, wait_until="networkidle")
                
                fields_to_fill = {
                    "first name": profile_data.get("first_name"),
                    "last name": profile_data.get("last_name"),
                    "email": profile_data.get("email"),
                    "phone": profile_data.get("phone"),
                    "linkedin": profile_data.get("linkedin_url"),
                    "github": profile_data.get("github_url"),
                    "website": profile_data.get("portfolio_url"),
                }
                
                for label, value in fields_to_fill.items():
                    if not value or str(value).lower() in {"none", "null", ""}:
                        continue
                    selectors = [
                        f"input[placeholder*='{label}' i]",
                        f"input[name*='{label}' i]",
                        f"label:has-text('{label}') + input",
                        f"input[aria-label*='{label}' i]"
                    ]
                    if await self._try_fill_first(page, selectors, value):
                        logger.info(f"Filled field '{label}'")

                upload_results = []
                if config.get("automation.application.auto_upload_documents", True):
                    resume_path = config.get("personal.application_resume_path", "data/optimized_resume.pdf")
                    cover_letter_path = config.get("personal.cover_letter_path", "data/optimized_cover_letter.pdf")

                    resume_selectors = [
                        "input[type='file'][name*='resume' i]",
                        "input[type='file'][id*='resume' i]",
                        "label:has-text('Resume') input[type='file']",
                        "input[type='file']",
                    ]
                    cover_selectors = [
                        "input[type='file'][name*='cover' i]",
                        "input[type='file'][id*='cover' i]",
                        "label:has-text('Cover') input[type='file']",
                    ]

                    if resume_path and os.path.exists(resume_path):
                        if await self._try_set_file(page, resume_selectors, resume_path):
                            upload_results.append("resume_uploaded")
                            logger.info("Uploaded resume file.")

                    if cover_letter_path and os.path.exists(cover_letter_path):
                        if await self._try_set_file(page, cover_selectors, cover_letter_path):
                            upload_results.append("cover_letter_uploaded")
                            logger.info("Uploaded cover letter file.")

                submission_status = "not_submitted"
                if submit:
                    if not force_submit and not config.get("automation.application.allow_submit", False):
                        submission_status = "submit_blocked_by_config"
                    else:
                        submit_selectors = [
                            "button:has-text('Submit')",
                            "button:has-text('Apply')",
                            "input[type='submit']",
                            "button[type='submit']",
                        ]
                        for sel in submit_selectors:
                            try:
                                button = await page.wait_for_selector(sel, timeout=1500)
                                if button:
                                    await button.click()
                                    submission_status = "submitted"
                                    logger.info("Submitted application form.")
                                    break
                            except Exception:
                                continue
                
                # Take screenshot for user confirmation
                screenshot_path = f"data/screenshots/apply_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                if config.get("automation.application.screenshot_after_fill", True):
                    os.makedirs("data/screenshots", exist_ok=True)
                    await page.screenshot(path=screenshot_path)
                
                await page.close()
                return (
                    f"Success: Application flow processed for {url}. "
                    f"Uploads: {upload_results or ['none']}. "
                    f"Submission: {submission_status}. "
                    f"Screenshot: {screenshot_path if config.get('automation.application.screenshot_after_fill', True) else 'disabled'}."
                )
            except Exception as e:
                await page.close()
                logger.error(f"Form filling failed: {e}")
                return f"Error filling form: {str(e)}"


class ApplicationApprovalStore:
    def __init__(self, path: str = "data/pending_approvals.json"):
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            return {"applications": []}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("applications"), list):
                return data
        except Exception:
            pass
        return {"applications": []}

    def _save(self, payload: Dict[str, Any]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def create_application(self, url: str, prefill_result: str) -> Dict[str, Any]:
        payload = self._load()
        approval_id = uuid.uuid4().hex[:8]
        screenshot_match = re.search(r"Screenshot:\s*([^\.\n]+(?:\.[A-Za-z0-9]+)?)", prefill_result)
        record = {
            "id": approval_id,
            "type": "application_submit",
            "url": url,
            "prefill_result": prefill_result,
            "screenshot": screenshot_match.group(1).strip() if screenshot_match else "",
            "status": "pending",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        payload["applications"].append(record)
        self._save(payload)
        return record

    def list_pending(self) -> list[Dict[str, Any]]:
        payload = self._load()
        return [item for item in payload.get("applications", []) if item.get("status") == "pending"]

    def get(self, approval_id: str) -> Optional[Dict[str, Any]]:
        payload = self._load()
        for item in payload.get("applications", []):
            if item.get("id") == approval_id:
                return item
        return None

    def resolve(self, approval_id: str, status: str, extra: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        payload = self._load()
        for item in payload.get("applications", []):
            if item.get("id") == approval_id:
                item["status"] = status
                item["resolved_at"] = datetime.now().isoformat(timespec="seconds")
                if extra:
                    item.update(extra)
                self._save(payload)
                return item
        return None


class LinkedInActuator:
    """Handles LinkedIn actions through the linkedin CLI when available."""

    def __init__(self):
        self.enabled = bool(config.get("automation.linkedin.enabled", True))
        self.mode = str(config.get("automation.linkedin.mode", "browser")).lower()
        self.cli_command = config.get("automation.linkedin.cli_command", "linkedin")
        self.allow_connect = bool(config.get("automation.linkedin.allow_connect", True))
        self.allow_message = bool(config.get("automation.linkedin.allow_message", True))

    def _resolve_cli(self) -> Optional[str]:
        return shutil.which(self.cli_command)

    @staticmethod
    def _walk_values(payload: Any):
        if isinstance(payload, dict):
            for key, value in payload.items():
                yield key, value
                yield from LinkedInActuator._walk_values(value)
        elif isinstance(payload, list):
            for item in payload:
                yield from LinkedInActuator._walk_values(item)

    @staticmethod
    def _normalize_connection_state(raw_output: str) -> str:
        text = (raw_output or "").strip()
        if not text:
            return "unknown"

        lowered = text.lower()
        try:
            payload = json.loads(text)
        except Exception:
            payload = None

        if payload is not None:
            bool_hints = {
                "is_connected",
                "connected",
                "first_degree",
                "is_first_degree",
            }
            status_hints = {
                "status",
                "connection_status",
                "relationship",
                "degree",
                "network_distance",
            }
            for key, value in LinkedInActuator._walk_values(payload):
                key_text = str(key).lower()
                if key_text in bool_hints and isinstance(value, bool):
                    return "connected" if value else "not_connected"
                if key_text in status_hints:
                    value_text = str(value).lower()
                    if any(token in value_text for token in ["not_connected", "2nd", "3rd", "out_of_network", "second-degree"]):
                        return "not_connected"
                    if any(token in value_text for token in ["pending", "invited", "requested"]):
                        return "pending"
                    if any(token in value_text for token in ["connected", "1st", "first_degree", "first-degree"]):
                        return "connected"

        if any(token in lowered for token in ["not_connected", "second-degree", "third-degree", "out_of_network", "\"2nd\"", "\"3rd\""]):
            return "not_connected"
        if any(token in lowered for token in ["pending", "invited", "requested"]):
            return "pending"
        if any(token in lowered for token in ["connected", "first-degree", "first_degree", "\"1st\"", " 1st"]):
            return "connected"
        return "unknown"

    async def _run_cli(self, *args: str) -> Tuple[int, str, str]:
        cli = self._resolve_cli()
        if not self.enabled:
            return 1, "", "LinkedIn automation disabled in config."
        if not cli:
            return 1, "", "linkedin CLI not installed or not found in PATH."

        process = await asyncio.create_subprocess_exec(
            cli,
            *args,
            stdout=PIPE,
            stderr=PIPE,
        )
        stdout, stderr = await process.communicate()
        return process.returncode, stdout.decode("utf-8", errors="ignore"), stderr.decode("utf-8", errors="ignore")

    async def connection_status(self, profile_url: str) -> str:
        if self.mode in {"browser", "auto"}:
            try:
                return await _browser_actuator.linkedin_connection_status(profile_url)
            except Exception as e:
                if self.mode == "browser":
                    return f"LinkedIn browser status check failed: {e}"
        code, stdout, stderr = await self._run_cli("connection", "status", profile_url, "--json", "-q")
        if code != 0:
            return f"LinkedIn status check failed: {stderr or stdout}"
        return stdout.strip() or "LinkedIn status check returned no output."

    async def connect(self, profile_url: str, note: str = "") -> str:
        if not self.allow_connect:
            return "LinkedIn connect action is disabled by config."
        if self.mode in {"browser", "auto"}:
            try:
                return await _browser_actuator.linkedin_connect(profile_url, note=note)
            except Exception as e:
                if self.mode == "browser":
                    return f"LinkedIn browser connect failed: {e}"
        status_raw = await self.connection_status(profile_url)
        status = self._normalize_connection_state(status_raw)
        if status == "connected":
            return f"LinkedIn connection already exists. Status: {status_raw}"
        if status == "pending":
            return f"LinkedIn connection request is already pending. Status: {status_raw}"
        args = ["connection", "send", profile_url]
        if note.strip():
            args.extend(["--note", note.strip()])
        args.extend(["--json", "-q"])
        code, stdout, stderr = await self._run_cli(*args)
        if code != 0:
            return f"LinkedIn connection request failed: {stderr or stdout}"
        return stdout.strip() or "LinkedIn connection request sent."

    async def send_message(self, profile_url: str, message: str) -> str:
        if not self.allow_message:
            return "LinkedIn message action is disabled by config."
        if self.mode in {"browser", "auto"}:
            try:
                return await _browser_actuator.linkedin_send_message(profile_url, message=message)
            except Exception as e:
                if self.mode == "browser":
                    return f"LinkedIn browser message failed: {e}"
        status_raw = await self.connection_status(profile_url)
        status = self._normalize_connection_state(status_raw)
        if status != "connected":
            return (
                "LinkedIn message blocked: target is not a first-degree connection yet. "
                f"Current status: {status}. Use linkedin_connect first. Raw status: {status_raw}"
            )
        args = ["message", "send", profile_url, message.strip(), "--json", "-q"]
        code, stdout, stderr = await self._run_cli(*args)
        if code != 0:
            return f"LinkedIn message send failed: {stderr or stdout}"
        return stdout.strip() or "LinkedIn message sent."

    async def connect_or_message(self, profile_url: str, connection_note: str = "", message: str = "") -> str:
        status_raw = await self.connection_status(profile_url)
        if status_raw.startswith("LinkedIn status check failed:"):
            return status_raw

        status = self._normalize_connection_state(status_raw)
        if status == "connected":
            if not message.strip():
                return "LinkedIn target is already connected. Provide a message to send."
            return await self.send_message(profile_url, message)
        if status == "pending":
            return (
                "LinkedIn connection request is still pending. Do not send a referral ask yet. "
                f"Raw status: {status_raw}"
            )
        return await self.connect(profile_url, note=connection_note)

# Singleton Actuators
_email_actuator = EmailActuator()
_browser_actuator = BrowserActuator()
_linkedin_actuator = LinkedInActuator()
_approval_store = ApplicationApprovalStore()

@tool
@tool_monitor
async def create_email_draft(to_email: str, subject: str, body: str):
    """Creates a professional cold email draft in your mailbox's 'Drafts' folder. 
    Use this to prepare outreach that you will personally review and send.
    """
    logger.tool_call("create_email_draft", {"to": to_email, "subject": subject})
    return await _email_actuator.create_draft(to_email, subject, body)

@tool
@tool_monitor
async def fill_job_form(url: str):
    """Automatically navigates to a job application URL and pre-fills the form using user profile data.
    After filling, it will generate a screenshot for verification before submission.
    """
    logger.tool_call("fill_job_form", {"url": url})
    
    profile_path = "data/profile.json"
    if not os.path.exists(profile_path):
        return "Error: Personal profile data not found at data/profile.json. Please create it first."
    
    with open(profile_path, "r", encoding="utf-8") as f:
        profile = json.load(f)
        
    return await _browser_actuator.fill_form(url, profile, submit=False)

@tool
@tool_monitor
async def apply_on_company_site(url: str, submit: bool = False):
    """Navigate to an official application page, fill known fields, upload resume/cover letter, and optionally submit if config allows."""
    logger.tool_call("apply_on_company_site", {"url": url, "submit": submit})

    profile_path = "data/profile.json"
    if not os.path.exists(profile_path):
        return "Error: Personal profile data not found at data/profile.json. Please create it first."

    with open(profile_path, "r", encoding="utf-8") as f:
        profile = json.load(f)

    response = await _browser_actuator.fill_form(url, profile, submit=submit)
    require_approval = bool(config.get("automation.application.require_submit_approval", True))
    notify_for_submission = bool(config.get("automation.application.notify_for_submission", True))
    if not submit and require_approval and response.startswith("Success: Application flow processed"):
        record = _approval_store.create_application(url=url, prefill_result=response)
        if notify_for_submission:
            try:
                from harness_engine.channels import telegram as telegram_channel
                message = (
                    "📝 官网申请已预填，等待你的提交授权。\n"
                    f"- approval_id: `{record['id']}`\n"
                    f"- url: {url}\n"
                    f"- screenshot: {record['screenshot'] or 'not captured'}\n\n"
                    f"使用 `/approve_apply {record['id']}` 提交，或 `/reject_apply {record['id']}` 放弃。"
                )
                await telegram_channel.send_message(message)
            except Exception as e:
                logger.warn(f"Failed to send application approval request: {e}")
        return (
            f"{response} Submission approval requested. "
            f"approval_id={record['id']}. Waiting for Telegram authorization before submit."
        )
    return response

@tool
@tool_monitor
async def linkedin_connection_status(profile_url: str):
    """Check the current LinkedIn connection status for a person profile."""
    logger.tool_call("linkedin_connection_status", {"profile_url": profile_url})
    return await _linkedin_actuator.connection_status(profile_url)

@tool
@tool_monitor
async def linkedin_search_people(company: str, role_keywords: str = "", location: str = "", limit: int = 5):
    """Search LinkedIn people results in a logged-in browser session and return likely contact candidates."""
    logger.tool_call(
        "linkedin_search_people",
        {"company": company, "role_keywords": role_keywords, "location": location, "limit": limit},
    )
    return await _browser_actuator.linkedin_search_people(
        company=company,
        role_keywords=role_keywords,
        location=location,
        limit=limit,
    )

@tool
@tool_monitor
async def linkedin_connect(profile_url: str, note: str = ""):
    """Send a LinkedIn connection request, optionally with a short note."""
    logger.tool_call("linkedin_connect", {"profile_url": profile_url})
    return await _linkedin_actuator.connect(profile_url, note=note)

@tool
@tool_monitor
async def linkedin_connect_preview(profile_url: str):
    """Open the LinkedIn connect flow and validate the UI without actually sending an invitation."""
    logger.tool_call("linkedin_connect_preview", {"profile_url": profile_url})
    return await _browser_actuator.linkedin_connect_preview(profile_url)

@tool
@tool_monitor
async def browser_bootstrap():
    """Ensure the remote-debug Chrome profile is running and ready for browser automation."""
    logger.tool_call("browser_bootstrap", {"cdp_url": _browser_actuator.cdp_url})
    return await _browser_actuator.bootstrap_browser()

@tool
@tool_monitor
async def linkedin_send_message(profile_url: str, message: str):
    """Send a LinkedIn direct message through the linkedin CLI, but only to first-degree connections."""
    logger.tool_call("linkedin_send_message", {"profile_url": profile_url})
    return await _linkedin_actuator.send_message(profile_url, message=message)

@tool
@tool_monitor
async def linkedin_referral_outreach(profile_url: str, connection_note: str = "", message: str = ""):
    """Connection-aware LinkedIn referral action: send a connection request first, or message only if already connected."""
    logger.tool_call("linkedin_referral_outreach", {"profile_url": profile_url})
    return await _linkedin_actuator.connect_or_message(
        profile_url,
        connection_note=connection_note,
        message=message,
    )

def get_actuator_tools():
    """Returns the set of execution-capable tools."""
    return [
        create_email_draft,
        fill_job_form,
        apply_on_company_site,
        browser_bootstrap,
        linkedin_search_people,
        linkedin_connection_status,
        linkedin_connect,
        linkedin_connect_preview,
        linkedin_send_message,
        linkedin_referral_outreach,
    ]
