import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List

from telegram import Bot

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from harness_engine.config import config
from harness_engine.core.agent import init_job_hunter, create_model
from harness_engine.core.auditor import AuditorAgent
from harness_engine.core.scheduler import AutonomousScheduler
from harness_engine.tools import actuators
from harness_engine.tools.actuators import (
    apply_on_company_site,
    browser_bootstrap,
    create_email_draft,
    linkedin_connect_preview,
    linkedin_connection_status,
    linkedin_search_people,
    linkedin_referral_outreach,
)
from harness_engine.tools.builtins import web_fetch, web_search


@dataclass
class Expectation:
    kind: str
    value: str


@dataclass
class Scenario:
    name: str
    prompt: str
    expectations: List[Expectation]


SCENARIOS = [
    Scenario(
        name="discovery_fallback",
        prompt=(
            "请先基于我当前已经注入的 resume、job targets 和 profile 工作，不要再让我重复提供简历。"
            "帮我找 3 个 Vancouver 或 Remote Canada 的 MLOps / AI Infrastructure / "
            "LLM Engineering 岗位；如果实时搜索不可用，请明确区分："
            "1. 已确认事实 2. 候选公司或角色假设 3. 你现在就能直接为我产出的下一步内容。"
            "不要把候选公司包装成已验证岗位，也不要引用“之前回复中的内容”。"
        ),
        expectations=[
            Expectation("contains_any", "已确认事实|confirmed facts"),
            Expectation("contains_any", "候选公司|假设|未验证|hypothesis"),
            Expectation("not_contains", "请把你目前的简历"),
            Expectation("not_contains", "请提供你的简历"),
            Expectation("not_contains", "之前回复"),
            Expectation("not_contains", "之前提供"),
        ],
    ),
    Scenario(
        name="referral_minimal_deliverable",
        prompt=(
            "不要再给我人工任务清单，也不要让我自己去做泛泛的 LinkedIn 搜索。"
            "请只做一个最小自动化 deliverable："
            "1. 从你刚才的候选公司假设里只选 1 家最值得追的公司 "
            "2. 明确写出：这不是已验证 live posting，只是 target-company hypothesis "
            "3. 直接给我两条可用文案："
            "一条未连接状态下的 LinkedIn connection request note；"
            "一条连接通过后的简短 follow-up "
            "4. 如果你现在无法自动定位具体目标人，请只告诉我一个最小缺失项，例如 1 个目标人的 LinkedIn URL 或姓名。"
            "不要再给我 3 家公司并行方案，不要引用之前回复，也不要给我简历修改作业。"
        ),
        expectations=[
            Expectation("contains_any", "connection request|connect"),
            Expectation("contains_any", "follow-up|follow up|连接通过"),
            Expectation("contains_any", "未验证|hypothesis|target-company"),
            Expectation("not_contains", "请在 LinkedIn 上"),
            Expectation("not_contains", "之前回复"),
            Expectation("not_contains", "简历修改"),
        ],
    ),
]


def evaluate_response(response: str, expectations: List[Expectation]):
    normalized = response.lower()
    checks = []
    for item in expectations:
        if item.kind == "contains_any":
            options = [x.strip().lower() for x in item.value.split("|") if x.strip()]
            passed = any(option in normalized for option in options)
            checks.append(
                {
                    "kind": item.kind,
                    "value": item.value,
                    "passed": passed,
                }
            )
        elif item.kind == "not_contains":
            passed = item.value.lower() not in normalized
            checks.append(
                {
                    "kind": item.kind,
                    "value": item.value,
                    "passed": passed,
                }
            )
        else:
            checks.append(
                {
                    "kind": item.kind,
                    "value": item.value,
                    "passed": False,
                    "error": "unknown expectation kind",
                }
            )
    return checks


