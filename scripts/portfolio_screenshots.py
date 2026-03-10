#!/usr/bin/env python3
"""Portfolio screenshot automation using Playwright.

Takes desktop (1440x900 @2x) and mobile (iPhone 14 Pro) screenshots
of HTML files and generates device-frame mockups.

Usage::

    # Single HTML file
    python scripts/portfolio_screenshots.py --html portfolio/projects/001/index.html

    # Batch: all projects
    python scripts/portfolio_screenshots.py --batch

    # Mockup only (screenshots already exist)
    python scripts/portfolio_screenshots.py --mockup portfolio/projects/001/screenshots/
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

PORTFOLIO_DIR = _ROOT / "portfolio" / "projects"
TEMPLATE_PATH = _ROOT / "portfolio" / "templates" / "device_mockup.html"

# Device configurations
DESKTOP_VIEWPORT = {"width": 1440, "height": 900}
MOBILE_VIEWPORT = {"width": 393, "height": 852}  # iPhone 14 Pro
DEVICE_SCALE_FACTOR = 2  # @2x for Retina


async def take_screenshots(
    html_path: Path,
    output_dir: Path,
) -> dict[str, Path]:
    """Take desktop and mobile screenshots of an HTML file.

    Args:
        html_path: Path to the HTML file.
        output_dir: Directory to save screenshots.

    Returns:
        Dict with 'desktop' and 'mobile' paths.
    """
    try:
        from playwright.async_api import async_playwright  # noqa: PLC0415
    except ImportError:
        print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    file_url = html_path.resolve().as_uri()
    results: dict[str, Path] = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # Desktop screenshot
        desktop_ctx = await browser.new_context(
            viewport=DESKTOP_VIEWPORT,
            device_scale_factor=DEVICE_SCALE_FACTOR,
        )
        desktop_page = await desktop_ctx.new_page()
        await desktop_page.goto(file_url, wait_until="networkidle")
        desktop_path = output_dir / "desktop.png"
        await desktop_page.screenshot(path=str(desktop_path), full_page=False)
        results["desktop"] = desktop_path
        await desktop_ctx.close()
        print(f"  Desktop: {desktop_path}")

        # Mobile screenshot
        mobile_ctx = await browser.new_context(
            viewport=MOBILE_VIEWPORT,
            device_scale_factor=DEVICE_SCALE_FACTOR,
            is_mobile=True,
        )
        mobile_page = await mobile_ctx.new_page()
        await mobile_page.goto(file_url, wait_until="networkidle")
        mobile_path = output_dir / "mobile.png"
        await mobile_page.screenshot(path=str(mobile_path), full_page=False)
        results["mobile"] = mobile_path
        await mobile_ctx.close()
        print(f"  Mobile:  {mobile_path}")

        await browser.close()

    return results


async def create_device_mockup(
    screenshot_dir: Path,
    output_path: Path | None = None,
) -> Path:
    """Render a device-frame mockup using the CSS template.

    Takes desktop.png and mobile.png from screenshot_dir, embeds them
    in the MacBook + iPhone frame template, screenshots the result.

    Args:
        screenshot_dir: Directory containing desktop.png and mobile.png.
        output_path: Path for the mockup output. Defaults to screenshot_dir/mockup.png.

    Returns:
        Path to the generated mockup PNG.
    """
    try:
        from playwright.async_api import async_playwright  # noqa: PLC0415
    except ImportError:
        print("ERROR: playwright not installed.")
        sys.exit(1)

    desktop_png = screenshot_dir / "desktop.png"
    mobile_png = screenshot_dir / "mobile.png"

    if not desktop_png.exists() or not mobile_png.exists():
        raise FileNotFoundError(f"Missing screenshots in {screenshot_dir}")

    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Missing template: {TEMPLATE_PATH}")

    # Build HTML with embedded screenshots
    template_html = TEMPLATE_PATH.read_text(encoding="utf-8")
    template_html = template_html.replace(
        "{{DESKTOP_SCREENSHOT}}", desktop_png.resolve().as_uri()
    )
    template_html = template_html.replace(
        "{{MOBILE_SCREENSHOT}}", mobile_png.resolve().as_uri()
    )

    # Write temporary HTML
    tmp_html = screenshot_dir / "_mockup_temp.html"
    tmp_html.write_text(template_html, encoding="utf-8")

    mockup_path = output_path or (screenshot_dir / "mockup.png")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1200, "height": 900},
            device_scale_factor=2,
        )
        await page.goto(tmp_html.resolve().as_uri(), wait_until="networkidle")
        await page.screenshot(path=str(mockup_path), full_page=False)
        await browser.close()

    # Clean up temp file
    tmp_html.unlink(missing_ok=True)

    print(f"  Mockup:  {mockup_path}")
    return mockup_path


async def process_single(html_path: Path) -> None:
    """Process a single HTML file: screenshots + mockup."""
    output_dir = html_path.parent / "screenshots"
    print(f"\n[SCREENSHOTS] {html_path.name}")

    await take_screenshots(html_path, output_dir)
    await create_device_mockup(output_dir)


async def batch_process() -> None:
    """Process all HTML files in portfolio/projects/*/."""
    if not PORTFOLIO_DIR.exists():
        print(f"No portfolio directory found at {PORTFOLIO_DIR}")
        return

    html_files = sorted(PORTFOLIO_DIR.glob("*/index.html"))
    if not html_files:
        print(f"No index.html files found in {PORTFOLIO_DIR}/*/")
        return

    print(f"Found {len(html_files)} projects to process")

    for html_file in html_files:
        await process_single(html_file)

    print(f"\nDone! Processed {len(html_files)} projects.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Portfolio screenshot automation")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--html", type=str, help="Path to a single HTML file")
    group.add_argument("--batch", action="store_true", help="Process all portfolio projects")
    group.add_argument("--mockup", type=str, help="Path to screenshots dir (mockup only)")
    args = parser.parse_args()

    if args.html:
        html_path = Path(args.html)
        if not html_path.exists():
            print(f"File not found: {html_path}")
            sys.exit(1)
        await process_single(html_path)

    elif args.batch:
        await batch_process()

    elif args.mockup:
        mockup_dir = Path(args.mockup)
        if not mockup_dir.exists():
            print(f"Directory not found: {mockup_dir}")
            sys.exit(1)
        print(f"\n[MOCKUP] {mockup_dir}")
        await create_device_mockup(mockup_dir)


if __name__ == "__main__":
    asyncio.run(main())
