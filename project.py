import json
from pathlib import Path

_PREFS_PATH = Path.home() / ".mutr.json"
_MAX_RECENT = 10


def _to_rel(abs_path: str, base: Path) -> str:
    try:
        return str(Path(abs_path).relative_to(base))
    except ValueError:
        return abs_path


def save_project(path: Path, state: dict) -> None:
    base = path.parent
    data = dict(state)
    data["tracks"] = [
        {**t,
         "file": _to_rel(t["file"], base),
         "source_file": _to_rel(t["source_file"], base)}
        for t in state["tracks"]
    ]
    path.write_text(json.dumps(data, indent=2))


def load_project(path: Path) -> dict:
    data = json.loads(path.read_text())
    base = path.parent
    data["tracks"] = [
        {**t,
         "file": str((base / t["file"]).resolve()),
         "source_file": str((base / t["source_file"]).resolve())}
        for t in data["tracks"]
    ]
    return data


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