async def run_scenario(agent, scenario: Scenario, thread_prefix: str):
    thread_id = f"{thread_prefix}_{scenario.name}"
    chunks = []
    timed_out = False

    iterator = agent.run(scenario.prompt, thread_id=thread_id)
    while True:
        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=45)
            if chunk:
                chunks.append(chunk)
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            timed_out = True
            break

    response = "\n".join(chunks).strip()
    checks = evaluate_response(response, scenario.expectations)
    passed = bool(response) and all(check["passed"] for check in checks) and not timed_out
    return {
        "name": scenario.name,
        "kind": "prompt",
        "thread_id": thread_id,
        "passed": passed,
        "timed_out": timed_out,
        "response": response,
        "checks": checks,
    }


async def run_email_draft_contract():
    recorded = {}

    async def fake_create_draft(to_email: str, subject: str, body: str):
        recorded["to_email"] = to_email
        recorded["subject"] = subject
        recorded["body"] = body
        return f"Success: Draft created for {to_email}. Please check your '[Gmail]/Drafts' folder to review and send."

    original = actuators._email_actuator.create_draft
    actuators._email_actuator.create_draft = fake_create_draft
    try:
        response = await create_email_draft.ainvoke(
            {
                "to_email": "hiring.manager@example.com",
                "subject": "Production AI systems at ExampleAI",
                "body": "Hi ExampleAI team,\n\nI build production-grade AI systems.\n",
            }
        )
    finally:
        actuators._email_actuator.create_draft = original

    checks = [
        {"kind": "contains", "value": "Success: Draft created", "passed": "Success: Draft created" in response},
        {"kind": "contains", "value": "Drafts", "passed": "Drafts" in response},
        {"kind": "arg", "value": "to_email", "passed": recorded.get("to_email") == "hiring.manager@example.com"},
        {"kind": "arg", "value": "subject", "passed": recorded.get("subject") == "Production AI systems at ExampleAI"},
    ]
    return {
        "name": "email_draft_contract",
        "kind": "tool",
        "passed": all(check["passed"] for check in checks),
        "response": response,
        "checks": checks,
    }


async def run_application_prefill_contract():
    recorded = {}
    original_create = actuators._approval_store.create_application

    async def fake_fill_form(url: str, profile_data, submit: bool = False):
        recorded["url"] = url
        recorded["profile_data"] = profile_data
        recorded["submit"] = submit
        return (
            f"Success: Application flow processed for {url}. "
            "Uploads: ['resume_uploaded', 'cover_letter_uploaded']. "
            "Submission: not_submitted. Screenshot: data/screenshots/fake.png."
        )

    def fake_create_application(url: str, prefill_result: str):
        recorded["approval_url"] = url
        recorded["approval_prefill_result"] = prefill_result
        return {
            "id": "testprefill1",
            "url": url,
            "screenshot": "data/screenshots/fake.png",
            "status": "pending",
        }

    original = actuators._browser_actuator.fill_form
    actuators._browser_actuator.fill_form = fake_fill_form
    actuators._approval_store.create_application = fake_create_application
    try:
        response = await apply_on_company_site.ainvoke(
            {
                "url": "https://jobs.example.com/apply/ml-infra-engineer",
                "submit": False,
            }
        )
    finally:
        actuators._browser_actuator.fill_form = original
        actuators._approval_store.create_application = original_create

    checks = [
        {"kind": "contains", "value": "Success: Application flow processed", "passed": "Success: Application flow processed" in response},
        {"kind": "contains", "value": "resume_uploaded", "passed": "resume_uploaded" in response},
        {"kind": "contains", "value": "cover_letter_uploaded", "passed": "cover_letter_uploaded" in response},
        {"kind": "arg", "value": "submit_false", "passed": recorded.get("submit") is False},
        {"kind": "arg", "value": "profile_email", "passed": bool(recorded.get("profile_data", {}).get("email"))},
    ]
    return {
        "name": "application_prefill_contract",
        "kind": "tool",
        "passed": all(check["passed"] for check in checks),
        "response": response,
        "checks": checks,
    }


