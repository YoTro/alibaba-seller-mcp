"""AI-generated structured product detail ("AI+结构化商详") and category-attribute
selection.

Produces template-agnostic text content — title, highlights, an ordered list of
detail modules (image captions + text blocks), attributes, keywords, FAQs — that
maps onto the ICBU publish schema (`productTitle`, `textDesc`, `detailImage`,
`customMoreProperty`). Three completeness tiers mirror Alibaba's detail templates
(premium / lite / general, see :data:`prompts.PRODUCT_DETAIL_VERSIONS`).

Detail *images* are a separate concern: see :mod:`detail_spec`.
"""

from __future__ import annotations

from typing import Any

from ..text_format import normalize_title
from .base import ClaudeGenerator, extract_json, usage_dict
from .prompts import ATTRIBUTE_SELECT_SYSTEM, PRODUCT_DETAIL_SYSTEM, PRODUCT_DETAIL_VERSIONS


class ProductDetailGenerator(ClaudeGenerator):
    def generate(
        self,
        *,
        product_name: str,
        version: str = "premium",
        features: list[str] | None = None,
        keywords: list[str] | None = None,
        target_market: str = "global B2B buyers",
        tone: str = "professional",
        language: str = "English",
        detail_image_count: int = 0,
        brands: list[str] | None = None,
        extra_instructions: str = "",
        max_tokens: int = 8000,
    ) -> dict[str, Any]:
        if version not in PRODUCT_DETAIL_VERSIONS:
            raise ValueError(f"version must be one of {sorted(PRODUCT_DETAIL_VERSIONS)}; got {version!r}")

        user_prompt = (
            f"Product: {product_name}\n"
            f"Key features: {', '.join(features) if features else '(none provided)'}\n"
            f"Target keywords: {', '.join(keywords) if keywords else '(none)'}\n"
            f"Target buyers: {target_market}\n"
            f"Tone: {tone}\n"
            f"Language: {language}\n"
            f"Seller has {detail_image_count} detail image(s) available "
            f"(image_slot 0..{max(detail_image_count - 1, 0)}).\n"
            f"Detail version to produce: {PRODUCT_DETAIL_VERSIONS[version]}\n"
        )
        if extra_instructions:
            user_prompt += f"\nAdditional instructions: {extra_instructions}\n"

        text, usage = self._complete(
            system=PRODUCT_DETAIL_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=max_tokens,
            label="product_detail",
            metadata={"version": version, "detail_image_count": detail_image_count},
        )
        content = extract_json(text)
        if content is None:
            return {"model": self.model, "content": None, "raw_text": text}
        content.setdefault("detail_version", version)
        # Enforce Alibaba title rules deterministically (AI guidance is a hint only):
        # clean disallowed punctuation, apply title case, cap at 128 characters.
        if content.get("title"):
            content["title"] = normalize_title(content["title"], brands=brands)
        return {"model": self.model, "content": content, "usage": usage_dict(usage)}

    def select_attributes(
        self, brief: str, attributes: list[dict[str, Any]], *, language: str = "English"
    ) -> dict[str, Any]:
        """Have Claude choose category-attribute values from each attribute's ALLOWED
        options, given the product brief.

        ``attributes``: [{"name", "type" (singleCheck/multiCheck/input), "options":
        [displayName, ...]}]. Returns {name: chosen displayName | [displayNames] |
        free-text}. Only values from ``options`` are used for select types; the tool
        maps display names to option codes afterward.
        """
        if not attributes:
            return {}
        lines = []
        for a in attributes:
            opts = a.get("options") or []
            multi = a.get("type") == "multiCheck"
            kind = "choose one or more" if multi else ("choose one" if a.get("type") == "singleCheck" else "free text")
            opt_str = ("; options: " + " | ".join(opts[:60])) if opts else ""
            lines.append(f"- {a['name']} ({kind}){opt_str}")
        user = f"Product: {brief}\nLanguage: {language}\nAttributes:\n" + "\n".join(lines)
        text, _ = self._complete(
            system=ATTRIBUTE_SELECT_SYSTEM,
            messages=[{"role": "user", "content": user}],
            max_tokens=2000,
            label="attribute_select",
            metadata={"count": len(attributes)},
        )
        return extract_json(text) or {}
