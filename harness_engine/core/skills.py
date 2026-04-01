import os
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Set

class Skill:
    """Representation of a pluggable AI skill."""
    def __init__(self, name: str, description: str, content: str, path: Path):
        self.name = name
        self.description = description
        self.content = content
        self.path = path

class SkillLoader:
    """Loads and hashes skills to provide light-weight indexing or full retrieval."""
    def __init__(self, skill_paths: List[str]):
        self.base_paths = [Path(p) for p in skill_paths]
        self.skills: Dict[str, Skill] = {}
        self.load_all()

    def _parse_skill_file(self, file_path: Path) -> Optional[Skill]:
        if not file_path.name.upper() == "SKILL.MD": return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            if not content.startswith("---"): return None
            parts = content.split("---", 2)
            if len(parts) < 3: return None
            metadata = yaml.safe_load(parts[1])
            body = parts[2].strip()
            name = metadata.get("name", file_path.parent.name)
            description = metadata.get("description", "No description.")
            return Skill(name=name, description=description, content=body, path=file_path)
        except Exception: return None

    def load_all(self):
        for base in self.base_paths:
            if not base.exists(): continue
            for root, _, files in os.walk(base):
                for file in files:
                    if file.upper() == "SKILL.MD":
                        skill = self._parse_skill_file(Path(root) / file)
                        if skill: self.skills[skill.name] = skill

    def get_skill_index(self) -> str:
        """Returns a lightweight list of available skills (name and description)."""
        if not self.skills: return "No specialized skills available."
        lines = ["## Available Specialized Skills (Index)"]
        for s in self.skills.values():
            lines.append(f"- **{s.name}**: {s.description}")
        lines.append("\n*Instructions: Use 'read_skill_instructions' to see the full SOP for any skill.*")
        return "\n".join(lines)

    def get_skill_content(self, name: str) -> str:
        """Retrieve the full Markdown instructions for a specific skill."""
        skill = self.skills.get(name)
        if skill:
            return f"### Skill Instructions: {skill.name}\n\n{skill.content}"
        return f"Skill '{name}' not found."

def create_skill_loader():
    from harness_engine.config import config
    skills_cfg = config.get("skills", {})
    if isinstance(skills_cfg, list):
        skill_paths = [s.get("path") for s in skills_cfg if "path" in s]
    elif isinstance(skills_cfg, dict):
        skill_paths = [v for k, v in skills_cfg.items() if "path" in k]
    else:
        skill_paths = []
        
    if not skill_paths:
        skill_paths = ["skills/public", "skills/custom"]
    return SkillLoader(skill_paths)