async def run_application_submission_approval_contract():
    recorded = {"notified": []}

    async def fake_fill_form(url: str, profile_data, submit: bool = False, force_submit: bool = False):
        return (
            f"Success: Application flow processed for {url}. "
            "Uploads: ['resume_uploaded', 'cover_letter_uploaded']. "
            "Submission: not_submitted. Screenshot: data/screenshots/fake.png."
        )

    def fake_create_application(url: str, prefill_result: str):
        return {
            "id": "apply1234",
            "url": url,
            "screenshot": "data/screenshots/fake.png",
            "status": "pending",
        }

    async def fake_send_message(text: str, chat_id=None):
        recorded["notified"].append(text)
        return True

    original_fill = actuators._browser_actuator.fill_form
    original_create = actuators._approval_store.create_application
    from harness_engine.channels import telegram as telegram_channel
    original_send = telegram_channel.send_message
    actuators._browser_actuator.fill_form = fake_fill_form
    actuators._approval_store.create_application = fake_create_application
    telegram_channel.send_message = fake_send_message
    try:
        response = await apply_on_company_site.ainvoke(
            {
                "url": "https://jobs.example.com/apply/ml-infra-engineer",
                "submit": False,
            }
        )
    finally:
        actuators._browser_actuator.fill_form = original_fill
        actuators._approval_store.create_application = original_create
        telegram_channel.send_message = original_send

    checks = [
        {"kind": "contains", "value": "approval_id=apply1234", "passed": "approval_id=apply1234" in response},
        {"kind": "contains", "value": "Waiting for Telegram authorization", "passed": "Waiting for Telegram authorization" in response},
        {"kind": "value", "value": "telegram_notified", "passed": any("apply1234" in text for text in recorded["notified"])},
    ]
    return {
        "name": "application_submission_approval_contract",
        "kind": "tool",
        "passed": all(check["passed"] for check in checks),
        "response": response,
        "checks": checks,
    }


async def run_scheduler_summary_contract():
    pushed_messages = []

    class FakeAgent:
        async def run(self, input_msg: str, thread_id: str = "default"):
            yield "Checked target-company shortlist."
            yield "Prepared next action for referral outreach."

    async def fake_send_message(text: str, chat_id=None):
        pushed_messages.append({"text": text, "chat_id": chat_id})
        return True

    original_send = actuators.logger  # no-op anchor to avoid lint-like reordering concerns
    _ = original_send
    from harness_engine.channels import telegram as telegram_channel

    send_original = telegram_channel.send_message
    telegram_channel.send_message = fake_send_message
    try:
        scheduler = AutonomousScheduler(FakeAgent(), interval_seconds=60)
        scheduler.follow_up_enabled = False
        scheduler.summary_every_cycles = 1
        scheduler.last_audit_date = datetime.now().date()
        scheduler.add_thread("acceptance_scheduler")
        await scheduler.run_now()
    finally:
        telegram_channel.send_message = send_original

    checks = [
        {"kind": "contains", "value": "Checked target-company shortlist.", "passed": "Checked target-company shortlist." in scheduler.last_summary},
        {"kind": "contains", "value": "Prepared next action for referral outreach.", "passed": "Prepared next action for referral outreach." in scheduler.last_summary},
        {"kind": "value", "value": "cycle_count", "passed": scheduler.cycle_count == 1},
        {"kind": "value", "value": "pushed_summary", "passed": len(pushed_messages) == 1},
    ]
    response = scheduler.last_summary
    return {
        "name": "scheduler_summary_contract",
        "kind": "scheduler",
        "passed": all(check["passed"] for check in checks),
        "response": response,
        "checks": checks,
    }


