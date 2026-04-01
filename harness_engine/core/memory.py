import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from harness_engine.core.logger import logger
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

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

class Fact:
    """A single piece of knowledge about the user or session."""
    def __init__(self, content: str, confidence: float = 1.0, created_at: str = None):
        self.content = content
        self.confidence = confidence
        self.created_at = created_at or datetime.now().isoformat()

    def to_dict(self):
        return {
            "content": self.content,
            "confidence": self.confidence,
            "created_at": self.created_at
        }

class LongTermMemory:
    """Manages long-term storage of user facts in a JSON file."""
    
    def __init__(self, storage_path: str = "data/memory.json"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.facts: List[Fact] = []
        self.load()

    def load(self):
        """Load facts from JSON storage."""
        if not self.storage_path.exists():
            self.facts = []
            return
            
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.facts = [Fact(**item) for item in data.get("facts", [])]
        except Exception as e:
            logger.warn(f"Failed to load memory from {self.storage_path}: {e}")
            self.facts = []

    def save(self):
        """Save facts to JSON storage."""
        try:
            data = {"facts": [f.to_dict() for f in self.facts], "last_updated": datetime.now().isoformat()}
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save memory to {self.storage_path}: {e}")

    def add_fact(self, content: str, confidence: float = 1.0):
        """Add a new fact if it doesn't already exist."""
        if self._looks_transient_company_fact(content):
            logger.info(f"Memory skipped transient company fact -> {content}")
            return

        # Simple deduplication based on content
        if any(f.content.lower() == content.lower() for f in self.facts):
            return
            
        self.facts.append(Fact(content, confidence))
        self.save()

    @staticmethod
    def _looks_transient_company_fact(content: str) -> bool:
        text = (content or "").strip()
        lowered = text.lower()
        if not text:
            return False

        transient_hints = [
            "targeting",
            "interested in",
            "for employment",
            "target company",
            "target companies",
            "shortlist",
        ]
        if not any(hint in lowered for hint in transient_hints):
            return False

        role_or_company_hints = [
            " at ",
            " roles at ",
            "jobs at",
            "openings at",
            "company ",
            "for employment",
            "for senior",
            "for leadership",
        ]
        has_scope_hint = any(hint in lowered for hint in role_or_company_hints)
        has_company_list_hint = ("," in text and " and " in lowered) or text.count(",") >= 2
        return has_scope_hint and has_company_list_hint

    def get_summary_prompt(self) -> str:
        """Generate a summary of known facts for the system prompt."""
        filtered_facts = [f for f in self.facts if not self._looks_transient_company_fact(f.content)]
        if not filtered_facts:
            return "No previous facts known."
            
        fact_list = [f"- {f.content}" for f in filtered_facts]
        return "\n".join(fact_list)

    async def update_from_history(self, messages: List[BaseMessage], model_factory: Any):
        """Extract new facts from the latest conversation history using an LLM."""
        if len(messages) < 2:
            return

        # Prepare extraction prompt
        history_text = ""
        for m in messages[-4:]: # Limit history context for extraction
            role = "User" if isinstance(m, HumanMessage) else "Agent"
            visible_text = _extract_text_content(getattr(m, "content", None))
            if visible_text:
                history_text += f"{role}: {visible_text}\n"

        if not history_text.strip():
            return

        system_prompt = """
        You are a Fact Extractor. Analyze the dialogue and extract NEW, IMPORTANT facts about the user's preferences, identity, or job-seeking context.
        
        Rules:
        1. Extract only core facts (e.g., 'User is looking for Python roles in Hangzhou').
        2. Do not repeat existing facts.
        3. Format each fact as a simple sentence.
        4. If no new facts are found, reply with 'NONE'.
        
        Reply only with the facts, one per line.
        """
        
        try:
            # We use a helper from the factory to create a model
            model = model_factory()
            response = await model.ainvoke([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"History:\n{history_text}"}
            ])
            
            new_facts_text = _extract_text_content(getattr(response, "content", None))
            if new_facts_text and new_facts_text != "NONE":
                for line in new_facts_text.split("\n"):
                    if line.strip():
                        self.add_fact(line.strip())
                        logger.info(f"Memory update: Found new fact -> {line.strip()}")
        except Exception as e:
            logger.warn(f"Failed to extract facts from history: {e}")

# Factory for creating and loading memory from config
def create_memory():
    from harness_engine.config import config
    storage_path = config.get("memory.storage_path", "data/memory.json")
    return LongTermMemory(storage_path)
