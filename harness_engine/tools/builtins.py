import os
import asyncio
import json
import logging
import re
import yaml
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
import httpx
from google import genai
from google.genai import types
from harness_engine.core.logger import logger, tool_monitor
from harness_engine.config import config
from langchain_core.tools import tool
from pathlib import Path
from urllib.parse import urlparse

# [NEW] Optional: Import deepagents tools if we want to extend them
# from deepagents.tools import FileSystemTools, PlanningTools

_skill_loader = None
def get_skill_loader():
    global _skill_loader
    if _skill_loader is None:
        from harness_engine.core.skills import create_skill_loader
        _skill_loader = create_skill_loader()
    return _skill_loader

class FirecrawlTool:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.firecrawl.dev/v1"
        self.default_max_results = int(config.get("tools.firecrawl.max_results", 5))

    async def search(self, query: str, limit: int = 5) -> str:
        limit = limit or self.default_max_results
        logger.tool_call("firecrawl_search", {"query": query, "limit": limit})
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/search",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={"query": query, "limit": limit},
                    timeout=30.0
                )
                response.raise_for_status()
                data = response.json()
                results = data.get("data", [])
                if not results: return "No results found for your query. Try different keywords."
                output = [f"[{idx+1}] {r.get('title')}\nURL: {r.get('url')}\nContent: {r.get('markdown')[:500]}..." for idx, r in enumerate(results)]
                return "\n\n".join(output)
            except httpx.HTTPStatusError as e:
                logger.error(f"Firecrawl search failed: {e}")
                status_code = e.response.status_code if e.response else "unknown"
                return (
                    "WEB_SEARCH_UNAVAILABLE\n"
                    f"reason: http_status_{status_code}\n"
                    "guidance: external live search is unavailable right now. "
                    "Do not present fresh job openings as confirmed. Use fallback planning or existing known targets."
                )
            except httpx.RequestError as e:
                logger.error(f"Firecrawl search failed: {e}")
                return (
                    "WEB_SEARCH_UNAVAILABLE\n"
                    "reason: network_error\n"
                    "guidance: external live search is unavailable right now. "
                    "Do not present fresh job openings as confirmed. Use fallback planning or existing known targets."
                )
            except Exception as e:
                logger.error(f"Firecrawl search failed: {e}")
                return (
                    "WEB_SEARCH_UNAVAILABLE\n"
                    "reason: unexpected_error\n"
                    f"detail: {str(e)}\n"
                    "guidance: external live search is unavailable right now. "
                    "Do not present fresh job openings as confirmed. Use fallback planning or existing known targets."
                )

    async def fetch(self, url: str) -> str:
        logger.tool_call("firecrawl_fetch", {"url": url})
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/scrape",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={"url": url},
                    timeout=60.0
                )
                response.raise_for_status()
                data = response.json()
                return data.get("data", {}).get("markdown", "No content found.")
            except httpx.HTTPStatusError as e:
                logger.error(f"Firecrawl fetch failed: {e}")
                status_code = e.response.status_code if e.response else "unknown"
                return (
                    "WEB_FETCH_UNAVAILABLE\n"
                    f"reason: http_status_{status_code}\n"
                    "guidance: full-page retrieval is unavailable right now. Do not claim page contents you could not fetch."
                )
            except httpx.RequestError as e:
                logger.error(f"Firecrawl fetch failed: {e}")
                return (
                    "WEB_FETCH_UNAVAILABLE\n"
                    "reason: network_error\n"
                    "guidance: full-page retrieval is unavailable right now. Do not claim page contents you could not fetch."
                )
            except Exception as e:
                logger.error(f"Firecrawl fetch failed: {e}")
                return (
                    "WEB_FETCH_UNAVAILABLE\n"
                    "reason: unexpected_error\n"
                    f"detail: {str(e)}\n"
                    "guidance: full-page retrieval is unavailable right now. Do not claim page contents you could not fetch."
                )


