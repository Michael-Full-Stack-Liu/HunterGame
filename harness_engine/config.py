import os
import re
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

def resolve_env_vars(data: Any) -> Any:
    """Recursively resolve environment variables in the config data."""
    if isinstance(data, dict):
        return {k: resolve_env_vars(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [resolve_env_vars(i) for i in data]
    elif isinstance(data, str):
        # Match $VAR or ${VAR}
        pattern = re.compile(r'\$\{?([a-zA-Z_][a-zA-Z0-9_]*)\}?')
        def replace(match):
            var_name = match.group(1)
            return os.getenv(var_name, match.group(0))
        return pattern.sub(replace, data)
    return data

class Config:
    """Unified configuration loader for the Job Hunter engine."""
    
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._loaded = False
        return cls._instance

    def __init__(self, config_path: str = "config.yaml"):
        if self._loaded:
            return
            
        # Ensure .env is loaded first
        load_dotenv()
        
        root_path = Path(__file__).parent.parent
        full_path = root_path / config_path
        
        if not full_path.exists():
            # Fallback to current directory if not found relative to package
            full_path = Path(config_path)
            
        if not full_path.exists():
            raise FileNotFoundError(f"Config file not found at {full_path}")

        with open(full_path, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f)
            self._data = resolve_env_vars(raw_data)
        
        self._loaded = True

    def get(self, key_path: str, default: Any = None) -> Any:
        """Get a value using a dot-separated path (e.g., 'channels.telegram.token')."""
        keys = key_path.split(".")
        value = self._data
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default

    @property
    def models(self) -> List[Dict[str, Any]]:
        return self._data.get("models", [])

    @property
    def telegram_config(self) -> Dict[str, Any]:
        return self._data.get("channels", {}).get("telegram", {})

    @property
    def firecrawl_key(self) -> Optional[str]:
        # Harmonized lookup for Firecrawl API key
        return self.get("tools.firecrawl.api_key")

    @property
    def memory_config(self) -> Dict[str, Any]:
        return self._data.get("memory", {})

    @property
    def sandbox_config(self) -> Dict[str, Any]:
        return self._data.get("sandbox", {})

# Global configuration instance
config = Config()
