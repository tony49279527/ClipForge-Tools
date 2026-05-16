"""
Product scraper: extracts product info from e-commerce URLs.
Supports Amazon, Shopify, WooCommerce, and generic product pages.
Uses Playwright for JavaScript-heavy sites, falls back to HTTP for simple ones.
"""
import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


@dataclass
class ProductInfo:
    url: str
    platform: str = "unknown"  # amazon, shopify, woocommerce, generic
    title: str = ""
    description: str = ""
    price: str = ""
    features: list = field(default_factory=list)
    image_urls: list = field(default_factory=list)
    category_hints: list = field(default_factory=list)  # breadcrumb / category clues
    error: str = ""

    def to_prompt_context(self) -> str:
        """Convert scraped data into a context block for AI prompt generation."""
        parts = []
        if self.title:
            parts.append(f"Product: {self.title}")
        if self.description:
            # Truncate to avoid token waste
            desc = self.description[:800]
            parts.append(f"Description: {desc}")
        if self.features:
            parts.append(f"Key features: {'; '.join(self.features[:8])}")
        if self.category_hints:
            parts.append(f"Category: {' > '.join(self.category_hints)}")
        if self.price:
            parts.append(f"Price: {self.price}")
        return "\n".join(parts)


def detect_platform(url: str) -> str:
    domain = urlparse(url).netloc.lower()
    if "amazon" in domain:
        return "amazon"
    if "myshopify" in domain or "shopify" in domain:
        return "shopify"
    if "/wp-content/" in url or "woocommerce" in domain:
        return "woocommerce"
    if "etsy" in domain:
        return "etsy"
    if "ebay" in domain:
        return "ebay"
    if "aliexpress" in domain or "alibaba" in domain:
        return "aliexpress"
    return "generic"


async def scrape_with_playwright(url: str) -> ProductInfo:
    """Full browser-based scrape for JS-heavy sites."""
    from playwright.async_api import async_playwright

    info = ProductInfo(url=url, platform=detect_platform(url))

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            page = await context.new_page()

            # Block unnecessary resources for speed
            async def block_unnecessary(route):
                if route.request.resource_type in {"font", "media", "stylesheet", "image"}:
                    await route.abort()
                else:
                    await route.continue_()
            await page.route("**/*", block_unnecessary)

            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)  # let dynamic content render

            # --- Title ---
            title_selectors = [
                "h1", "[data-test='product-title']", ".product-title",
                "#productTitle", ".product-name", ".product_title",
                "[itemprop='name']", ".product-single__title",
            ]
            for sel in title_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        text = (await el.inner_text()).strip()
                        if len(text) > 3:
                            info.title = text
                            break
                except Exception:
                    pass

            # --- Description ---
            desc_selectors = [
                "#productDescription", ".product-description",
                "[data-test='product-description']", ".description",
                "[itemprop='description']", "#description",
                ".product-single__description", ".product__description",
            ]
            for sel in desc_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        text = (await el.inner_text()).strip()
                        if len(text) > 20:
                            info.description = text[:1500]
                            break
                except Exception:
                    pass

            # Fallback: meta description
            if not info.description:
                try:
                    meta = await page.query_selector("meta[name='description']")
                    if meta:
                        content = await meta.get_attribute("content")
                        if content:
                            info.description = content.strip()[:1500]
                except Exception:
                    pass

            # Last resort: grab page title + visible text summary
            if not info.title:
                try:
                    info.title = (await page.title()).strip()
                except Exception:
                    pass
            if not info.description:
                try:
                    body_text = await page.inner_text("body")
                    # Take first meaningful chunk
                    lines = [l.strip() for l in body_text.split("\n") if len(l.strip()) > 20]
                    info.description = " ".join(lines[:10])[:1500]
                except Exception:
                    pass

            # --- Features / bullet points ---
            feature_selectors = [
                "#feature-bullets li", ".product-features li",
                "[data-test='product-features'] li", ".key-features li",
                ".product-attributes li", ".specs li",
            ]
            for sel in feature_selectors:
                try:
                    els = await page.query_selector_all(sel)
                    for el in els[:10]:
                        text = (await el.inner_text()).strip()
                        if text and len(text) > 2:
                            info.features.append(text)
                    if info.features:
                        break
                except Exception:
                    pass

            # --- Price ---
            price_selectors = [
                "[data-test='product-price']", ".price", "#price",
                ".product-price", "[itemprop='price']", ".price-sales",
                "#priceblock_ourprice", ".a-price .a-offscreen",
            ]
            for sel in price_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        text = (await el.inner_text()).strip()
                        if text and re.search(r'[\d]', text):
                            info.price = text
                            break
                except Exception:
                    pass

            # --- Image URLs ---
            try:
                imgs = await page.query_selector_all("img")
                found = 0
                for img in imgs:
                    src = await img.get_attribute("src")
                    alt = await img.get_attribute("alt") or ""
                    if not src:
                        continue
                    # Filter out tiny icons, logos
                    if any(x in src.lower() for x in ["icon", "logo", "avatar", "banner", "thumb"]):
                        continue
                    # Prefer images with product-related alt text
                    if info.title and any(w in alt.lower() for w in info.title.lower().split()[:2]):
                        info.image_urls.append(src)
                        found += 1
                    elif found < 3 and ("product" in alt.lower() or "photo" in alt.lower()):
                        info.image_urls.append(src)
                        found += 1
                # If not enough, grab any reasonably-named images
                if len(info.image_urls) < 2:
                    for img in imgs:
                        src = await img.get_attribute("src") or ""
                        if src and src not in info.image_urls:
                            if any(ext in src.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                                info.image_urls.append(src)
                            if len(info.image_urls) >= 5:
                                break
            except Exception:
                pass

            # --- Category / Breadcrumb ---
            try:
                crumbs = await page.query_selector_all("[aria-label='Breadcrumb'] a, .breadcrumb a, .breadcrumbs a")
                for crumb in crumbs[:5]:
                    text = (await crumb.inner_text()).strip()
                    if text and len(text) > 1:
                        info.category_hints.append(text)
            except Exception:
                pass

            await browser.close()

    except Exception as e:
        info.error = str(e)

    return info


async def scrape_product(url: str) -> ProductInfo:
    """Main entry: scrape a product URL and return structured info."""
    return await scrape_with_playwright(url)


# CLI test
if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.amazon.com/dp/B0TEST12345"
    result = asyncio.run(scrape_product(url))
    print(json.dumps({
        "url": result.url,
        "platform": result.platform,
        "title": result.title,
        "description": result.description[:200] if result.description else "",
        "price": result.price,
        "features": result.features[:5],
        "images": result.image_urls[:3],
        "categories": result.category_hints,
        "error": result.error,
    }, indent=2, ensure_ascii=False))
