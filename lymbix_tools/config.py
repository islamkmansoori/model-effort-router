"""A small stand-in for Lymbix's config loader (written for model-effort-router, not copied).

Lymbix's own `lymbix/config.py` holds the whole product's settings. The modules copied here read only a few keys
(`model_routing`, `force_model_tier`, `budget`), so this loader returns those from `lymbix_tools.json` in the current
folder when it exists, and an empty config otherwise.
"""
import json
from pathlib import Path
from typing import Any, Dict, Optional


def load_config(root: Optional[Path] = None) -> Dict[str, Any]:
    path = (root or Path.cwd()) / "lymbix_tools.json"
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        pass
    return {}
