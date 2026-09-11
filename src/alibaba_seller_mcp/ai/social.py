"""Generate social-media content for a product with Claude.

Uses the official Anthropic SDK. Every call's token usage is recorded through the
:class:`UsageTracker`, so the usage-stats module reflects real spend.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

from ..config import Config
from ..usage.tracker import UsageTracker

SYSTEM_PROMPT = (
    "You are an expert B2B e-commerce social-media copywriter for sellers on "
    "Alibaba.com Global B2B. You write concise, platform-appropriate, "
    "conversion-oriented posts that respect each platform's norms and length "
    "limits, and you never invent product facts that were not provided.\n\n"
    "Return ONLY a single JSON object, no prose and no markdown fences, shaped as:\n"
    '{"posts": [{"platform": str, "variant": int, "text": str, '
    '"hashtags": [str], "call_to_action": str}]}'
)

_LENGTH_HINTS = {
    "twitter": "<= 280 characters, punchy, 1-3 hashtags",
    "x": "<= 280 characters, punchy, 1-3 hashtags",
    "instagram": "engaging caption, up to ~150 words, 5-12 relevant hashtags",
    "facebook": "friendly, 50-120 words, minimal hashtags",
    "linkedin": "professional B2B tone, 80-150 words, 3-5 hashtags",
    "tiktok": "short hook-driven caption, trend-aware, 3-6 hashtags",
    "pinterest": "descriptive keyword-rich caption, 2-5 hashtags",
}


class SocialContentGenerator:
    def __init__(self, config: Config, tracker: UsageTracker, *, client: anthropic.Anthropic | None = None):
        self.config = config
        self.tracker = tracker
        if client is not None:
            self._client = client
        elif config.anthropic_api_key:
            self._client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        else:
            # Falls back to ANTHROPIC_API_KEY / ant auth profile in the environment.
            self._client = anthropic.Anthropic()

    def generate(
        self,
        *,
        product_name: str,
        platforms: list[str],
        features: list[str] | None = None,
        keywords: list[str] | None = None,
        tone: str = "professional",
        language: str = "English",
        variants: int = 1,
        extra_instructions: str = "",
        max_tokens: int = 4000,
    ) -> dict[str, Any]:
        if not platforms:
            raise ValueError("At least one platform is required.")

        platform_lines = "\n".join(
            f"- {p}: {_LENGTH_HINTS.get(p.lower(), 'follow platform norms')}"
            for p in platforms
        )
        user_prompt = (
            f"Product: {product_name}\n"
            f"Key features: {', '.join(features) if features else '(none provided)'}\n"
            f"Target keywords: {', '.join(keywords) if keywords else '(none)'}\n"
            f"Tone: {tone}\n"
            f"Language: {language}\n"
            f"Variants per platform: {variants}\n"
            f"Platforms and constraints:\n{platform_lines}\n"
        )
        if extra_instructions:
            user_prompt += f"\nAdditional instructions: {extra_instructions}\n"

        response = self._client.messages.create(
            model=self.config.social_model,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        self.tracker.record(
            self.config.social_model,
            response.usage,
            label="social_content",
            metadata={"platforms": platforms, "variants": variants},
        )

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        posts = _parse_posts(text)
        return {
            "model": self.config.social_model,
            "posts": posts,
            "raw_text": None if posts else text,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        }


def _parse_posts(text: str) -> list[dict[str, Any]] | None:
    """Parse the model's JSON, tolerating stray fences/prose around it."""
    candidate = text
    if "```" in candidate:
        # strip a ```json ... ``` fence if the model added one
        parts = candidate.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("{") or part.lower().startswith("json"):
                candidate = part[4:].strip() if part.lower().startswith("json") else part
                break
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    posts = data.get("posts")
    return posts if isinstance(posts, list) else None
