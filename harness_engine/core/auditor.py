from typing import Optional, List, Dict, Any, AsyncGenerator
from langchain_core.messages import SystemMessage, HumanMessage
from deepagents import create_deep_agent

from harness_engine.config import config
from harness_engine.core.logger import logger
from harness_engine.core.agent import create_model, _extract_text_content
from harness_engine.tools import get_all_tools

class AuditorAgent:
    """Specialized Subagent for system performance auditing and strategy evolution."""
    
    def __init__(self, checkpointer=None):
        self.tools = get_all_tools()
        self.checkpointer = checkpointer
        
        # Load Auditor specific instructions
        try:
            with open("skills/custom/auditor/SKILL.md", "r", encoding="utf-8") as f:
                content = f.read()
                # Strip YAML frontmatter
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        self.instruction = parts[2].strip()
                    else:
                        self.instruction = content
                else:
                    self.instruction = content
        except Exception as e:
            logger.error(f"Failed to load Auditor instructions: {e}")
            self.instruction = "You are the Job Hunter Auditor. Analyze performance and suggest improvements."

        # Add strict 'proposal-only' instruction
        self.instruction += "\n\nIMPORTANT: You are in 'Proposal Mode'. Do NOT directly call update_skill unless explicitly told that the user has ALREADY authorized the specific change. Your goal is to analyze data and output a 'Strategic Diagnosis Report' for the user to review."

        self.app = create_deep_agent(
            model=create_model(), # Can be swapped for a stronger model if needed
            tools=self.tools,
            system_prompt=self.instruction,
            checkpointer=checkpointer,
        )

    async def run_audit(self, thread_id: str = "auditor_session") -> AsyncGenerator[str, None]:
        """Runs the audit cycle and yields the analysis report."""
        logger.info(f"Starting Auditor Subagent run (Thread: {thread_id})...")

        prompt = (
            "Perform a full audit in two parts: (1) System Audit and (2) Strategy Audit. "
            "First call 'performance_auditor' to obtain the latest JHHS metrics. "
            "Then analyze reliability, efficiency, activity, and automation quality. "
            "Also assess job-search strategy quality: targeting, research quality, outreach quality, "
            "follow-up discipline, and candidate positioning. "
            "Use the required report structure from your instructions and output only a proposal-mode diagnosis with prioritized fixes."
        )
        
        inputs = {
            "messages": [
                SystemMessage(content=self.instruction),
                HumanMessage(content=prompt)
            ]
        }
        
        config_run = {"configurable": {"thread_id": thread_id}}
        result = await self.app.ainvoke(inputs, config=config_run)
        messages = result.get("messages", []) if isinstance(result, dict) else []
        for msg in reversed(messages):
            visible_text = _extract_text_content(getattr(msg, "content", None))
            if visible_text:
                yield visible_text
                return
