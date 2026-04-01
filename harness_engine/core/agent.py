import operator
import json
from pathlib import Path
from typing import Annotated, Dict, List, Literal, Optional, Sequence, TypedDict, Union, Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from deepagents import create_deep_agent

from harness_engine.config import config
from harness_engine.core.logger import logger
from harness_engine.core.skills import create_skill_loader
from harness_engine.core.memory import create_memory
from harness_engine.tools import get_all_tools

class AgentState(TypedDict):
    """DeepAgents state structure."""
    messages: Annotated[Sequence[BaseMessage], operator.add]
    todos: List[Dict[str, Any]] 
    memory_context: str
    skills_context: str
    thread_id: str


def _extract_text_content(content: Any) -> str:
    """Best-effort extraction of visible text from LangChain message content."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                if block.strip():
                    parts.append(block.strip())
                continue
            if isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
    return str(content).strip()


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def load_personal_context() -> str:
    """Load profile, job targets, and resume context into the agent prompt."""
    sections: List[str] = []

    profile_path = Path(config.get("personal.profile_path", "data/profile.json"))
    if profile_path.exists():
        try:
            profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
            sections.append(
                "## Candidate Profile\n"
                + json.dumps(profile_data, ensure_ascii=False, indent=2)
            )
        except Exception:
            profile_text = _read_text_file(profile_path)
            if profile_text:
                sections.append(f"## Candidate Profile\n{profile_text}")

    goals_path = Path(config.get("personal.goals_path", "data/job_targets.md"))
    if goals_path.exists():
        goals_text = _read_text_file(goals_path)
        if goals_text:
            sections.append(f"## Job Search Goals\n{goals_text}")

    resume_text_path = Path(config.get("personal.resume_text_path", "data/resume.md"))
    if resume_text_path.exists():
        resume_text = _read_text_file(resume_text_path)
        if resume_text:
            sections.append(f"## Resume\n{resume_text}")
    else:
        resume_path = Path(config.get("personal.resume_path", "data/resume.pdf"))
        if resume_path.exists() and resume_path.suffix.lower() in {".txt", ".md", ".json"}:
            resume_text = _read_text_file(resume_path)
            if resume_text:
                sections.append(f"## Resume\n{resume_text}")

    if not sections:
        return "No personal profile, goals, or resume context available."
    return "\n\n".join(sections)


def load_operation_policy() -> str:
    """Load concise operating preferences from config for the agent prompt."""
    policy = config.get("operation_policy", {})
    if not isinstance(policy, dict) or not policy:
        return "No additional operating policy configured."

    lines = ["## Operating Policy"]
    for key, value in policy.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)

def create_model(model_name: Optional[str] = None, **_: Any):
    if not model_name:
        model_name = config.models[0]["name"] if config.models else "gpt-4o"
    model_config = next((m for m in config.models if m["name"] == model_name), {})
    provider = model_config.get("use", "langchain_openai:ChatOpenAI")
    api_key = model_config.get("api_key", "")
    actual_model = model_config.get("model", model_name)
    base_url = model_config.get("base_url")
    default_headers = model_config.get("default_headers")
    normalized_model = str(actual_model).lower()
    normalized_base_url = str(base_url or "").lower()
    is_google_gemini = (
        "google" in provider.lower()
        or "gemini" in normalized_model
        or "generativelanguage.googleapis.com" in normalized_base_url
    )

    if is_google_gemini:
        # Gemini 3 function calling requires thought_signature propagation.
        # The native Google integration handles this, while generic OpenAI-compatible
        # wrappers may fail on multi-step tool use in agent loops.
        return ChatGoogleGenerativeAI(
            model=actual_model,
            google_api_key=api_key,
        )
    
    if "openai" in provider.lower():
        openai_kwargs = {
            "model": actual_model,
            "api_key": api_key,
            "streaming": True,
        }
        if base_url:
            openai_kwargs["base_url"] = base_url
        if default_headers:
            openai_kwargs["default_headers"] = default_headers
        return ChatOpenAI(**openai_kwargs)
    elif "anthropic" in provider.lower():
        return ChatAnthropic(model=actual_model, api_key=api_key, streaming=True)
    fallback_kwargs = {
        "model": actual_model,
        "api_key": api_key,
        "streaming": True,
    }
    if base_url:
        fallback_kwargs["base_url"] = base_url
    if default_headers:
        fallback_kwargs["default_headers"] = default_headers
    return ChatOpenAI(**fallback_kwargs)

class JobHunterAgent:
    """Agent Harness wrapping deepagents with dashboard integration."""
    
    def __init__(self, checkpointer=None):
        self.skill_loader = create_skill_loader()
        self.memory = create_memory()
        self.tools = get_all_tools()
        self.checkpointer = checkpointer
        
        try:
            with open("agent.md", "r", encoding="utf-8") as f:
                self.instruction = f.read()
        except Exception:
            self.instruction = "You are 'Job Hunter', a specialized AI career assistant."

        self.app = create_deep_agent(
            model=create_model(),
            tools=self.tools,
            system_prompt=self.instruction,
            checkpointer=checkpointer,
        )

    async def run(self, input_msg: str, thread_id: str = "default"):
        """Run and pipe DeepAgent internal states to the logger dashboard."""
        memory_ctx = self.memory.get_summary_prompt()
        skills_idx = self.skill_loader.get_skill_index()
        personal_ctx = load_personal_context()
        policy_ctx = load_operation_policy()
        emitted_response = False
        last_ai_visible_text = ""
        
        full_instr = f"""
{self.instruction}

