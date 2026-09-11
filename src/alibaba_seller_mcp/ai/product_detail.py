"""AI generation of a structured product detail ("AI+结构化商详") with Claude.

Produces template-agnostic structured content — title, highlights, an ordered list
of detail modules (image captions + text blocks), attributes, keywords, FAQs — that
maps onto the ICBU publish schema (`productTitle`, `textDesc`, `detailImage`,
`customMoreProperty`). Three completeness tiers mirror Alibaba's detail templates:

    premium  (全能精装版)  — richest: highlights, many image+text modules, full
                            attribute table, FAQs
    lite     (经济简装版)  — core image+text modules + basic attributes
    general  (通用排版)    — standard image-text flow

Token usage is recorded through the :class:`UsageTracker`.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

from ..config import Config
from ..text_format import normalize_title
from ..usage.tracker import UsageTracker

VERSIONS = {
    "premium": (
        "全能精装版 (premium): the richest layout. Produce 6-10 detail modules mixing "
        "scene images (gallery 200), detail shots with captions (gallery 300) and text "
        "blocks; 3-5 highlights; a full attribute table (8-15 rows); and 2-3 FAQs."
    ),
    "lite": (
        "经济简装版 (lite): a lean layout. Produce 3-4 detail modules (core image "
        "captions + one or two text blocks), 2-3 highlights, and 5-8 key attributes. "
        "No FAQs."
    ),
    "general": (
        "通用排版 (general): a standard image-text flow. Produce 4-5 detail modules "
        "alternating image captions and short text, 2 highlights, and 5-8 attributes."
    ),
}

SYSTEM_PROMPT = (
    "You are an expert Alibaba.com Global B2B product-detail copywriter. "
    "You write accurate, B2B-buyer-oriented, keyword-aware content and never invent "
    "product facts that were not provided. Respect these hard limits: the title is at "
    "most 128 characters (including spaces), must avoid special characters (@ ! ！ ? ？ "
    "$ ^ { } ~ 、 and similar) — only - / , & . punctuation is allowed — and must not "
    "stack keywords; each image caption (generalText) is at most 500 characters.\n"
    "Title capitalization (Alibaba title case): capitalize the first word and every "
    "major word (nouns, verbs, adjectives, adverbs, and any word of 4+ letters). Keep "
    "these short words lowercase unless first: the a an; and but for nor or so yet if; "
    "at by for in of on to up (with may be lowercase). Leave brand/model/acronym tokens "
    "(e.g. C01B, USB, ABS) in their original casing.\n\n"
    "Organize the detail body into the standard Alibaba section order, tagging each "
    "module with a `section` from: highlights, scene, detail, product_dimensions, "
    "packaging_shipping, company_overview, factory_profile, certification. Produce the "
    "modules in that order (omit sections that don't apply for the tier).\n"
    "Return ONLY one JSON object, no prose and no markdown fences, shaped exactly as:\n"
    "{\n"
    '  "title": str,\n'
    '  "highlights": str,\n'
    '  "detail_version": "premium"|"lite"|"general",\n'
    '  "modules": [\n'
    '    {"type": "image", "section": str, "gallery": "200"|"300"|"350", "image_slot": int, "caption": str},\n'
    '    {"type": "text", "section": str, "text": str}\n'
    "  ],\n"
    '  "attributes": [{"name": str, "value": str}],\n'
    '  "company_intro": str,\n'
    '  "keywords": [str],\n'
    '  "faqs": [{"question": str, "answer": str}]\n'
    "}\n"
    "`image_slot` is a 0-based index into the seller's detail images; only use slots "
    "in range. gallery 300 supports captions; 200/350 captions may be empty. "
    "`company_intro` is the Company Introduction paragraph."
)


class ProductDetailGenerator:
    def __init__(self, config: Config, tracker: UsageTracker, *, client: anthropic.Anthropic | None = None):
        self.config = config
        self.tracker = tracker
        if client is not None:
            self._client = client
        elif config.anthropic_api_key:
            self._client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        else:
            self._client = anthropic.Anthropic()

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
        if version not in VERSIONS:
            raise ValueError(f"version must be one of {sorted(VERSIONS)}; got {version!r}")

        user_prompt = (
            f"Product: {product_name}\n"
            f"Key features: {', '.join(features) if features else '(none provided)'}\n"
            f"Target keywords: {', '.join(keywords) if keywords else '(none)'}\n"
            f"Target buyers: {target_market}\n"
            f"Tone: {tone}\n"
            f"Language: {language}\n"
            f"Seller has {detail_image_count} detail image(s) available "
            f"(image_slot 0..{max(detail_image_count - 1, 0)}).\n"
            f"Detail version to produce: {VERSIONS[version]}\n"
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
            label="product_detail",
            metadata={"version": version, "detail_image_count": detail_image_count},
        )

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        content = _extract_json(text)
        if content is None:
            return {"model": self.config.social_model, "content": None, "raw_text": text}
        content.setdefault("detail_version", version)
        # Enforce Alibaba title rules deterministically (AI guidance is a hint only):
        # clean disallowed punctuation, apply title case, cap at 128 characters.
        if content.get("title"):
            content["title"] = normalize_title(content["title"], brands=brands)
        return {
            "model": self.config.social_model,
            "content": content,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        }


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
        system = (
            "You select Alibaba category attribute values for a product. For each "
            "attribute, pick the value(s) that best fit the product; for select-type "
            "attributes choose ONLY from the listed options (exact strings); for "
            "free-text attributes give a short accurate value. Do not invent options. "
            'Return ONLY a JSON object mapping each attribute name to a string '
            "(single/free-text) or an array of strings (multi). Omit an attribute only "
            "if truly nothing fits."
        )
        user = f"Product: {brief}\nLanguage: {language}\nAttributes:\n" + "\n".join(lines)
        response = self._client.messages.create(
            model=self.config.social_model,
            max_tokens=2000,
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        self.tracker.record(
            self.config.social_model, response.usage, label="attribute_select",
            metadata={"count": len(attributes)},
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        data = _extract_json(text)
        return data or {}


def _extract_json(text: str) -> dict[str, Any] | None:
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
