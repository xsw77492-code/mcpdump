"""Render MCP data structures as something a person can read.

Information density beats decoration: the point is to show schemas and errors
clearly.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from ..core.jsonrpc import JsonRpcError
from ..core.session import ServerInfo
from ..i18n import t


def styled_lines(lines: Sequence[tuple[str, str]]) -> Text:
    """Join ``(text, style)`` pairs into one ``Text``, bypassing markup parsing.

    Required for any output carrying external data (tool names, strings from the
    server): ``[`` and ``]`` are common there and markup would read them as tags.
    """
    return Text("\n").join(Text(text, style=style) for text, style in lines)


def styled_line(*parts: tuple[str, str]) -> Text:
    """Join ``(text, style)`` pairs into a single line of ``Text``.

    Same as ``styled_lines`` but joined with nothing instead of a newline, for
    one line that mixes styles and external data (``--trace`` echoing a frame).
    """
    text = Text()
    for content, style in parts:
        text.append(content, style=style)
    return text


def format_duration(ms: float) -> str:
    """Write a millisecond count in the unit a person can read at a glance.

    Each step rounds onto the grid of the digit it displays before deciding
    whether to carry: otherwise 59.999 s renders as ``60.00 s``.
    """
    total = max(ms, 0.0)

    tenths = round(total * 10)  # 0.1 ms grid
    if tenths < 10_000:
        return f"{tenths / 10:.1f} ms"

    hundredths = round(total / 10)  # 0.01 s grid
    if hundredths < 6_000:
        return f"{hundredths / 100:.2f} s"

    minutes, rest = divmod(round(total / 100), 600)  # 0.1 s grid
    if minutes < 60:
        return f"{minutes}m {rest / 10:.1f}s"

    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def render_json(console: Console, payload: Any, *, title: str | None = None) -> None:
    """Print any object with JSON syntax highlighting."""
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    syntax = Syntax(text, "json", theme="ansi_dark", word_wrap=True, background_color="default")
    if title:
        console.print(Panel(syntax, title=title, border_style="mcpdump.dim", expand=False))
    else:
        console.print(syntax)


def render_server_header(console: Console, server: ServerInfo, transport_desc: str) -> None:
    title = Text()
    title.append(server.name or t("render.unnamed"), style="mcpdump.brand")
    if server.version:
        title.append(f" v{server.version}", style="mcpdump.dim")
    if server.title:
        title.append(f"  ({server.title})", style="mcpdump.dim")

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="mcpdump.meta", justify="right")
    grid.add_column()
    grid.add_row(t("render.label.transport"), transport_desc)
    grid.add_row(
        t("render.label.protocol_version"),
        server.protocol_version or t("render.undeclared"),
    )
    grid.add_row(t("render.label.capabilities"), render_capabilities(server) or t("render.none"))
    if server.instructions:
        grid.add_row(
            t("render.label.instructions"),
            escape(server.instructions.strip().splitlines()[0][:100]),
        )

    console.print(Panel(grid, title=title, border_style="mcpdump.brand", expand=False))


def render_capabilities(server: ServerInfo) -> str:
    """Join the server's capabilities into one line. Labels and order come from ``ServerInfo``."""
    return " · ".join(server.describe_capabilities())


def _schema_hint(schema: dict[str, Any] | None) -> str:
    """Compress an inputSchema into a one-line argument hint."""
    if not schema:
        return ""
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    parts: list[str] = []
    for name, spec in props.items():
        kind = spec.get("type", "any") if isinstance(spec, dict) else "any"
        marker = "" if name in required else "?"
        parts.append(f"{name}{marker}: {kind}")
    if not parts:
        return ""
    return "(" + ", ".join(parts) + ")"


def render_tools(console: Console, tools: list[dict[str, Any]], *, verbose: bool = False) -> None:
    if not tools:
        console.print(Text(t("render.no_tools"), style="mcpdump.dim"))
        return

    table = Table(
        title=t("render.tools_title", count=len(tools)),
        title_justify="left",
        border_style="mcpdump.dim",
        header_style="mcpdump.label",
        show_lines=verbose,
        expand=True,
    )
    table.add_column(t("render.col.name"), style="mcpdump.brand", no_wrap=True)
    table.add_column(
        t("render.col.args"), style="mcpdump.type", no_wrap=not verbose, overflow="fold"
    )
    table.add_column(t("render.col.description"), overflow="fold")

    for tool in tools:
        table.add_row(
            tool.get("name", t("render.unnamed")),
            _schema_hint(tool.get("inputSchema")) or "—",
            (tool.get("description") or "—").strip().replace("\n", " "),
        )
    console.print(table)