class GeminiGroundedSearchTool:
    def __init__(self, api_key: str, model: str):
        self.client = genai.Client(api_key=api_key)
        self.model = model

    @staticmethod
    def _extract_text(response: Any) -> str:
        text = getattr(response, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            content = getattr(candidates[0], "content", None)
            if content is not None:
                return str(content).strip()
        return ""

    @staticmethod
    def _extract_sources(response: Any) -> List[str]:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return []
        grounding = getattr(candidates[0], "grounding_metadata", None)
        if not grounding:
            return []
        chunks = getattr(grounding, "grounding_chunks", None) or []
        urls: List[str] = []
        for chunk in chunks:
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", None) if web else None
            if isinstance(uri, str) and uri and uri not in urls:
                urls.append(uri)
        return urls[:5]

    @staticmethod
    def _extract_queries(response: Any) -> List[str]:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return []
        grounding = getattr(candidates[0], "grounding_metadata", None)
        if not grounding:
            return []
        queries = getattr(grounding, "web_search_queries", None) or []
        return [str(q).strip() for q in queries if str(q).strip()][:5]

    async def search(self, query: str) -> str:
        logger.tool_call("gemini_google_search", {"query": query, "model": self.model})

        def _run():
            return self.client.models.generate_content(
                model=self.model,
                contents=query,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                ),
            )

        try:
            response = await asyncio.to_thread(_run)
            text = self._extract_text(response)
            if not text:
                return (
                    "WEB_SEARCH_UNAVAILABLE\n"
                    "reason: empty_grounded_response\n"
                    "guidance: external live search returned no usable text."
                )
            queries = self._extract_queries(response)
            sources = self._extract_sources(response)
            parts = [text]
            if queries:
                parts.append("Search Queries:\n" + "\n".join(f"- {q}" for q in queries))
            if sources:
                parts.append("Sources:\n" + "\n".join(f"- {u}" for u in sources))
            return "\n\n".join(parts)
        except Exception as e:
            logger.error(f"Gemini grounded search failed: {e}")
            return (
                "WEB_SEARCH_UNAVAILABLE\n"
                f"reason: grounding_error\n"
                f"detail: {str(e)}\n"
                "guidance: external live search is unavailable right now. "
                "Do not present fresh job openings as confirmed. Use fallback planning or existing known targets."
            )


class JinaFetchTool:
    def __init__(self):
        self.base_url = "https://r.jina.ai/"

    async def fetch(self, url: str) -> str:
        logger.tool_call("jina_fetch", {"url": url})
        target = f"{self.base_url}{url}"
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                response = await client.get(target, timeout=60.0)
                response.raise_for_status()
                text = response.text.strip()
                if not text:
                    return (
                        "WEB_FETCH_UNAVAILABLE\n"
                        "reason: empty_fetch_response\n"
                        "guidance: full-page retrieval returned no content."
                    )
                return text
            except Exception as e:
                logger.error(f"Jina fetch failed: {e}")
                return (
                    "WEB_FETCH_UNAVAILABLE\n"
                    "reason: jina_fetch_error\n"
                    f"detail: {str(e)}\n"
                    "guidance: full-page retrieval is unavailable right now. Do not claim page contents you could not fetch."
                )


def _normalize_company_domain(company_domain: str = "", careers_url: str = "") -> str:
    domain = (company_domain or "").strip().lower()
    if domain:
        return domain.replace("https://", "").replace("http://", "").strip("/").split("/", 1)[0]
    if careers_url:
        parsed = urlparse(careers_url)
        return (parsed.netloc or "").strip().lower()
    return ""


def _candidate_contact_urls(company_domain: str, careers_url: str = "") -> List[str]:
    urls: List[str] = []
    if careers_url:
        urls.append(careers_url.strip())
    if company_domain:
        base = f"https://{company_domain}"
        urls.extend(
            [
                base,
                f"{base}/careers",
                f"{base}/jobs",
                f"{base}/contact",
                f"{base}/about",
            ]
        )
    seen = set()
    deduped = []
    for url in urls:
        clean = url.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        deduped.append(clean)
    return deduped


