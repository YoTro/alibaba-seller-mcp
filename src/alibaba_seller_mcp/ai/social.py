"""Social-media posts for a product, per platform, with Claude."""

from __future__ import annotations

from typing import Any

from .base import ClaudeGenerator, extract_json, usage_dict
from .prompts import SOCIAL_LENGTH_HINTS, SOCIAL_SYSTEM


class SocialContentGenerator(ClaudeGenerator):
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
            f"- {p}: {SOCIAL_LENGTH_HINTS.get(p.lower(), 'follow platform norms')}" for p in platforms
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

        text, usage = self._complete(
            system=SOCIAL_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=max_tokens,
            label="social_content",
            metadata={"platforms": platforms, "variants": variants},
        )
        posts = _parse_posts(text)
        return {"model": self.model, "posts": posts, "raw_text": None if posts else text,
                "usage": usage_dict(usage)}


def _parse_posts(text: str) -> list[dict[str, Any]] | None:
    data = extract_json(text)
    posts = (data or {}).get("posts")
    return posts if isinstance(posts, list) else None