## Memory & Context
{memory_ctx}

## Candidate Context
{personal_ctx}

{policy_ctx}

## Specialized Skills Index
{skills_idx}
        """

        config_run = {"configurable": {"thread_id": thread_id}}
        inputs = None
        if input_msg:
            inputs = {
                "messages": [SystemMessage(content=full_instr), HumanMessage(content=input_msg)]
            }
        else:
            inputs = {"messages": [SystemMessage(content=full_instr)]}
        
        async for output in self.app.astream(inputs, config=config_run):
            # Capture node-specific outputs for dashboard
            for node_name, state_update in output.items():
                logger.update_state(node=node_name)
                if not isinstance(state_update, dict):
                    continue
                
                # If planner update todos
                if "todos" in state_update:
                    logger.update_state(tasks=state_update["todos"])
                
                if node_name == "agent":
                    msgs = state_update.get("messages", [])
                    if msgs:
                        last_msg = msgs[-1]
                        visible_text = _extract_text_content(getattr(last_msg, "content", None))
                        if visible_text:
                            last_ai_visible_text = visible_text
                        # Capture thought process
                        logger.update_state(thought=visible_text)
                        if (
                            isinstance(last_msg, AIMessage)
                            and not last_msg.tool_calls
                            and visible_text
                        ):
                            emitted_response = True
                            yield visible_text
                
                elif node_name == "tools":
                    msgs = state_update.get("messages", [])
                    if msgs:
                        last_msg = msgs[-1]
                        if hasattr(last_msg, "name"):
                            logger.info(f"Tool {last_msg.name} finished.")
            
        latest_msgs = []
        if self.checkpointer is not None:
            # Optional: Sync memory facts from history when checkpoint state is available.
            state_values = (await self.app.aget_state(config=config_run)).values
            latest_msgs = state_values.get("messages", [])
            await self.memory.update_from_history(latest_msgs, create_model)

        if not emitted_response and latest_msgs:
            for msg in reversed(latest_msgs):
                if isinstance(msg, AIMessage):
                    visible_text = _extract_text_content(getattr(msg, "content", None))
                    if visible_text:
                        yield visible_text
                        return

        if not emitted_response and last_ai_visible_text:
            yield last_ai_visible_text

async def init_job_hunter():
    import aiosqlite
    db_path = "data/harness.db"
    conn = await aiosqlite.connect(db_path)
    checkpointer = AsyncSqliteSaver(conn)
    await checkpointer.setup()
    return JobHunterAgent(checkpointer=checkpointer)
