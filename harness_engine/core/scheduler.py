import asyncio
import aiosqlite
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Set
from harness_engine.core.logger import logger
from harness_engine.core.agent import JobHunterAgent
from harness_engine.core.auditor import AuditorAgent
from harness_engine.config import config
from harness_engine.channels import telegram

class AutonomousScheduler:
    """Periodically triggers the agent to run tasks with weekend-aware follow-up logic."""
    
    def __init__(self, agent: JobHunterAgent, interval_seconds: int = 3600):
        self.agent = agent
        self.auditor = AuditorAgent() # New Auditor Subagent
        self.interval = interval_seconds
        self.active_threads = set() # Set of thread_ids to monitor
        self.last_audit_date = None
        self.summary_every_cycles = int(config.get("auto_run.summary_every_cycles", 2))
        self.summary_max_chars = int(config.get("auto_run.summary_max_chars", 3500))
        self.background_prompt = config.get(
            "auto_run.background_prompt",
            "Run a concise background job-search maintenance cycle and summarize results.",
        )
        self.background_research_prompt = config.get(
            "auto_run.background_research_prompt",
            (
                "Phase 1 - Discovery and research. Review the candidate profile, job targets, memory, "
                "recent traces, and any known targets. Verify current high-fit roles or, if live roles are "
                "not available, produce a tightly scoped target-company and contact shortlist with the best "
                "evidence you can gather now. Preserve continuity for already-tracked companies, but do not "
                "get stuck on the same shortlist forever. If current tracked companies already have progress "
                "or no fresh signal, investigate at least one additional company this cycle and compare it "
                "against the existing targets. Research first; do not stop the overall cycle here."
            ),
        )
        self.background_execution_prompt = config.get(
            "auto_run.background_execution_prompt",
            (
                "Phase 2 - Decision and execution. Based on the current run's evidence, choose the single best "
                "next low-risk action and execute it now. Priority order: "
                "1) referral-first LinkedIn people search and connection request, "
                "2) LinkedIn message if already connected, "
                "3) create a strong email draft, "
                "4) prefill an official application with submit=false. "
                "Do not do more broad research in this phase unless execution truly depends on it. "
                "If LinkedIn profile discovery returns no candidates that actually match the target company, "
                "report that as a discovery limitation, not as a connect-button failure. "
                "For non-LinkedIn public discovery, use web_search/web_fetch style grounded search instead of "
                "browser-driving Google or Bing search result pages. "
                "Only report a LinkedIn UI blocker if you called linkedin_connect_preview or linkedin_connect "
                "for a specific profile URL and the tool explicitly failed. "
                "If a LinkedIn tool reports blocked/auth_wall/bot_detection/challenge, stop LinkedIn automation "
                "for this cycle and switch to a non-LinkedIn fallback. "
                "For email fallback, call discover_company_contacts first. Prefer public_emails from official pages; "
                "only use heuristic_emails if no public email was found, and say that the address is heuristic."
            ),
        )
        self.background_summary_prompt = config.get(
            "auto_run.background_summary_prompt",
            (
                "Phase 3 - Summary. Summarize this cycle in Chinese. Include: "
                "1) what was checked, 2) what concrete action was executed, 3) what artifact or result was produced, "
                "4) what still needs attention or approval. If execution was blocked, name the exact blocker. "
                "End with exactly one machine-readable line in this format: "
                "TRACKED_COMPANIES_JSON={\"tracked_companies\":[{\"company\":\"...\",\"status\":\"...\","
                "\"last_action\":\"...\",\"next_action\":\"...\",\"notes\":\"...\",\"is_new_target\":true}]}"
            ),
        )
        self.require_action_per_cycle = bool(
            config.get("operation_policy.require_one_executed_action_per_cycle", True)
        )
        self.audit_enabled = bool(config.get("auto_run.audit.enabled", True))
        self.audit_time = str(config.get("auto_run.audit.time", "09:00"))
        self.follow_up_enabled = bool(config.get("follow_up.enabled", True))
        self.stage_1_business_days = int(config.get("follow_up.stage_1_business_days", 5))
        self.stage_2_business_days = int(config.get("follow_up.stage_2_business_days", 10))
        self.cycle_count = 0
        self.started_at = datetime.now()
        self.next_audit_at = self._compute_next_audit_at(self.started_at)
        self.next_cycle_at = self.started_at
        self.last_cycle_started_at: Optional[str] = None
        self.last_cycle_completed_at: Optional[str] = None
        self.last_summary: str = ""
        self.last_error: str = ""
        self.last_cycle_updates: Dict[str, str] = {}
        self._cycle_lock = asyncio.Lock()
        self.execution_tools: Set[str] = {
            "linkedin_connect",
            "linkedin_send_message",
            "linkedin_referral_outreach",
            "create_email_draft",
            "apply_on_company_site",
        }

    def add_thread(self, thread_id: str):
        self.active_threads.add(thread_id)
        logger.info(f"Thread {thread_id} added to autonomous scheduler.")

    def get_status_snapshot(self) -> Dict[str, Any]:
        return {
            "enabled": bool(config.get("auto_run.enabled", True)),
            "interval_seconds": self.interval,
            "active_threads": sorted(self.active_threads),
            "cycle_count": self.cycle_count,
            "last_cycle_started_at": self.last_cycle_started_at,
            "last_cycle_completed_at": self.last_cycle_completed_at,
            "last_summary": self.last_summary,
            "last_error": self.last_error,
            "last_cycle_updates": self.last_cycle_updates,
            "is_running": self._cycle_lock.locked(),
            "next_audit_at": self.next_audit_at.isoformat(timespec="seconds") if self.next_audit_at else None,
            "next_cycle_at": self.next_cycle_at.isoformat(timespec="seconds") if self.next_cycle_at else None,
        }

    def _is_weekend(self, dt: datetime):
        return dt.weekday() >= 5 # 5 is Saturday, 6 is Sunday

    def _get_business_days_diff(self, start_date: datetime, end_date: datetime) -> int:
        """Calculates the number of business days between two dates."""
        days = (end_date - start_date).days
        business_days = 0
        current_date = start_date
        for _ in range(days):
            current_date += timedelta(days=1)
            if not self._is_weekend(current_date):
                business_days += 1
        return business_days

    @staticmethod
    def _parse_date(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        text = str(value).strip()
        if not text:
            return None
        for candidate in (text, text.split()[0]):
            try:
                return datetime.fromisoformat(candidate)
            except ValueError:
                try:
                    return datetime.strptime(candidate, "%Y-%m-%d")
                except ValueError:
                    continue
        return None

    def _compute_next_audit_at(self, now: datetime) -> Optional[datetime]:
        if not self.audit_enabled:
            return None
        try:
            hour_text, minute_text = self.audit_time.split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)
        except Exception:
            hour, minute = 9, 0
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    @staticmethod
    async def _applications_table_exists(conn: aiosqlite.Connection) -> bool:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='applications'"
        ) as cursor:
            row = await cursor.fetchone()
        return bool(row)

    async def _get_company_progress_snapshot(self, thread_id: str, limit: int = 6) -> str:
        try:
            conn = await aiosqlite.connect("data/harness.db")
            async with conn.execute(
                """
                SELECT company, status, last_action, next_action, notes, is_new_target, last_updated
                FROM company_progress
                WHERE source_thread = ? OR source_thread IS NULL OR source_thread = ''
                ORDER BY datetime(last_updated) DESC, company ASC
                LIMIT ?
                """,
                (thread_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
            await conn.close()
        except Exception as e:
            logger.warn(f"Failed to load company progress snapshot: {e}")
            return "No tracked company progress is currently stored."

        if not rows:
            return "No tracked company progress is currently stored."

        lines = ["Tracked company progress snapshot:"]
        for company, status, last_action, next_action, notes, is_new_target, last_updated in rows:
            lines.append(
                "- "
                f"{company} | status={status or 'unknown'} | last_action={last_action or 'unknown'} | "
                f"next_action={next_action or 'unknown'} | "
                f"is_new_target={bool(is_new_target)} | last_updated={last_updated or 'unknown'} | "
                f"notes={notes or 'none'}"
            )
        return "\n".join(lines)

    @staticmethod
    def _extract_company_tracking_payload(summary: str) -> Dict[str, Any]:
        marker = "TRACKED_COMPANIES_JSON="
        if marker not in summary:
            return {"tracked_companies": []}
        payload_text = summary.split(marker, 1)[1].strip().splitlines()[0].strip()
        try:
            payload = json.loads(payload_text)
        except Exception:
            return {"tracked_companies": []}
        if isinstance(payload, dict) and isinstance(payload.get("tracked_companies"), list):
            return payload
        return {"tracked_companies": []}

    @staticmethod
    def _strip_company_tracking_marker(summary: str) -> str:
        marker = "TRACKED_COMPANIES_JSON="
        if marker not in summary:
            return summary.strip()
        before, _, after = summary.partition(marker)
        trailing = after.splitlines()[1:]
        cleaned_parts = [before.strip()]
        trailing_text = "\n".join(line for line in trailing if line.strip()).strip()
        if trailing_text:
            cleaned_parts.append(trailing_text)
        return "\n".join(part for part in cleaned_parts if part).strip()

    async def _save_company_tracking_payload(
        self,
        thread_id: str,
        cycle_started_at: datetime,
        payload: Dict[str, Any],
    ) -> None:
        companies = payload.get("tracked_companies", [])
        if not companies:
            return

        conn = await aiosqlite.connect("data/harness.db")
        try:
            for item in companies:
                if not isinstance(item, dict):
                    continue
                company = str(item.get("company") or "").strip()
                if not company:
                    continue
                await conn.execute(
                    """
                    INSERT INTO company_progress (
                        company, status, last_action, next_action, notes,
                        source_thread, is_new_target, last_cycle_started_at, last_updated
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(company) DO UPDATE SET
                        status=excluded.status,
                        last_action=excluded.last_action,
                        next_action=excluded.next_action,
                        notes=excluded.notes,
                        source_thread=excluded.source_thread,
                        is_new_target=excluded.is_new_target,
                        last_cycle_started_at=excluded.last_cycle_started_at,
                        last_updated=excluded.last_updated
                    """,
                    (
                        company,
                        str(item.get("status") or "").strip(),
                        str(item.get("last_action") or "").strip(),
                        str(item.get("next_action") or "").strip(),
                        str(item.get("notes") or "").strip(),
                        thread_id,
                        1 if item.get("is_new_target") else 0,
                        cycle_started_at.isoformat(timespec="seconds"),
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
            await conn.commit()
        finally:
            await conn.close()

    async def _run_cycle(self):
        if self._cycle_lock.locked():
            logger.warn("Autonomous cycle skipped because a previous cycle is still running.")
            return

        async with self._cycle_lock:
            self.last_cycle_started_at = datetime.now().isoformat(timespec="seconds")
            self.last_error = ""
            self.last_cycle_updates = {}
            self.cycle_count += 1
            logger.info(f"Starting autonomous run cycle. Active threads: {len(self.active_threads)}")
        
            # 1. Weekend Check: Skip operations on weekends
            now = datetime.now()
            if self._is_weekend(now):
                logger.info("Today is a weekend. Skipping active outreach/follow-up cycles.")
                self.last_summary = "Weekend detected. Skipped active outreach and follow-up cycle."
                self.last_cycle_completed_at = datetime.now().isoformat(timespec="seconds")
                return

            # 2. Check for multi-stage follow-ups
            if self.follow_up_enabled:
                try:
                    conn = await aiosqlite.connect("data/harness.db")
                    if await self._applications_table_exists(conn):
                        query = """
                            SELECT id, company, job_title, contact_name, last_contact_date, status
                            FROM applications 
                            WHERE status IN ('SENT', 'FOLLOWUP_1_SENT', 'FOLLOWUP_1_DRAFTED')
                        """
                        async with conn.execute(query) as cursor:
                            apps = await cursor.fetchall()
                            for app_id, company, job_title, contact, last_date_str, status in apps:
                                last_date = self._parse_date(last_date_str)
                                if not last_date:
                                    logger.warn(f"Skipping follow-up check for {company}: invalid last_contact_date={last_date_str}")
                                    continue
                                biz_days = self._get_business_days_diff(last_date, now)

                                target_thread = list(self.active_threads)[0] if self.active_threads else "default"

                                if status == 'SENT' and biz_days >= self.stage_1_business_days:
                                    logger.info(f"Triggering Stage 1 Follow-up for {company} ({biz_days} biz days)")
                                    await self._trigger_followup(app_id, company, job_title, contact, 1, target_thread)

                                elif status in {'FOLLOWUP_1_SENT', 'FOLLOWUP_1_DRAFTED'} and biz_days >= self.stage_2_business_days:
                                    logger.info(f"Triggering Stage 2 Follow-up for {company}")
                                    await self._trigger_followup(app_id, company, job_title, contact, 2, target_thread)
                    else:
                        logger.info("Follow-up check skipped: applications table is not initialized yet.")
                    await conn.close()
                except Exception as e:
                    logger.error(f"Follow-up check failed: {e}")
                    self.last_error = f"Follow-up check failed: {e}"

            # 3. Daily Evolution Audit
            if self.audit_enabled and self.next_audit_at and now >= self.next_audit_at:
                logger.info("Triggering Daily Evolution Audit...")
                target_thread = list(self.active_threads)[0] if self.active_threads else "default"
                await self._run_audit(target_thread)
                self.last_audit_date = now.date()
                self.next_audit_at = self._compute_next_audit_at(now + timedelta(seconds=1))

            # 4. Wake up active threads for general tasks
            summaries = []
            for thread_id in list(self.active_threads):
                try:
                    cycle_start = datetime.now()
                    collected = []
                    tracked_snapshot = await self._get_company_progress_snapshot(thread_id)

                    research_chunks = await self._collect_agent_output(
                        (
                            f"{self.background_research_prompt}\n\n"
                            f"{tracked_snapshot}\n\n"
                            "Cycle diversification rule: keep advancing tracked companies with real next actions, "
                            "but if the same companies have already been researched recently or lack a strong next step, "
                            "add at least one new company to investigate this cycle."
                        ),
                        thread_id,
                    )
                    collected.extend(research_chunks)

                    execution_prompt = self.background_execution_prompt
                    if self.require_action_per_cycle:
                        execution_prompt = (
                            f"{execution_prompt}\n\n"
                            "Cycle completion rule: do not stop at research alone. "
                            "This phase is only complete after at least one concrete low-risk action is executed, "
                            "or after you explicitly report the blocker that prevented execution."
                        )
                    execution_chunks = await self._collect_agent_output(
                        execution_prompt,
                        thread_id,
                    )
                    collected.extend(execution_chunks)

                    executed_actions = self._executed_actions_since(cycle_start)
                    if self.require_action_per_cycle and not executed_actions:
                        logger.info(
                            f"No concrete execution action detected for {thread_id} in first pass. Running forced execution pass."
                        )
                        forced_prompt = (
                            "You did not finish the cycle because no concrete execution action was taken. "
                            "Do not do more broad research. Pick the best current target from available evidence and execute exactly one concrete low-risk action now. "
                            "Priority order: "
                            "1) linkedin_connect if a valid target profile is known and not connected; "
                            "2) linkedin_send_message if already connected; "
                            "3) create_email_draft for the strongest target if LinkedIn execution is blocked; "
                            "4) apply_on_company_site with submit=false if there is an official URL. "
                            "If every execution path is blocked, state the exact blocker and the attempted path. "
                            "Do not call something a LinkedIn UI blocker unless a real linkedin_connect_preview "
                            "or linkedin_connect attempt failed for a specific profile URL."
                        )
                        forced_chunks = await self._collect_agent_output(forced_prompt, thread_id)
                        collected.extend(forced_chunks)
                        executed_actions = self._executed_actions_since(cycle_start)
                        logger.info(
                            f"Forced execution pass for {thread_id} completed. Executed actions: {sorted(executed_actions) or ['none']}"
                        )

                    summary_chunks = await self._collect_agent_output(
                        (
                            f"{self.background_summary_prompt}\n\n"
                            f"{tracked_snapshot}\n\n"
                            "When relevant, distinguish between existing tracked companies and newly introduced companies."
                        ),
                        thread_id,
                    )
                    summary = "\n".join(c for c in summary_chunks if c).strip()
                    if not summary:
                        summary = "\n".join(c for c in collected if c).strip()
                    tracking_payload = self._extract_company_tracking_payload(summary)
                    await self._save_company_tracking_payload(thread_id, cycle_start, tracking_payload)
                    summary = self._strip_company_tracking_marker(summary)
                    if summary:
                        summaries.append(f"[{thread_id}] {summary}")
                        self.last_cycle_updates[thread_id] = summary
                        logger.info(f"Background Update [{thread_id}]: {summary[:120]}...")
                    else:
                        self.last_cycle_updates[thread_id] = "Cycle completed without visible summary."
                except Exception as e:
                    logger.error(f"Error in autonomous cycle: {e}")
                    self.last_error = f"Autonomous cycle error: {e}"
                    self.last_cycle_updates[thread_id] = f"Error: {e}"

            if summaries:
                self.last_summary = "\n\n".join(summaries)
                if self.summary_every_cycles > 0 and self.cycle_count % self.summary_every_cycles == 0:
                    await telegram.send_message(
                        f"🗂️ 后台求职摘要（第 {self.cycle_count} 轮）\n\n{self.last_summary[:self.summary_max_chars]}"
                    )
            elif not self.last_summary:
                self.last_summary = "Background cycle completed. No visible summary was generated."

            self.last_cycle_completed_at = datetime.now().isoformat(timespec="seconds")
            self.next_cycle_at = datetime.now() + timedelta(seconds=self.interval)
            next_cycle_text = self.next_cycle_at.isoformat(timespec="seconds")
            next_audit_text = self.next_audit_at.isoformat(timespec="seconds") if self.next_audit_at else "disabled"
            logger.update_state(
                node="Idle",
                thought=(
                    f"Background cycle completed. Waiting for next cycle at {next_cycle_text}. "
                    f"Next audit at {next_audit_text}."
                ),
            )

    async def _collect_agent_output(self, prompt: str, thread_id: str) -> list[str]:
        chunks: list[str] = []
        async for chunk in self.agent.run(input_msg=prompt, thread_id=thread_id):
            if chunk:
                chunks.append(chunk.strip())
        return chunks

    def _executed_actions_since(self, start_time: datetime) -> Set[str]:
        trace_path = logger.trace_file
        if not trace_path:
            return set()
        try:
            actions: Set[str] = set()
            with open(trace_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    if event.get("event") != "tool_complete":
                        continue
                    tool = str(event.get("tool") or "")
                    if tool not in self.execution_tools:
                        continue
                    ts = event.get("timestamp")
                    if not ts:
                        continue
                    try:
                        event_time = datetime.fromisoformat(ts)
                    except Exception:
                        continue
                    if event_time >= start_time and event.get("success", False):
                        actions.add(tool)
            return actions
        except Exception as e:
            logger.warn(f"Failed to inspect execution actions from trace log: {e}")
            return set()

    async def _run_audit(self, thread_id: str):
        """Triggers the Auditor subagent to analyze performance and propose upgrades."""
        logger.info(f"Auditor Subagent initiated (Daily Cycle) for thread {thread_id}")
        
        full_report = ""
        try:
            async for chunk in self.auditor.run_audit(thread_id=f"auto_{thread_id}"):
                if chunk:
                    full_report += chunk
            
            if full_report:
                # Push report to Telegram if enabled
                chat_id = config.get("channels.telegram.chat_id")
                if chat_id:
                     await telegram.send_message(
                         f"📊 **每日审计自动化报告**\n\n{full_report[:3500]}\n\n⚖️ 请查阅以上建议，如需执行请进行手动授权。"
                     )
            logger.info("Daily Audit completed.")
        except Exception as e:
            logger.error(f"Daily Audit failed: {e}")

    async def _trigger_followup(self, app_id: int, company: str, title: str, contact: str, stage: int, thread_id: str):
        """Triggers the agent to generate a draft and notifies the user."""
        business_days = self.stage_1_business_days if stage == 1 else self.stage_2_business_days
        prompt = (
            f"It has been {business_days} business days since our last contact with {contact} "
            f"at {company} regarding the {title} position. Please draft a stage {stage} "
            "follow-up email and save it as a draft."
        )
        
        async for chunk in self.agent.run(input_msg=prompt, thread_id=thread_id):
            pass # We just want it to execute the tools
            
        # Update status in DB
        new_status = f"FOLLOWUP_{stage}_DRAFTED"
        conn = await aiosqlite.connect("data/harness.db")
        await conn.execute("UPDATE applications SET status = ?, last_contact_date = ? WHERE id = ?", (new_status, datetime.now().date(), app_id))
        await conn.commit()
        await conn.close()
        
        await telegram.send_message(f"🚨 **Stage {stage} Follow-up Created**\nCompany: {company}\nTarget: {contact}\nDraft is ready in your mailbox. Please review and send.")

    async def start(self):
        logger.info(f"Autonomous Scheduler started. Interval: {self.interval}s")
        self.next_cycle_at = datetime.now()
        logger.update_state(
            node="Idle",
            thought=(
                f"Scheduler initialized. First cycle starts now. "
                f"Next audit at {self.next_audit_at.isoformat(timespec='seconds') if self.next_audit_at else 'disabled'}."
            ),
        )
        if config.get("auto_run.enabled", True):
            try:
                await self._run_cycle()
            except Exception as e:
                self.last_error = f"Autonomous scheduler startup cycle failed: {e}"
                logger.error(self.last_error)
        while True:
            self.next_cycle_at = datetime.now() + timedelta(seconds=self.interval)
            logger.update_state(
                node="Idle",
                thought=(
                    f"Scheduler sleeping until {self.next_cycle_at.isoformat(timespec='seconds')}. "
                    f"Next audit at {self.next_audit_at.isoformat(timespec='seconds') if self.next_audit_at else 'disabled'}."
                ),
            )
            await asyncio.sleep(self.interval)
            if config.get("auto_run.enabled", True):
                try:
                    await self._run_cycle()
                except Exception as e:
                    self.last_error = f"Autonomous scheduler cycle failed: {e}"
                    logger.error(self.last_error)

    async def run_now(self):
        try:
            await self._run_cycle()
        except Exception as e:
            self.last_error = f"Manual scheduler cycle failed: {e}"
            logger.error(self.last_error)

def create_scheduler(agent: JobHunterAgent):
    interval = config.get("auto_run.interval_seconds", config.get("auto_check_interval", 3600))
    scheduler = AutonomousScheduler(agent, interval)
    auto_threads = config.get("channels.telegram.chat_id")
    if auto_threads:
        scheduler.add_thread(str(auto_threads))
    return scheduler
