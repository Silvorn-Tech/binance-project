import json
from datetime import datetime
from pathlib import Path
from typing import Optional

STATE_DIR = Path("state")


def _ensure_state_dir() -> None:
    STATE_DIR.mkdir(exist_ok=True)


def state_path(symbol: str) -> Path:
    _ensure_state_dir()
    return STATE_DIR / f"{symbol.upper()}.json"


def save_state(symbol: str, data: dict) -> None:
    _ensure_state_dir()
    payload = dict(data)
    payload["last_update"] = datetime.utcnow().isoformat() + "Z"
    with state_path(symbol).open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def load_state(symbol: str) -> Optional[dict]:
    path = state_path(symbol)
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def clear_state(symbol: str) -> None:
    path = state_path(symbol)
    if path.exists():
        path.unlink()
