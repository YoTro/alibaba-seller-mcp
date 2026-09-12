"""AI-written **detail-image spec**: Claude fills the page templates defined in
:mod:`rendering.spec` with short, factual text; the deterministic renderer then
draws the pages. This is the cheap, repeatable alternative to image models — one
call (~6k tokens) per product, then free re-renders after hand edits.
"""

from __future__ import annotations

import base64
from typing import Any

from pydantic import ValidationError

from ..rendering.spec import PagesOnly
from .base import ClaudeGenerator, extract_json
from .prompts import DETAIL_SPEC_SYSTEM


class DetailSpecGenerator(ClaudeGenerator):
    def generate(
        self,
        *,
        brand: str,
        product_name: str,
        model: str = "",
        facts: str,
        features: list[str] | None = None,
        oem_odm: str = "",
        scene_captions: list[str] | None = None,
        has_side_render: bool = True,
        language: str = "English",
        extra_instructions: str = "",
        product_image: bytes | None = None,
        product_image_type: str = "image/jpeg",
        max_tokens: int = 6000,
    ) -> dict[str, Any]:
        """Return ``{"model", "pages", "usage"}``.

        ``product_image`` (the side render, downscaled) lets the model place
        ``callouts`` coordinates on the real product. The JSON is validated against
        :class:`PagesOnly`; on a validation error the model gets one retry with the
        error text, then a ``ValueError`` is raised.
        """
        user_prompt = (
            f"Brand: {brand or '(none — do not print a brand)'}\nProduct: {product_name}\nModel: {model or '(none)'}\n"
            f"Facts:\n{facts}\n"
            f"Key features: {', '.join(features) if features else '(none provided)'}\n"
            f"OEM/ODM offer: {oem_odm or '(not offered — omit the oem_odm page)'}\n"
            f"Photos available: hero render yes; side render {'yes' if has_side_render else 'no'}; "
            f"scene photos: {', '.join(scene_captions) if scene_captions else 'none (omit the scenes page)'}\n"
            f"Language: {language}\n"
        )
        if extra_instructions:
            user_prompt += f"Additional instructions: {extra_instructions}\n"

        content: list[dict[str, Any]] = []
        if product_image:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": product_image_type,
                           "data": base64.standard_b64encode(product_image).decode("ascii")},
            })
            user_prompt += "The attached image is the side render used for callouts.\n"
        content.append({"type": "text", "text": user_prompt})
        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]

        usage_total = {"input_tokens": 0, "output_tokens": 0}
        last_error = ""
        for attempt in range(2):
            text, usage = self._complete(
                system=DETAIL_SPEC_SYSTEM, messages=messages, max_tokens=max_tokens,
                label="detail_image_spec", metadata={"attempt": attempt + 1},
            )
            usage_total["input_tokens"] += usage.input_tokens
            usage_total["output_tokens"] += usage.output_tokens
            data = extract_json(text)
            try:
                if data is None:
                    raise ValueError("no JSON object in the response")
                pages = PagesOnly.model_validate(data)
                return {"model": self.model,
                        "pages": [p.model_dump(exclude_none=True) for p in pages.pages],
                        "usage": usage_total}
            except (ValidationError, ValueError) as exc:
                last_error = str(exc)
                messages.append({"role": "assistant", "content": text or "{}"})
                messages.append({
                    "role": "user",
                    "content": "Your JSON was rejected by the spec validator:\n"
                               f"{last_error[:3000]}\nReturn the corrected JSON object only.",
                })
        raise ValueError(f"detail spec still invalid after retry: {last_error[:800]}")
