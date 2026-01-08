import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from hermes.utils.bot_config import BotConfig

CONFIG_DIR = Path("configs")


def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def config_path(bot_id: str) -> Path:
    _ensure_config_dir()
    return CONFIG_DIR / f"{bot_id}.json"


def save_config(config: BotConfig) -> None:
    _ensure_config_dir()
    payload = asdict(config)
    with config_path(config.bot_id).open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def load_config(bot_id: str) -> Optional[BotConfig]:
    path = config_path(bot_id)
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    data.setdefault("disable_max_buys_per_day", False)
    data.setdefault("disable_daily_budget", False)

    return BotConfig(**data)
