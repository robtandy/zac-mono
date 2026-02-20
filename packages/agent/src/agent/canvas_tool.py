from __future__ import annotations

import base64
from typing import Any

from .tools import Tool, ToolDefinition, ToolResult


class CanvasTool(Tool):
    """Long-lived browser canvas the agent can render to, navigate, execute JS in, and screenshot."""

    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None
        self._page: Any = None

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="canvas",
            description=(
                "Control a persistent browser canvas for interacting with web content. "
                "Use this to:"
                "\n- Load a webpage (navigate), then screenshot it to see the content."
                "\n- Execute JavaScript in the page to extract data or manipulate elements."
                "\n- Render custom HTML/Lit interfaces (e.g., graphs, forms, or visualizations) to show to the user."
                "\n- Dismiss the canvas when done to free resources."
                "\n\n"
                "Example workflow:"
                "\n1. `navigate` to a URL (e.g., a documentation page)."
                "\n2. `screenshot` to capture the page for analysis."
                "\n3. `execute_js` to extract data or interact with the page."
                "\n4. `render` custom HTML (e.g., a graph using Chart.js or a Lit component)."
                "\n5. `dismiss` the canvas when finished."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["render", "navigate", "execute_js", "screenshot", "dismiss"],
                    },
                    "html": {"type": "string", "description": "HTML to render (for render action)"},
                    "url": {"type": "string", "description": "URL to navigate to (for navigate action)"},
                    "js": {"type": "string", "description": "JavaScript to execute (for execute_js action)"},
                },
                "required": ["action"],
            },
        )

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        action = args.get("action")
        match action:
            case "render":
                html = args.get("html", "")
                try:
                    await self._ensure_page()
                except Exception as e:
                    return ToolResult(output=f"Failed to start browser: {e}", is_error=True)
                await self._page.set_content(html, wait_until="networkidle")
                return ToolResult(output="Canvas updated with HTML content.")
            case "navigate":
                url = args.get("url", "")
                if not url:
                    return ToolResult(output="No url provided.", is_error=True)
                try:
                    await self._ensure_page()
                except Exception as e:
                    return ToolResult(output=f"Failed to start browser: {e}", is_error=True)
                await self._page.goto(url, wait_until="networkidle")
                return ToolResult(output=f"Canvas navigated to {url}")
            case "execute_js":
                js = args.get("js", "")
                if self._page is None:
                    return ToolResult(output="No canvas open.", is_error=True)
                result = await self._page.evaluate(js)
                return ToolResult(output=str(result) if result is not None else "undefined")
            case "screenshot":
                if self._page is None:
                    return ToolResult(output="No canvas open.", is_error=True)
                data = await self._page.screenshot(type="png")
                return ToolResult(output=base64.b64encode(data).decode())
            case "dismiss":
                await self._close()
                return ToolResult(output="Canvas dismissed.")
            case _:
                return ToolResult(output=f"Unknown canvas action: {action}", is_error=True)

    async def _ensure_page(self) -> None:
        """Lazy-init Playwright browser and create page if needed."""
        if self._browser is None:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch()
        if self._page is None:
            self._page = await self._browser.new_page(
                viewport={"width": 1280, "height": 720}
            )

    async def _close(self) -> None:
        """Close page (keep browser for potential reuse)."""
        if self._page:
            await self._page.close()
            self._page = None

    async def cleanup(self) -> None:
        """Full cleanup -- call on agent shutdown."""
        await self._close()
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
