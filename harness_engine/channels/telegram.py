import asyncio
import json
import logging
from typing import Optional, List, Dict, Any
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from harness_engine.config import config
from harness_engine.core.logger import logger
from harness_engine.core.agent import JobHunterAgent
from harness_engine.core.auditor import AuditorAgent
from harness_engine.tools import actuators

class TelegramChannel:
    """The Telegram bridge for the DeepAgents-powered engine."""
    
    def __init__(self, token: str, chat_id: Optional[int] = None, agent: Optional[JobHunterAgent] = None, scheduler=None):
        self.token = token
        self.allowed_chat_id = chat_id
        self.agent = agent
        self.scheduler = scheduler
        self.auditor = AuditorAgent() # We initialize a secondary agent instance for auditing
        self.application = Application.builder().token(token).build()

    @staticmethod
    def _is_group_chat(update: Update) -> bool:
        chat = update.effective_chat
        return bool(chat and chat.type in {"group", "supergroup"})

    async def _extract_group_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
        """In groups, only react when the bot is mentioned or a user replies to the bot."""
        if not update.message or not update.message.text:
            return None

        text = update.message.text.strip()
        if not self._is_group_chat(update):
            return text

        bot_username = context.bot.username or ""
        mention = f"@{bot_username}" if bot_username else ""

        if mention and mention.lower() in text.lower():
            return text.replace(mention, "").strip() or None

        reply_to = update.message.reply_to_message
        if reply_to and reply_to.from_user and context.bot.id == reply_to.from_user.id:
            return text

        return None

    async def push_message(self, chat_id: int, text: str):
        """Proactively push a message (e.g., from background scheduler)."""
        try:
            await self.application.bot.send_message(chat_id=chat_id, text=f"🔔 [DeepAgent 自驱动推送] {text}")
            logger.info(f"Pushed message to {chat_id}")
        except Exception as e:
            logger.error(f"Failed to push message: {e}")

    async def start_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        user = update.effective_user
        logger.info(f"User {user.first_name} started the bot.")
        await update.message.reply_text(
            f"你好 {user.first_name}！我是你的 DeepAgent 求职马甲。\n\n"
            "系统已升级至工业级架构：\n"
            "🧠 [Recursive Planning] 递归式任务拆解 (write_todos)\n"
            "📂 [FileSystem Memory] 物理文件系统级长程记忆\n"
            "🤝 [Sub-Agent Delegation] 多代理协作能力\n"
            "监控中..."
        )

    async def chatid_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show the current chat metadata for Telegram debugging."""
        chat = update.effective_chat
        user = update.effective_user
        await update.message.reply_text(
            "当前会话信息：\n"
            f"- chat_id: `{chat.id}`\n"
            f"- chat_type: `{chat.type}`\n"
            f"- user_id: `{user.id if user else 'unknown'}`",
            parse_mode="Markdown",
        )

    async def status_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show scheduler status for long-running autonomous mode."""
        chat_id = update.effective_chat.id
        if self.allowed_chat_id and chat_id != self.allowed_chat_id:
            return
        if not self.scheduler:
            await update.message.reply_text("当前未连接后台调度器。")
            return
        snapshot = self.scheduler.get_status_snapshot()
        text = (
            "后台运行状态：\n"
            f"- enabled: `{snapshot['enabled']}`\n"
            f"- running_now: `{snapshot['is_running']}`\n"
            f"- interval_seconds: `{snapshot['interval_seconds']}`\n"
            f"- cycle_count: `{snapshot['cycle_count']}`\n"
            f"- last_cycle_started_at: `{snapshot['last_cycle_started_at'] or 'N/A'}`\n"
            f"- last_cycle_completed_at: `{snapshot['last_cycle_completed_at'] or 'N/A'}`\n"
            f"- next_cycle_at: `{snapshot.get('next_cycle_at') or 'N/A'}`\n"
            f"- next_audit_at: `{snapshot.get('next_audit_at') or 'N/A'}`\n"
            f"- last_error: `{snapshot['last_error'] or 'None'}`\n"
            f"- active_threads: `{', '.join(snapshot['active_threads']) or 'None'}`"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def summary_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show the latest autonomous summary."""
        chat_id = update.effective_chat.id
        if self.allowed_chat_id and chat_id != self.allowed_chat_id:
            return
        if not self.scheduler:
            await update.message.reply_text("当前未连接后台调度器。")
            return
        snapshot = self.scheduler.get_status_snapshot()
        summary = snapshot.get("last_summary") or "暂时还没有后台摘要。"
        await update.message.reply_text(f"最近后台摘要：\n\n{summary[:3800]}")

    async def runnow_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Trigger one autonomous cycle immediately."""
        chat_id = update.effective_chat.id
        if self.allowed_chat_id and chat_id != self.allowed_chat_id:
            return
        if not self.scheduler:
            await update.message.reply_text("当前未连接后台调度器。")
            return
        await update.message.reply_text("已触发一轮后台巡航，我会在完成后把摘要记到 /summary。")
        asyncio.create_task(self.scheduler.run_now())

    async def approvals_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if self.allowed_chat_id and chat_id != self.allowed_chat_id:
            return
        pending = actuators._approval_store.list_pending()
        if not pending:
            await update.message.reply_text("当前没有待审批的官网申请。")
            return
        lines = ["待审批官网申请："]
        for item in pending[:20]:
            lines.append(
                f"- `{item['id']}` | {item['url']}\n"
                f"  screenshot: `{item.get('screenshot') or 'N/A'}`"
            )
        lines.append("\n使用 `/approve_apply <id>` 提交，或 `/reject_apply <id>` 放弃。")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def approve_apply_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if self.allowed_chat_id and chat_id != self.allowed_chat_id:
            return
        if not context.args:
            await update.message.reply_text("请提供 approval_id，例如：`/approve_apply abc12345`", parse_mode="Markdown")
            return
        approval_id = context.args[0].strip()
        record = actuators._approval_store.get(approval_id)
        if not record or record.get("status") != "pending":
            await update.message.reply_text("未找到待审批记录，或该记录已处理。")
            return

        profile_path = config.get("personal.profile_path", "data/profile.json")
        with open(profile_path, "r", encoding="utf-8") as f:
            profile = json.load(f)

        await update.message.reply_text(f"已收到授权，正在提交：`{approval_id}`", parse_mode="Markdown")
        result = await actuators._browser_actuator.fill_form(
            record["url"],
            profile,
            submit=True,
            force_submit=True,
        )
        new_status = "approved_submitted" if "Submission: submitted" in result else "approved_failed"
        actuators._approval_store.resolve(approval_id, new_status, {"submission_result": result})
        await update.message.reply_text(
            f"官网申请审批结果：\n- approval_id: `{approval_id}`\n- result: {result[:3500]}",
            parse_mode="Markdown",
        )

    async def reject_apply_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if self.allowed_chat_id and chat_id != self.allowed_chat_id:
            return
        if not context.args:
            await update.message.reply_text("请提供 approval_id，例如：`/reject_apply abc12345`", parse_mode="Markdown")
            return
        approval_id = context.args[0].strip()
        record = actuators._approval_store.resolve(approval_id, "rejected")
        if not record:
            await update.message.reply_text("未找到待审批记录，或该记录已处理。")
            return
        await update.message.reply_text(f"已拒绝提交：`{approval_id}`", parse_mode="Markdown")

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """Log uncaught Telegram handler errors."""
        logger.error(f"Telegram application error: {context.error}")

    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming text messages."""
        if not update.message or not update.message.text:
            return
            
        chat_id = update.effective_chat.id
        if self.allowed_chat_id and chat_id != self.allowed_chat_id:
            logger.warn(f"Unauthorized message from {chat_id}; expected {self.allowed_chat_id}")
            return

        user_message = await self._extract_group_prompt(update, context)
        if not user_message:
            logger.info(f"Ignoring group message in {chat_id}: bot not mentioned or replied to.")
            return

        logger.info(f"Received message: {user_message}")
        
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        status_msg = await update.message.reply_text("🔍 DeepAgent 正在进行任务拆解与分析...")
        
        try:
            full_response = ""
            # Stream from the agent (which now wraps deepagents)
            async for chunk in self.agent.run(user_message, thread_id=str(chat_id)):
                if chunk:
                    # Provide feedback on sub-steps if possible (could be extended)
                    full_response += chunk
            
            if full_response:
                if len(full_response) > 4000:
                    chunks = [full_response[i:i+4000] for i in range(0, len(full_response), 4000)]
                    await status_msg.edit_text(chunks[0])
                    for c in chunks[1:]:
                        await context.bot.send_message(chat_id=chat_id, text=c)
                else:
                    await status_msg.edit_text(full_response)
            else:
                await status_msg.edit_text("抱歉，DeepAgent 暂时没有生成反馈。")
        except Exception as e:
            logger.error(f"Error in DeepAgent Telegram handler: {e}")
            await status_msg.edit_text(f"❌ 系统级错误: {str(e)}")

    async def audit_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /audit command."""
        chat_id = update.effective_chat.id
        if self.allowed_chat_id and chat_id != self.allowed_chat_id:
            return

        logger.info("Manual audit triggered via Telegram.")
        status_msg = await update.message.reply_text("🕵️‍♂️ **正在启动系统审计官 (Auditor Subagent)...**\n分析中，请稍候...", parse_mode='Markdown')
        
        try:
            full_report = ""
            async for chunk in self.auditor.run_audit(thread_id=f"audit_{chat_id}"):
                if chunk:
                    full_report += chunk
            
            if full_report:
                # Telegram limited to 4096 chars
                if len(full_report) > 4000:
                   for i in range(0, len(full_report), 4000):
                       await context.bot.send_message(chat_id=chat_id, text=full_report[i:i+4000])
                else:
                    await status_msg.edit_text(full_report)
                
                await context.bot.send_message(
                    chat_id=chat_id, 
                    text="⚖️ **审计结果已送达**\n\n以上为审计提议。如需执行修改，请复制对应的 `update_skill` 命令并在对话中发送授权指令。"
                )
            else:
                await status_msg.edit_text("⚠️ 审计官未发现明显待优化项。系统目前运行平稳。")
        except Exception as e:
            logger.error(f"Auditor failed: {e}")
            await status_msg.edit_text(f"❌ 审计官运行错误: {str(e)}")

    async def run_polling(self):
        """Start polling loop asynchronously."""
        self.application.add_handler(CommandHandler("start", self.start_handler))
        self.application.add_handler(CommandHandler("chatid", self.chatid_handler))
        self.application.add_handler(CommandHandler("status", self.status_handler))
        self.application.add_handler(CommandHandler("summary", self.summary_handler))
        self.application.add_handler(CommandHandler("runnow", self.runnow_handler))
        self.application.add_handler(CommandHandler("approvals", self.approvals_handler))
        self.application.add_handler(CommandHandler("approve_apply", self.approve_apply_handler))
        self.application.add_handler(CommandHandler("reject_apply", self.reject_apply_handler))
        self.application.add_handler(CommandHandler("audit", self.audit_handler))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.message_handler))
        self.application.add_error_handler(self.error_handler)
        
        logger.info("Initializing DeepAgent bot polling...")
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        logger.info("DeepAgent Bot polling started.")

async def send_message(text: str, chat_id: Optional[int] = None):
    """Send a proactive Telegram message without requiring a running channel instance."""
    token = config.get("channels.telegram.token")
    target_chat_id = chat_id or config.get("channels.telegram.chat_id")
    if not token or not target_chat_id:
        logger.warn("Telegram proactive push skipped: missing token or chat_id.")
        return False

    try:
        bot = Bot(token=token)
        await bot.send_message(chat_id=int(target_chat_id), text=text)
        logger.info(f"Proactive Telegram message sent to {target_chat_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to send proactive Telegram message: {e}")
        return False

def create_telegram_channel(agent: JobHunterAgent, scheduler=None):
    token = config.get("channels.telegram.token")
    raw_chat_id = config.get("channels.telegram.chat_id")
    chat_id = int(raw_chat_id) if raw_chat_id else None
    
    if not token:
        logger.error("Telegram TOKEN is not configured.")
        return None
        
    return TelegramChannel(token=token, chat_id=chat_id, agent=agent, scheduler=scheduler)