def _extract_email_addresses(text: str, company_domain: str = "") -> List[str]:
    matches = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text or "")
    cleaned = []
    seen = set()
    for email in matches:
        value = email.strip(".,;:()[]{}<>").lower()
        if company_domain and company_domain not in value:
            continue
        if value in seen:
            continue
        seen.add(value)
        cleaned.append(value)
    return cleaned


def _heuristic_contact_emails(company_domain: str) -> List[str]:
    if not company_domain:
        return []
    aliases = [
        "recruiting",
        "careers",
        "jobs",
        "hiring",
        "talent",
        "people",
        "hr",
    ]
    return [f"{alias}@{company_domain}" for alias in aliases]

# We keep our custom search but remove write_plan as deepagents has write_todos
@tool
@tool_monitor
def update_skill(name: str, content: str):
    """Refine your own custom instructions by updating a skill in 'skills/custom/'.
    This is your primary path for 'self-evolution' in specific domains.
    """
    logger.tool_call("update_skill", {"name": name})
    try:
        base_dir = Path("skills/custom")
        base_dir.mkdir(parents=True, exist_ok=True)
        clean_name = "".join([c for c in name if c.isalnum() or c in (' ', '-', '_')]).strip()
        file_path = base_dir / clean_name / "SKILL.md"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        skill_content = f"---\nname: {name}\ndescription: Custom skill\n---\n\n{content}"
        with open(file_path, "w", encoding="utf-8") as f: f.write(skill_content)
        get_skill_loader().load_all()
        return f"Successfully updated skill '{name}'."
    except Exception as e: return f"Error: {str(e)}"

@tool
@tool_monitor
def read_skill_instructions(name: str):
    """Read the full detailed instructions for a specialized skill (e.g., job analysis).
    Refer to the 'Skill Index' in your core context for available options.
    """
    logger.tool_call("read_skill", {"name": name})
    return get_skill_loader().get_skill_content(name)

# Built-in wrappers for Job Hunter
fc_key = config.firecrawl_key
firecrawl = FirecrawlTool(fc_key) if fc_key else None
primary_model_cfg = config.models[0] if config.models else {}
google_grounding = None
if primary_model_cfg.get("api_key") and (
    "gemini" in str(primary_model_cfg.get("model", "")).lower()
    or "google" in str(primary_model_cfg.get("use", "")).lower()
    or "generativelanguage.googleapis.com" in str(primary_model_cfg.get("base_url", "")).lower()
):
    google_grounding = GeminiGroundedSearchTool(
        api_key=primary_model_cfg.get("api_key"),
        model=primary_model_cfg.get("model", "gemini-2.5-flash"),
    )
jina_fetch = JinaFetchTool()

@tool
@tool_monitor
async def web_search(query: str):
    """Search the web for news, job listings, or company info."""
    if google_grounding:
        grounded_result = await google_grounding.search(query)
        if "WEB_SEARCH_UNAVAILABLE" not in grounded_result:
            return grounded_result
        # Grounding occasionally returns a transient empty/error response; retry once before falling back.
        retry_result = await google_grounding.search(query)
        if "WEB_SEARCH_UNAVAILABLE" not in retry_result:
            return retry_result
    if firecrawl:
        return await firecrawl.search(query, limit=firecrawl.default_max_results)
    return (
        "WEB_SEARCH_UNAVAILABLE\n"
        "reason: no_search_backend_configured\n"
        "guidance: external live search is unavailable right now. "
        "Do not present fresh job openings as confirmed. Use fallback planning or existing known targets."
    )