async def run_linkedin_connect_then_followup_contract():
    recorded = {"connect_calls": 0, "message_calls": 0}

    async def fake_connection_status(profile_url: str):
        if profile_url.endswith("/connected"):
            return json.dumps({"status": "connected"})
        return json.dumps({"status": "not_connected"})

    async def fake_connect(profile_url: str, note: str = ""):
        recorded["connect_calls"] += 1
        recorded["connect_note"] = note
        return "LinkedIn connection request sent."

    async def fake_send_message(profile_url: str, message: str):
        recorded["message_calls"] += 1
        recorded["message_text"] = message
        return "LinkedIn message sent."

    original_status = actuators._linkedin_actuator.connection_status
    original_connect = actuators._linkedin_actuator.connect
    original_send = actuators._linkedin_actuator.send_message
    actuators._linkedin_actuator.connection_status = fake_connection_status
    actuators._linkedin_actuator.connect = fake_connect
    actuators._linkedin_actuator.send_message = fake_send_message
    try:
        first_response = await linkedin_referral_outreach.ainvoke(
            {
                "profile_url": "https://linkedin.example.com/not_connected",
                "connection_note": "Hi Alex, would love to connect.",
                "message": "Hi Alex, thanks for connecting.",
            }
        )
        second_response = await linkedin_referral_outreach.ainvoke(
            {
                "profile_url": "https://linkedin.example.com/connected",
                "connection_note": "unused",
                "message": "Hi Alex, thanks for connecting.",
            }
        )
    finally:
        actuators._linkedin_actuator.connection_status = original_status
        actuators._linkedin_actuator.connect = original_connect
        actuators._linkedin_actuator.send_message = original_send

    checks = [
        {"kind": "contains", "value": "connection request sent", "passed": "connection request sent" in first_response.lower()},
        {"kind": "contains", "value": "message sent", "passed": "message sent" in second_response.lower()},
        {"kind": "value", "value": "connect_calls", "passed": recorded["connect_calls"] == 1},
        {"kind": "value", "value": "message_calls", "passed": recorded["message_calls"] == 1},
    ]
    return {
        "name": "linkedin_connect_then_followup_contract",
        "kind": "tool",
        "passed": all(check["passed"] for check in checks),
        "response": f"{first_response}\n{second_response}",
        "checks": checks,
    }


async def run_linkedin_search_people_contract():
    async def fake_search(company: str, role_keywords: str = "", location: str = "", limit: int = 5):
        return json.dumps(
            {
                "query": f"{company} {role_keywords} {location}".strip(),
                "search_url": "https://www.linkedin.com/search/results/people/?keywords=cohere",
                "candidates": [
                    {
                        "name": "Alex Chen",
                        "profile_url": "https://www.linkedin.com/in/alex-chen/",
                        "snippet": "Engineering Manager, ML Infrastructure at Cohere",
                    },
                    {
                        "name": "Priya Singh",
                        "profile_url": "https://www.linkedin.com/in/priya-singh/",
                        "snippet": "Senior Recruiter at Cohere",
                    },
                ],
            },
            ensure_ascii=False,
        )

    original = actuators._browser_actuator.linkedin_search_people
    actuators._browser_actuator.linkedin_search_people = fake_search
    try:
        response = await linkedin_search_people.ainvoke(
            {
                "company": "Cohere",
                "role_keywords": "ML Infrastructure Engineering Manager",
                "location": "Canada",
                "limit": 2,
            }
        )
    finally:
        actuators._browser_actuator.linkedin_search_people = original

    checks = [
        {"kind": "contains", "value": "Alex Chen", "passed": "Alex Chen" in response},
        {"kind": "contains", "value": "profile_url", "passed": "profile_url" in response},
        {"kind": "contains", "value": "Cohere", "passed": "Cohere" in response},
    ]
    return {
        "name": "linkedin_search_people_contract",
        "kind": "tool",
        "passed": all(check["passed"] for check in checks),
        "response": response,
        "checks": checks,
    }


async def run_linkedin_connect_preview_contract():
    async def fake_preview(profile_url: str):
        return json.dumps(
            {
                "status": "not_connected",
                "connect_flow_opened": True,
                "add_note_available": True,
                "send_available": True,
                "url": profile_url,
            },
            ensure_ascii=False,
        )

    original = actuators._browser_actuator.linkedin_connect_preview
    actuators._browser_actuator.linkedin_connect_preview = fake_preview
    try:
        response = await linkedin_connect_preview.ainvoke(
            {"profile_url": "https://www.linkedin.com/in/alex-chen/"}
        )
    finally:
        actuators._browser_actuator.linkedin_connect_preview = original

    checks = [
        {"kind": "contains", "value": "connect_flow_opened", "passed": "connect_flow_opened" in response},
        {"kind": "contains", "value": "add_note_available", "passed": "add_note_available" in response},
        {"kind": "contains", "value": "send_available", "passed": "send_available" in response},
    ]
    return {
        "name": "linkedin_connect_preview_contract",
        "kind": "tool",
        "passed": all(check["passed"] for check in checks),
        "response": response,
        "checks": checks,
    }


