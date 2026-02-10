#!/usr/bin/env python3
"""Screenshot utility for CLI terminal output.

This script captures terminal/CLI output and renders it as an image.
It can run a command and capture its output, or read from a file.

Usage:
    # Capture output of a command
    python scripts/screenshot_cli.py --command "chad --help"

    # Capture from a file containing terminal output
    python scripts/screenshot_cli.py --input /path/to/output.txt

    # Specify output path
    python scripts/screenshot_cli.py --command "chad --help" --output /tmp/cli.png

    # Set terminal width
    python scripts/screenshot_cli.py --command "chad --help" --width 120

Examples:
    # Screenshot the CLI settings menu
    python scripts/screenshot_cli.py --input cli_menu.txt --output cli_settings.png

    # Screenshot command output with custom width
    python scripts/screenshot_cli.py --command "ls -la" --width 80 --output listing.png
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def capture_command_output(command: str, width: int = 100) -> str:
    """Run a command and capture its output."""
    # Use script command to capture with ANSI codes preserved
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        env={**subprocess.os.environ, "COLUMNS": str(width), "TERM": "xterm-256color"},
    )
    return result.stdout + result.stderr


def render_text_to_html(text: str, width: int = 100, title: str = "CLI Output") -> str:
    """Render terminal text to HTML using rich."""
    from io import StringIO

    from rich.console import Console

    # Create a console that writes to a string buffer
    buffer = StringIO()
    console = Console(
        file=buffer,
        width=width,
        force_terminal=True,
        record=True,
        legacy_windows=False,
    )

    # Print the text to capture ANSI codes
    console.print(text, highlight=False, soft_wrap=True)

    # Export to HTML
    html = console.export_html(
        inline_styles=True,
        code_format='<pre style="font-family: \'JetBrains Mono\', \'Menlo\', \'Monaco\', \'Courier New\', monospace; '
        'font-size: 14px; line-height: 1.4; padding: 16px; background-color: #1e1e1e; color: #d4d4d4; '
        'border-radius: 8px; white-space: pre-wrap; overflow-x: auto;">{code}</pre>',
    )

    # Wrap in full HTML document
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <style>
        body {{
            margin: 0;
            padding: 20px;
            background-color: #2d2d2d;
            display: flex;
            justify-content: center;
        }}
        .terminal {{
            max-width: {width}ch;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        }}
    </style>
</head>
<body>
    <div class="terminal">
        {html}
    </div>
</body>
</html>"""


def html_to_png(html_content: str, output_path: Path, width: int = 1200) -> bool:
    """Convert HTML to PNG using Playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Error: Playwright not installed. Run: pip install playwright && playwright install chromium")
        return False

    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
        f.write(html_content)
        html_path = f.name

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": width, "height": 800})
            page.goto(f"file://{html_path}")

            # Wait for content to render
            page.wait_for_load_state("networkidle")

            # Get the actual content size
            content_box = page.locator("body").bounding_box()
            if content_box:
                # Add some padding
                page.set_viewport_size(
                    {"width": int(content_box["width"]) + 40, "height": int(content_box["height"]) + 40}
                )

            # Take screenshot
            page.screenshot(path=str(output_path), full_page=True)
            browser.close()
        return True
    finally:
        Path(html_path).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Capture CLI output as screenshot")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--command", "-c", help="Command to run and capture")
    group.add_argument("--input", "-i", type=Path, help="Input file with terminal output")

    parser.add_argument("--output", "-o", type=Path, help="Output PNG path")
    parser.add_argument("--width", "-w", type=int, default=100, help="Terminal width in columns (default: 100)")
    parser.add_argument("--title", "-t", default="CLI Output", help="Title for the screenshot")
    parser.add_argument("--open", action="store_true", help="Open the screenshot after capturing")

    args = parser.parse_args()

    # Get the text to render
    if args.command:
        print(f"Running: {args.command}")
        text = capture_command_output(args.command, args.width)
    else:
        if not args.input.exists():
            print(f"Error: Input file not found: {args.input}")
            return 1
        text = args.input.read_text()

    if not text.strip():
        print("Error: No output to capture")
        return 1

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        output_path = Path("/tmp/chad/cli_screenshot.png")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Render to HTML then to PNG
    print(f"Rendering {len(text)} characters...")
    html = render_text_to_html(text, args.width, args.title)

    print(f"Converting to PNG: {output_path}")
    if html_to_png(html, output_path, width=args.width * 10):
        print(f"✓ Screenshot saved: {output_path}")

        if args.open:
            import webbrowser

            webbrowser.open(f"file://{output_path.absolute()}")

        return 0
    else:
        print("✗ Failed to create screenshot")
        return 1


if __name__ == "__main__":
    sys.exit(main())
