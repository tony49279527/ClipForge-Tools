"""
Auto-generate video prompts from scraped product data and optional images.
Uses the same AI pipeline (ARK API) to turn raw product info into optimized
storyboard prompts for each video platform.
"""
import json
import os
from dataclasses import dataclass, field
from typing import Optional

from product_scraper import ProductInfo


@dataclass
class VideoPromptSet:
    """A set of prompts for generating videos across platforms."""
    product_name: str
    target_audience: str = "DIY enthusiasts, homeowners, garage hobbyists"
    storyboard_frames: list = field(default_factory=list)  # [{clip_index, prompt_zh, prompt_en}]
    youtube_title: str = ""
    youtube_description: str = ""
    shorts_title: str = ""
    shorts_description: str = ""
    category_tags: list = field(default_factory=list)


def build_prompt_from_product(
    product: ProductInfo,
    user_images: Optional[list] = None,
    platform_hint: str = "youtube",  # youtube, shorts, tiktok, all
) -> str:
    """Build a structured prompt for the AI to generate storyboard frames.

    This is the key function: it takes raw scraped data and turns it into
    a prompt that tells the AI HOW to structure the video.
    """
    context = product.to_prompt_context()

    # Platform-specific instructions
    platform_guides = {
        "youtube": (
            "Long-form product demonstration video, 3-5 minutes. "
            "Structure: hook (problem) → product reveal → features demo → real use case → CTA."
        ),
        "shorts": (
            "Short vertical video, 15-60 seconds, 9:16 aspect. "
            "Fast cuts, text overlays, hook in first 1 second. "
            "Show the product in action immediately."
        ),
        "tiktok": (
            "Vertical short video, 15-60 seconds. "
            "Trendy, fast-paced, with captions. Start with a bold claim or surprising result."
        ),
        "all": (
            "Generate a versatile product video that can be cut into multiple formats. "
            "Start with the most visually compelling use case."
        ),
    }

    guide = platform_guides.get(platform_hint, platform_guides["all"])

    # Category-specific context
    category_hints = ""
    if product.category_hints:
        category_hints = f"The product belongs to: {' > '.join(product.category_hints)}."

    # Image reference note
    image_note = ""
    if user_images:
        image_note = f"Reference images of the actual product are available ({len(user_images)} images). Use these as visual reference for the product's appearance, color, and design."

    feature_text = ""
    if product.features:
        feature_text = "Key selling points: " + "; ".join(product.features[:5])

    prompt = f"""You are a professional video producer creating product videos for YouTube.

Product information:
{context}

{category_hints}
{feature_text}

Video format: {guide}
{image_note}

Task: Create a {4}-frame storyboard for this product video. For each frame, provide:
1. A Chinese prompt (prompt_zh) describing the visual scene in detail
2. An English prompt (prompt_en) with the same description

Frame 1: Hook - grab attention in 3 seconds. Show the problem or the most impressive result.
Frame 2: Product reveal - show the product clearly, its key features.
Frame 3: Demonstration - show the product being used in a real scenario (garage, workshop, home).
Frame 4: Result + CTA - show the final result and include a call to action.

Also provide:
- A YouTube video title (50-80 characters, keyword-rich, click-worthy)
- A YouTube video description (200-300 characters, with relevant keywords)
- 3-5 category/hashtag tags

Output as JSON:
{{
  "youtube_title": "...",
  "youtube_description": "...",
  "tags": ["...", "..."],
  "frames": [
    {{"clip_index": 1, "prompt_zh": "...", "prompt_en": "..."}},
    ...
  ]
}}

The video should appeal to {product.target_audience or "DIY enthusiasts and tool users"}.
Use realistic workshop/garage settings. The product should look professional and effective."""

    return prompt


def product_audience_guess(product: ProductInfo) -> str:
    """Guess target audience from product data."""
    text = (product.title + " " + product.description + " " + " ".join(product.features)).lower()

    audience_signals = [
        (["garage", "diy", "tool", "workshop", "repair", "fix", "home improvement"], "DIY enthusiasts, garage hobbyists, home mechanics"),
        (["rv", "camper", "trailer", "camping", "outdoor"], "RV owners, campers, outdoor enthusiasts"),
        (["car", "auto", "vehicle", "truck", "mechanic"], "Car enthusiasts, auto DIYers, mechanics"),
        (["kitchen", "cook", "bake", "chef", "food"], "Home cooks, kitchen enthusiasts"),
        (["garden", "lawn", "plant", "yard", "outdoor"], "Gardeners, homeowners, landscapers"),
        (["beauty", "skin", "hair", "spa", "salon"], "Beauty professionals, skincare enthusiasts"),
        (["fitness", "gym", "workout", "exercise", "sport"], "Fitness enthusiasts, athletes, gym-goers"),
        (["pet", "dog", "cat", "animal"], "Pet owners, animal lovers"),
    ]

    for keywords, audience in audience_signals:
        if any(kw in text for kw in keywords):
            return audience

    return "DIY enthusiasts, homeowners, general consumers"


def generate_prompts_from_url(
    product: ProductInfo,
    user_images: Optional[list] = None,
) -> dict:
    """Main entry: takes scraped product info, returns a complete prompt set.

    This is the function that bridges scraping → prompt generation.
    In the full flow, this output goes to the AI for actual storyboard generation.
    """

    audience = product_audience_guess(product)

    # Build platform-specific prompts
    youtube_prompt = build_prompt_from_product(product, user_images, "youtube")
    shorts_prompt = build_prompt_from_product(product, user_images, "shorts")

    return {
        "product_name": product.title or "New Product",
        "target_audience": audience,
        "category_tags": product.category_hints[:5],
        "has_images": len(product.image_urls) > 0,
        "image_count": len(product.image_urls),
        "scraped_features": product.features[:8],
        "youtube_prompt": youtube_prompt,
        "shorts_prompt": shorts_prompt,
        "raw_product": {
            "title": product.title,
            "description": product.description[:300],
            "price": product.price,
            "platform": product.platform,
        },
    }


# CLI test
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        url = sys.argv[1]
        import asyncio
        from product_scraper import scrape_product
        product = asyncio.run(scrape_product(url))
        result = generate_prompts_from_url(product)
        # Print the youtube prompt for manual review
        print("=== YOUTUBE PROMPT ===")
        print(result["youtube_prompt"])
        print("\n=== SHORTS PROMPT ===")
        print(result["shorts_prompt"])
    else:
        # Demo with mock data
        mock = ProductInfo(
            url="https://example.com/tools/wrench-set",
            platform="shopify",
            title="Professional 14-Piece Ratcheting Wrench Set",
            description="Complete metric and SAE wrench set with 72-tooth ratcheting mechanism. Perfect for automotive repair, garage DIY projects, and professional mechanics. Chrome vanadium steel with corrosion-resistant finish.",
            price="$49.99",
            features=[
                "72-tooth ratcheting mechanism for tight spaces",
                "Chrome vanadium steel construction",
                "Includes both metric (8-19mm) and SAE (1/4-7/8) sizes",
                "180-degree flexible head",
                "Lifetime warranty",
            ],
            category_hints=["Tools", "Hand Tools", "Wrenches"],
            image_urls=["https://example.com/wrench-main.jpg"],
        )
        result = generate_prompts_from_url(mock)
        print("=== YOUTUBE PROMPT ===")
        print(result["youtube_prompt"])
        print("\n=== AUDIENCE ===")
        print(result["target_audience"])