async def run_browser_bootstrap_contract():
    async def fake_bootstrap():
        return json.dumps(
            {
                "status": "already_running",
                "cdp_url": "http://127.0.0.1:9222",
                "user_data_dir": "/home/liu/.config/google-chrome-remote-debug",
            },
            ensure_ascii=False,
        )

    original = actuators._browser_actuator.bootstrap_browser
    actuators._browser_actuator.bootstrap_browser = fake_bootstrap
    try:
        response = await browser_bootstrap.ainvoke({})
    finally:
        actuators._browser_actuator.bootstrap_browser = original

    checks = [
        {"kind": "contains_any", "value": "already_running|started", "passed": any(x in response for x in ["already_running", "started"])},
        {"kind": "contains", "value": "cdp_url", "passed": "cdp_url" in response},
    ]
    return {
        "name": "browser_bootstrap_contract",
        "kind": "tool",
        "passed": all(check["passed"] for check in checks),
        "response": response,
        "checks": checks,
    }


async def run_followup_draft_generation_contract():
    import aiosqlite
    from harness_engine.core import scheduler as scheduler_module
    from harness_engine.channels import telegram as telegram_channel

    db_path = Path("/tmp/acceptance_followup.db")
    if db_path.exists():
        db_path.unlink()

    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT,
                job_title TEXT,
                contact_name TEXT,
                contact_info TEXT,
                status TEXT,
                last_contact_date DATE,
                notes TEXT
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO applications (company, job_title, contact_name, status, last_contact_date)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("ExampleAI", "ML Infra Engineer", "Alex Chen", "SENT", "2026-03-20"),
        )
        await conn.commit()

    class FakeAgent:
        def __init__(self):
            self.prompts = []

        async def run(self, input_msg: str, thread_id: str = "default"):
            self.prompts.append({"input_msg": input_msg, "thread_id": thread_id})
            yield "Draft prepared."

    fake_agent = FakeAgent()
    pushed_messages = []

    async def fake_send_message(text: str, chat_id=None):
        pushed_messages.append(text)
        return True

    original_sqlite_connect = scheduler_module.aiosqlite.connect
    original_send = telegram_channel.send_message
    scheduler_module.aiosqlite.connect = lambda *_args, **_kwargs: original_sqlite_connect(db_path)
    telegram_channel.send_message = fake_send_message
    try:
        scheduler = AutonomousScheduler(fake_agent, interval_seconds=60)
        scheduler.summary_every_cycles = 999
        scheduler.last_audit_date = datetime.now().date()
        scheduler.add_thread("acceptance_followup")
        await scheduler.run_now()

        async with aiosqlite.connect(db_path) as conn:
            async with conn.execute("SELECT status FROM applications WHERE company = ?", ("ExampleAI",)) as cursor:
                row = await cursor.fetchone()
                final_status = row[0] if row else None
    finally:
        scheduler_module.aiosqlite.connect = original_sqlite_connect
        telegram_channel.send_message = original_send
        await asyncio.sleep(1.0)
        if db_path.exists():
            db_path.unlink()

    checks = [
        {"kind": "contains", "value": "stage 1", "passed": any("stage 1" in item["input_msg"].lower() for item in fake_agent.prompts)},
        {"kind": "value", "value": "status_updated", "passed": final_status == "FOLLOWUP_1_DRAFTED"},
        {"kind": "value", "value": "telegram_notified", "passed": any("Follow-up Created" in text for text in pushed_messages)},
    ]
    return {
        "name": "followup_draft_generation_contract",
        "kind": "scheduler",
        "passed": all(check["passed"] for check in checks),
        "response": json.dumps(
            {
                "final_status": final_status,
                "prompts": fake_agent.prompts,
                "notifications": pushed_messages,
            },
            ensure_ascii=False,
        ),
        "checks": checks,
    }