@tool
@tool_monitor
async def web_fetch(url: str):
    """Scrape the full content of a specific page (e.g., job description)."""
    jina_result = await jina_fetch.fetch(url)
    if "WEB_FETCH_UNAVAILABLE" not in jina_result:
        return jina_result
    if firecrawl:
        return await firecrawl.fetch(url)
    return jina_result


@tool
@tool_monitor
async def discover_company_contacts(company: str, company_domain: str = "", careers_url: str = ""):
    """Check official company pages for public contact emails and return fallback recruiting aliases when none are found."""
    normalized_domain = _normalize_company_domain(company_domain=company_domain, careers_url=careers_url)
    urls = _candidate_contact_urls(normalized_domain, careers_url=careers_url)
    checked_urls: List[Dict[str, Any]] = []
    public_emails: List[str] = []

    for url in urls[:5]:
        fetched = await jina_fetch.fetch(url)
        unavailable = "WEB_FETCH_UNAVAILABLE" in fetched
        emails = [] if unavailable else _extract_email_addresses(fetched, company_domain=normalized_domain)
        checked_urls.append(
            {
                "url": url,
                "fetch_ok": not unavailable,
                "public_email_count": len(emails),
            }
        )
        for email in emails:
            if email not in public_emails:
                public_emails.append(email)

    heuristic_emails = [email for email in _heuristic_contact_emails(normalized_domain) if email not in public_emails]
    return json.dumps(
        {
            "company": company,
            "company_domain": normalized_domain,
            "checked_urls": checked_urls,
            "public_emails": public_emails[:10],
            "heuristic_emails": heuristic_emails[:10],
            "recommended_email": (public_emails[:1] or heuristic_emails[:1] or [""])[0],
            "note": (
                "Prefer public_emails over heuristic_emails. "
                "If only heuristic_emails are available, explicitly treat them as unverified fallback addresses."
            ),
        },
        ensure_ascii=False,
    )

