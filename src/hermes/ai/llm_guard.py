from typing import Any, Dict

REQUIRED_FIELDS = {
    "market_regime",
    "market_friendly",
    "recommended_profile",
    "decision",
    "risk_level",
    "confidence",
    "reasoning_tags",
}
ALLOWED_REGIMES = {"TRENDING", "LATERAL", "VOLATILE", "DEAD"}
ALLOWED_PROFILES = {"SENTINEL", "EQUILIBRIUM", "VORTEX", "NO_TRADE"}
ALLOWED_DECISIONS = {"ENABLE_TRADING", "DISABLE_TRADING", "INSUFFICIENT_DATA"}
ALLOWED_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH"}


class LLMGuard:
    @staticmethod
    def validate(response: Dict[str, Any]) -> Dict[str, Any]:
        missing = REQUIRED_FIELDS - response.keys()
        if missing:
            raise ValueError(f"LLM response missing fields: {missing}")

        market_regime = response.get("market_regime")
        if market_regime not in ALLOWED_REGIMES:
            raise ValueError("Invalid market_regime value")

        recommended_profile = response.get("recommended_profile")
        if recommended_profile not in ALLOWED_PROFILES:
            raise ValueError("Invalid recommended_profile value")

        decision = response.get("decision")
        if decision not in ALLOWED_DECISIONS:
            raise ValueError("Invalid decision value")

        risk_level = response.get("risk_level")
        if risk_level not in ALLOWED_RISK_LEVELS:
            raise ValueError("Invalid risk_level value")

        confidence = response.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0.0 <= confidence <= 1.0:
            raise ValueError("Invalid confidence value")

        tags = response.get("reasoning_tags")
        if not isinstance(tags, list):
            raise ValueError("Invalid reasoning_tags value")
        if any(not isinstance(tag, str) for tag in tags):
            raise ValueError("Invalid reasoning_tags value")
        if len(tags) > 3:
            raise ValueError("Invalid reasoning_tags value")

        return response