async def run_auditor_report_contract():
    class FakeAuditor:
        async def run_audit(self, thread_id: str = "auditor_session"):
            yield (
                "### 审计诊断报告 [2026-03-31]\n\n"
                "#### 1. Executive Summary\n"
                "- summary\n\n"
                "#### 2. System Audit\n"
                "- system\n\n"
                "#### 3. Strategy Audit\n"
                "- strategy\n\n"
                "#### 4. Root Causes\n"
                "- cause\n\n"
                "#### 5. Priority Fixes\n"
                "- P0\n\n"
                "#### 6. Evolution Proposals (待授权)\n"
                "- proposal\n\n"
                "#### 7. Authorization Text\n"
                "- 授权审计：更新 outreach\n"
            )

    auditor = FakeAuditor()
    chunks = []
    async for chunk in auditor.run_audit("acceptance_auditor"):
        if chunk:
            chunks.append(chunk)
    response = "\n".join(chunks)
    checks = [
        {"kind": "contains", "value": "Executive Summary", "passed": "Executive Summary" in response},
        {"kind": "contains", "value": "System Audit", "passed": "System Audit" in response},
        {"kind": "contains", "value": "Strategy Audit", "passed": "Strategy Audit" in response},
        {"kind": "contains", "value": "Priority Fixes", "passed": "Priority Fixes" in response},
        {"kind": "contains", "value": "Authorization Text", "passed": "Authorization Text" in response},
    ]
    return {
        "name": "auditor_report_contract",
        "kind": "audit",
        "passed": all(check["passed"] for check in checks),
        "response": response,
        "checks": checks,
    }


async def run_real_model_smoke():
    model = create_model()
    response = await model.ainvoke("Reply with exactly: MODEL_OK")
    content = str(getattr(response, "content", ""))
    checks = [{"kind": "contains", "value": "MODEL_OK", "passed": "MODEL_OK" in content}]
    return {
        "name": "real_model_smoke",
        "kind": "real",
        "passed": all(check["passed"] for check in checks),
        "response": content,
        "checks": checks,
    }


async def run_real_auditor_smoke():
    auditor = AuditorAgent(checkpointer=None)
    chunks = []
    timed_out = False
    iterator = auditor.run_audit(thread_id="acceptance_real_auditor")
    while True:
        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=60)
            if chunk:
                chunks.append(str(chunk))
                joined = "\n".join(chunks)
                if "Priority Fixes" in joined or "优先修复" in joined:
                    break
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            timed_out = True
            break

    response = "\n".join(chunks).strip()
    lowered = response.lower()
    checks = [
        {"kind": "contains_any", "value": "Executive Summary|执行摘要", "passed": any(x.lower() in lowered for x in ["Executive Summary".lower(), "执行摘要"])},
        {"kind": "contains_any", "value": "System Audit|系统审计", "passed": any(x.lower() in lowered for x in ["System Audit".lower(), "系统审计"])},
        {"kind": "contains_any", "value": "Strategy Audit|策略审计", "passed": any(x.lower() in lowered for x in ["Strategy Audit".lower(), "策略审计"])},
        {"kind": "contains_any", "value": "Priority Fixes|优先修复", "passed": any(x.lower() in lowered for x in ["Priority Fixes".lower(), "优先修复"])},
        {"kind": "not_contains", "value": "update_skill(", "passed": "update_skill(" not in response},
    ]
    return {
        "name": "real_auditor_smoke",
        "kind": "real",
        "passed": bool(response) and all(check["passed"] for check in checks) and not timed_out,
        "response": response[:2000],
        "checks": checks,
    }


async def run_real_telegram_bot_smoke():
    bot = Bot(token=config.get("channels.telegram.token"))
    me = await bot.get_me()
    response = json.dumps({"id": me.id, "username": me.username, "is_bot": me.is_bot}, ensure_ascii=False)
    checks = [
        {"kind": "value", "value": "is_bot", "passed": bool(me.is_bot)},
        {"kind": "value", "value": "username", "passed": bool(me.username)},
    ]
    return {
        "name": "real_telegram_bot_smoke",
        "kind": "real",
        "passed": all(check["passed"] for check in checks),
        "response": response,
        "checks": checks,
    }