def render_resources(console: Console, resources: list[dict[str, Any]]) -> None:
    if not resources:
        return
    table = Table(
        title=t("render.resources_title", count=len(resources)),
        title_justify="left",
        border_style="mcpdump.dim",
        header_style="mcpdump.label",
        expand=True,
    )
    table.add_column(t("render.col.uri"), style="mcpdump.brand", overflow="fold")
    table.add_column(t("render.col.mime"), style="mcpdump.type", no_wrap=True)
    table.add_column(t("render.col.description"), overflow="fold")
    for res in resources:
        table.add_row(
            res.get("uri", "—"),
            res.get("mimeType") or "—",
            (res.get("description") or res.get("name") or "—").strip().replace("\n", " "),
        )
    console.print(table)


def render_prompts(console: Console, prompts: list[dict[str, Any]]) -> None:
    if not prompts:
        return
    table = Table(
        title=t("render.prompts_title", count=len(prompts)),
        title_justify="left",
        border_style="mcpdump.dim",
        header_style="mcpdump.label",
        expand=True,
    )
    table.add_column(t("render.col.name"), style="mcpdump.brand", no_wrap=True)
    table.add_column(t("render.col.args"), style="mcpdump.type", overflow="fold")
    table.add_column(t("render.col.description"), overflow="fold")
    for prompt in prompts:
        args = prompt.get("arguments") or []
        hint = "(" + ", ".join(
            f"{a.get('name', '?')}{'' if a.get('required') else '?'}: str" for a in args
        ) + ")" if args else "—"
        table.add_row(
            prompt.get("name", t("render.unnamed")),
            hint,
            (prompt.get("description") or "—").strip().replace("\n", " "),
        )
    console.print(table)


def render_tool_result(console: Console, result: dict[str, Any]) -> None:
    """Render a tools/call result: content array, structuredContent, isError."""
    is_error = bool(result.get("isError"))
    style = "mcpdump.err" if is_error else "mcpdump.res"
    label = t("render.tool_error") if is_error else t("render.tool_result")

    blocks: list[Any] = []
    for item in result.get("content") or []:
        kind = item.get("type")
        if kind == "text":
            blocks.append(Text(item.get("text", "")))
        elif kind == "image":
            blocks.append(Text(
                t(
                    "render.image_block",
                    mime=item.get("mimeType", "?"),
                    size=len(item.get("data", "")),
                ),
                style="mcpdump.dim",
            ))
        elif kind == "resource":
            res = item.get("resource") or {}
            blocks.append(Text(
                t("render.resource_block", uri=res.get("uri", "?")), style="mcpdump.dim"
            ))
        else:
            blocks.append(Text(json.dumps(item, ensure_ascii=False), style="mcpdump.dim"))

    if not blocks:
        blocks.append(Text(t("render.empty_content"), style="mcpdump.dim"))

    console.print(Panel(
        Text("\n").join(blocks) if len(blocks) > 1 else blocks[0],
        title=label,
        border_style=style,
        expand=False,
    ))

    if "structuredContent" in result:
        render_json(console, result["structuredContent"], title="structuredContent")


def render_next_steps(console: Console, lines: list[str], *, title: str | None = None) -> None:
    """The third block: commands the user can copy straight from the output.

    ``title`` is resolved inside the body rather than as a parameter default,
    since defaults are evaluated at import time and would pin the language.
    """
    if not lines:
        return
    body = Text()
    body.append(f"{title or t('render.next_steps')}\n", style="mcpdump.label")
    body.append("\n".join(lines), style="mcpdump.dim")
    console.print()
    console.print(body)


def render_error(console: Console, exc: Exception) -> None:
    if isinstance(exc, JsonRpcError):
        body = Text()
        body.append(f"code: {exc.code}\n", style="mcpdump.err")
        body.append(f"message: {exc.message}\n")
        if exc.data is not None:
            body.append("data: " + json.dumps(exc.data, ensure_ascii=False), style="mcpdump.dim")
        console.print(Panel(
            body, title=t("render.jsonrpc_error_title"), border_style="mcpdump.err", expand=False
        ))
        return
    console.print(
        Panel(Text(str(exc)), title=type(exc).__name__, border_style="mcpdump.err", expand=False)
    )