@tool
@tool_monitor
async def performance_auditor():
    """Analyze historical performance metrics and calculate a quantifiable Job Hunter Health Score (JHHS).
    This provides metrics on reliability, conversion, efficiency, and activity.
    """
    stats = {
        "apps_sent": 0,
        "drafts_pending": 0,
        "status_breakdown": {},
        "total_tool_calls": 0,
        "successful_tool_calls": 0,
        "avg_duration": 0.0,
        "activity_24h": 0,
        "activity_7d": 0,
        "recent_failures": [],
        "failures_by_tool": {},
        "tool_latency": {}
    }
    
    try:
        import aiosqlite
        now = datetime.now()
        day_ago = now - timedelta(days=1)
        
        # 1. Database Stats
        if os.path.exists("data/harness.db"):
            async with aiosqlite.connect("data/harness.db") as conn:
                async with conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='applications'"
                ) as cursor:
                    has_applications_table = bool(await cursor.fetchone())
                if has_applications_table:
                    async with conn.execute("SELECT status, count(*) FROM applications GROUP BY status") as cursor:
                        rows = await cursor.fetchall()
                        for status, count in rows:
                            stats["status_breakdown"][status] = count
                            if status == 'SENT':
                                stats["apps_sent"] = count
                            elif 'DRAFTED' in status:
                                stats["drafts_pending"] += count
        
        # 2. Deep Trace Analysis
        if os.path.exists("data/traces.jsonl"):
            durations = []
            with open("data/traces.jsonl", "r") as f:
                for line in f:
                    try:
                        event = json.loads(line)
                        if event.get("event") == "tool_complete":
                            stats["total_tool_calls"] += 1
                            if event.get("success"):
                                stats["successful_tool_calls"] += 1
                            else:
                                stats["recent_failures"].append(f"{event.get('tool')} ({event.get('timestamp')})")
                                tool_name = event.get("tool", "unknown")
                                stats["failures_by_tool"][tool_name] = stats["failures_by_tool"].get(tool_name, 0) + 1
                            
                            tool_name = event.get("tool", "unknown")
                            duration = event.get("duration", 0)
                            durations.append(duration)
                            tool_metrics = stats["tool_latency"].setdefault(tool_name, {"count": 0, "total_duration": 0.0})
                            tool_metrics["count"] += 1
                            tool_metrics["total_duration"] += duration
                            
                            # Activity in last 24h
                            ts = datetime.fromisoformat(event.get("timestamp"))
                            if ts > day_ago:
                                stats["activity_24h"] += 1
                            if ts > now - timedelta(days=7):
                                stats["activity_7d"] += 1
                    except Exception: continue
            
            if durations:
                stats["avg_duration"] = sum(durations) / len(durations)
        
        # 3. Score Calculation (JHHS)
        # 3.1 Conversion (30 pts) - Ratio of Sent vs Total Apps
        total_apps = stats["apps_sent"] + stats["drafts_pending"]
        conv_score = (stats["apps_sent"] / total_apps * 30) if total_apps > 0 else 15.0
        
        # 3.2 Reliability (30 pts) - Success Rate
        rel_rate = stats["successful_tool_calls"] / stats["total_tool_calls"] if stats["total_tool_calls"] > 0 else 1.0
        rel_score = rel_rate * 30
        
        # 3.3 Efficiency (20 pts) - Time penalty
        # Targets: < 5s = 20pts, < 15s = 10pts, else 5pts
        if stats["total_tool_calls"] == 0:
            eff_score = 20
        elif stats["avg_duration"] < 5:
            eff_score = 20
        elif stats["avg_duration"] < 15:
            eff_score = 10
        else:
            eff_score = 5
            
        # 3.4 Activity (20 pts) - Actions in last 24h
        # Target: 50 actions/day for full points
        act_score = min(stats["activity_24h"] / 50.0, 1.0) * 20
        
        total_score = conv_score + rel_score + eff_score + act_score
        
        status_color = "🟢" if total_score > 80 else "🟡" if total_score > 50 else "🔴"
        
        if os.path.exists("data/harness.db") and not stats["status_breakdown"]:
            app_tracking_note = "Application tracking table is initialized but currently has no recorded application rows."
        else:
            app_tracking_note = "Application tracking data available."

        report = (
            f"### {status_color} Job Hunter Health Score (JHHS): {total_score:.1f}/100\n"
            f"- **Conversion (投递转化率)**: {conv_score:.1f}/30 (Sent: {stats['apps_sent']}, Pending: {stats['drafts_pending']})\n"
            f"- **Reliability (执行成功率)**: {rel_score:.1f}/30 ({rel_rate*100:.1f}% Success)\n"
            f"- **Efficiency (执行效率)**: {eff_score:.1f}/20 (Avg: {stats['avg_duration']:.2f}s)\n"
            f"- **Activity (24h 活跃度)**: {act_score:.1f}/20 ({stats['activity_24h']} actions)\n"
            f"- **7d Activity (7天活跃度)**: {stats['activity_7d']} actions\n"
            f"- **Application Status Breakdown**: {stats['status_breakdown'] or 'None'}\n\n"
            f"**Critical Observations:**\n"
            f"- Application Tracking: {app_tracking_note}\n"
            f"- Failure Rate: {100 - (rel_rate*100):.1f}%\n"
            f"- Recent Tool Errors: {stats['recent_failures'][-3:] if stats['recent_failures'] else 'None'}\n"
            f"- Failure Hotspots: {stats['failures_by_tool'] or 'None'}\n"
            f"- Tool Latency Summary: "
            f"{ {tool: round(values['total_duration'] / values['count'], 2) for tool, values in stats['tool_latency'].items()} or 'None' }"
        )
        return report
    except Exception as e:
        return f"Audit Error: {str(e)}"

def get_job_hunter_tools():
    """Returns the custom tools for the Job Hunter."""
    return [web_search, web_fetch, discover_company_contacts, update_skill, read_skill_instructions, performance_auditor]