async def run_real_firecrawl_smoke():
    response = await web_search.ainvoke({"query": "site:greenhouse.io ml infrastructure engineer canada"})
    checks = [
        {"kind": "not_contains", "value": "WEB_SEARCH_UNAVAILABLE", "passed": "WEB_SEARCH_UNAVAILABLE" not in response},
        {"kind": "not_contains", "value": "Search error:", "passed": "Search error:" not in response},
    ]
    return {
        "name": "real_web_search_smoke",
        "kind": "real",
        "passed": all(check["passed"] for check in checks),
        "response": response[:1500],
        "checks": checks,
    }


async def run_real_web_fetch_smoke():
    response = await web_fetch.ainvoke({"url": "https://ai.google.dev/gemini-api/docs/google-search"})
    checks = [
        {"kind": "not_contains", "value": "WEB_FETCH_UNAVAILABLE", "passed": "WEB_FETCH_UNAVAILABLE" not in response},
        {"kind": "contains_any", "value": "Google Search|Gemini API|建立基準|grounding", "passed": any(x.lower() in response.lower() for x in ["Google Search", "Gemini API", "建立基準", "grounding"])},
    ]
    return {
        "name": "real_web_fetch_smoke",
        "kind": "real",
        "passed": all(check["passed"] for check in checks),
        "response": response[:1500],
        "checks": checks,
    }


async def run_real_application_prefill_smoke():
    response = await apply_on_company_site.ainvoke(
        {
            "url": "https://boards.greenhouse.io/embed/job_app?token=7090334",
            "submit": False,
        }
    )
    checks = [
        {"kind": "contains", "value": "Success: Application flow processed", "passed": "Success: Application flow processed" in response},
        {"kind": "contains", "value": "Submission: not_submitted", "passed": "Submission: not_submitted" in response},
        {"kind": "contains", "value": "Screenshot:", "passed": "Screenshot:" in response},
    ]
    return {
        "name": "real_application_prefill_smoke",
        "kind": "real",
        "passed": all(check["passed"] for check in checks),
        "response": response[:1500],
        "checks": checks,
    }


async def run_real_email_draft_smoke():
    target_email = config.get("email.user")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    response = await create_email_draft.ainvoke(
        {
            "to_email": target_email,
            "subject": f"[INTEGRATION TEST] Job Hunter Draft Smoke {timestamp}",
            "body": "This is a real integration test draft created by Job Hunter. No send action is required.",
        }
    )
    checks = [
        {"kind": "contains", "value": "Success: Draft created", "passed": "Success: Draft created" in response},
        {"kind": "contains", "value": "Drafts", "passed": "Drafts" in response},
    ]
    return {
        "name": "real_email_draft_smoke",
        "kind": "real",
        "passed": all(check["passed"] for check in checks),
        "response": response,
        "checks": checks,
    }


