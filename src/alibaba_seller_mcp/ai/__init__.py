"""Claude (Anthropic) generators — one class per concern, prompts in ``prompts``.

    product_detail.ProductDetailGenerator  title / highlights / modules / attributes / FAQs
    detail_spec.DetailSpecGenerator        page spec for the code-rendered detail images
    social.SocialContentGenerator          per-platform social posts
"""

from .detail_spec import DetailSpecGenerator
from .product_detail import ProductDetailGenerator
from .social import SocialContentGenerator

__all__ = ["DetailSpecGenerator", "ProductDetailGenerator", "SocialContentGenerator"]
