import json
from pathlib import Path

from mutr_core.project import load_project, save_project

_PREFS_PATH = Path.home() / ".mutr.json"
_MAX_RECENT = 10


def load_prefs() -> dict:
    try:
        return json.loads(_PREFS_PATH.read_text())
    except Exception:
        return {"recent_projects": []}


def save_prefs(prefs: dict) -> None:
    try:
        _PREFS_PATH.write_text(json.dumps(prefs, indent=2))
    except Exception:
        pass


def update_recent(prefs: dict, path: str) -> None:
    recents = prefs.setdefault("recent_projects", [])
    if path in recents:
        recents.remove(path)
    recents.insert(0, path)
    prefs["recent_projects"] = recents[:_MAX_RECENT]
