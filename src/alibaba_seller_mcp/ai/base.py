"""Shared plumbing for the Claude generators: client construction, one call
helper that records token usage, and tolerant JSON extraction.

Each generator (product detail, detail-image spec) subclasses
:class:`ClaudeGenerator` and owns exactly one concern; prompts live in
:mod:`prompts`.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

from ..config import Config
from ..usage.tracker import UsageTracker


class ClaudeGenerator:
    def __init__(self, config: Config, tracker: UsageTracker, *, client: anthropic.Anthropic | None = None):
        self.config = config
        self.tracker = tracker
        if client is not None:
            self._client = client
        elif config.anthropic_api_key:
            self._client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        else:
            # Falls back to ANTHROPIC_API_KEY / `ant auth login` profile in the environment.
            self._client = anthropic.Anthropic()

    @property
    def model(self) -> str:
        return self.config.social_model

    def _check_budget(self, label: str) -> None:
        """Stop before a call that would run past this session's token budget.

        Soft by design: the budget is checked before each call, so the total may
        overshoot by one call's worth rather than aborting work midway. It bounds
        a runaway loop, which is what costs money — it is not a billing control.
        """
        budget = getattr(self.config, "ai_token_budget", 0)
        spent = self.tracker.session_tokens
        if budget and spent >= budget:
            raise RuntimeError(
                f"AI token budget for this session is used up: {spent:,} of {budget:,} tokens "
                f"(refused a {label!r} call). Raise or clear ALIBABA_MCP_AI_TOKEN_BUDGET "
                "and restart the server, or set ai: false in the brief to skip AI entirely."
            )

    def _complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        label: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, Any]:
        """One adaptive-thinking call. Returns ``(text, usage)`` and records usage
        under ``label`` so ``usage_stats`` reflects real spend."""
        self._check_budget(label)
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            system=system,
            messages=messages,
        )
        self.tracker.record(self.model, response.usage, label=label, metadata=metadata)
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return text, response.usage


def usage_dict(usage: Any) -> dict[str, int]:
    return {"input_tokens": int(getattr(usage, "input_tokens", 0)),
            "output_tokens": int(getattr(usage, "output_tokens", 0))}


def extract_json(text: str) -> dict[str, Any] | None:
    """Parse the model's JSON object, tolerating stray fences or prose around it."""
    candidate = text
    if "```" in candidate:
        for part in candidate.split("```"):
            part = part.strip()
            if part.startswith("{"):
                candidate = part
                break
            if part.lower().startswith("json"):
                candidate = part[4:].strip()
                break
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
