import json
from pathlib import Path
from typing import Any, Dict

import ollama

DEFAULT_MODEL = "llama3.1:8b"


class HermesLLMClient:
    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model = model
        self.prompt = self._load_prompt()

    def _load_prompt(self) -> str:
        path = Path(__file__).resolve().parent / "prompts" / "hermes_core_v1.txt"
        return path.read_text(encoding="utf-8")

    def analyze_market(self, market_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "prompt": self._build_prompt(market_snapshot),
            "format": "json",
            "stream": False,
        }
        response = ollama.generate(**payload)
        content = response.get("response", "")
        return json.loads(content)

    def _build_prompt(self, market_snapshot: Dict[str, Any]) -> str:
        snapshot = json.dumps(market_snapshot, ensure_ascii=False, sort_keys=True)
        return f"{self.prompt}\n\nINPUT:\n{snapshot}"