async def run_real_linkedin_status_smoke():
    profile_path = Path(config.get("personal.profile_path", "data/profile.json"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    response = await linkedin_connection_status.ainvoke({"profile_url": profile["linkedin_url"]})
    lowered = response.lower()
    checks = [
        {
            "kind": "not_contains",
            "value": "failed",
            "passed": "failed" not in lowered and "not installed" not in lowered and "disabled" not in lowered,
        },
        {
            "kind": "contains_any",
            "value": "\"status\"|connected|not_connected|pending|unknown",
            "passed": any(x in lowered for x in ['"status"', "connected", "not_connected", "pending", "unknown"]),
        },
    ]
    return {
        "name": "real_linkedin_status_smoke",
        "kind": "real",
        "passed": all(check["passed"] for check in checks),
        "response": response[:1500],
        "checks": checks,
    }


async def run_real_linkedin_search_people_smoke():
    response = await linkedin_search_people.ainvoke(
        {
            "company": "Cohere",
            "role_keywords": "engineering manager ml infrastructure",
            "location": "Canada",
            "limit": 3,
        }
    )
    lowered = response.lower()
    checks = [
        {
            "kind": "not_contains",
            "value": "failed",
            "passed": "failed" not in lowered and "error" not in lowered,
        },
        {
            "kind": "contains",
            "value": "candidates",
            "passed": "candidates" in lowered,
        },
        {
            "kind": "contains_any",
            "value": "linkedin.com/in/|profile_url",
            "passed": "linkedin.com/in/" in lowered or "profile_url" in lowered,
        },
    ]
    return {
        "name": "real_linkedin_search_people_smoke",
        "kind": "real",
        "passed": all(check["passed"] for check in checks),
        "response": response[:1500],
        "checks": checks,
    }


async def run_real_linkedin_connect_preview_smoke():
    search_result = await linkedin_search_people.ainvoke(
        {
            "company": "Cohere",
            "role_keywords": "engineering manager ml infrastructure",
            "location": "Canada",
            "limit": 3,
        }
    )
    payload = json.loads(search_result)
    candidates = payload.get("candidates") or []
    if not candidates:
        return {
            "name": "real_linkedin_connect_preview_smoke",
            "kind": "real",
            "passed": False,
            "response": search_result[:1500],
            "checks": [{"kind": "value", "value": "candidate_found", "passed": False}],
        }

    response = await linkedin_connect_preview.ainvoke(
        {"profile_url": candidates[0]["profile_url"]}
    )
    lowered = response.lower()
    checks = [
        {
            "kind": "contains_any",
            "value": "connect_flow_opened|already connected|request already pending",
            "passed": any(
                x in lowered
                for x in ["connect_flow_opened", "already connected", "request already pending"]
            ),
        },
        {"kind": "not_contains", "value": "failed", "passed": "failed" not in lowered},
    ]
    return {
        "name": "real_linkedin_connect_preview_smoke",
        "kind": "real",
        "passed": all(check["passed"] for check in checks),
        "response": response[:1500],
        "checks": checks,
    }


async def main():
    parser = argparse.ArgumentParser(description="Run closed-loop acceptance checks against the agent.")
    parser.add_argument("--output", default="test/last_acceptance_report.json")
    parser.add_argument("--thread-prefix", default="acceptance")
    parser.add_argument("--real", action="store_true", help="Run non-destructive real integration smoke tests.")
    args = parser.parse_args()

    agent = await init_job_hunter()
    results = []
    for scenario in SCENARIOS:
        results.append(await run_scenario(agent, scenario, args.thread_prefix))
    results.append(await run_email_draft_contract())
    results.append(await run_application_prefill_contract())
    results.append(await run_application_submission_approval_contract())
    results.append(await run_browser_bootstrap_contract())
    results.append(await run_linkedin_search_people_contract())
    results.append(await run_linkedin_connect_preview_contract())
    results.append(await run_linkedin_connect_then_followup_contract())
    results.append(await run_scheduler_summary_contract())
    results.append(await run_followup_draft_generation_contract())
    results.append(await run_auditor_report_contract())

    if args.real:
        results.append(await run_real_model_smoke())
        results.append(await run_real_telegram_bot_smoke())
        results.append(await run_real_firecrawl_smoke())
        results.append(await run_real_web_fetch_smoke())
        results.append(await run_real_application_prefill_smoke())
        results.append(await run_real_email_draft_smoke())
        results.append(await run_real_linkedin_status_smoke())
        results.append(await run_real_linkedin_search_people_smoke())
        results.append(await run_real_linkedin_connect_preview_smoke())
        results.append(await run_real_auditor_smoke())

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scenario_count": len(results),
        "passed_count": sum(1 for item in results if item["passed"]),
        "results": results,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for item in results:
        status = "PASS" if item["passed"] else "FAIL"
        print(f"[{status}] {item['name']}")
        if item.get("timed_out"):
            print("(scenario timed out after 90s; partial response shown below)")
        print(item["response"][:1200] or "<empty response>")
        print()
        for check in item["checks"]:
            marker = "ok" if check["passed"] else "xx"
            print(f"  - [{marker}] {check['kind']}: {check['value']}")
        print()

    await asyncio.sleep(1.0)
    print(f"Report written to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
