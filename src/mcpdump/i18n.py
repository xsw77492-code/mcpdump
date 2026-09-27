"""The user-facing message catalog.

English is the default language -- stars come mostly from the English-speaking
community. Chinese is enabled by ``MCPDUMP_LANG=zh`` or a Chinese locale.

**At the top level rather than under ``ui/``**: ``core/`` needs it too, and ``core``
may not depend on ``ui``.

**Both languages are written side by side for each message** -- a missing translation
is visible at a glance, with no cross-referencing. Better suited to a project this size
than ``.po`` or JSON files: no build step, and changing wording is changing code.

**Programmer-facing exception messages are not translated** (such as the
``AttributeError`` in ``symbols.py``). They appear in tracebacks, where English is the
Python ecosystem convention and easier to search for and report.

**Known limitation**: Typer reads ``help=`` when the decorator is evaluated, so
command-line help is fixed at import time. ``MCPDUMP_LANG`` must be set before the
process starts -- which is its normal usage anyway.
"""

from __future__ import annotations

import os

__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGES",
    "MESSAGES",
    "capability_label",
    "current_language",
    "set_language",
    "t",
]

DEFAULT_LANGUAGE = "en"
LANGUAGES = ("en", "zh")

#: The message catalog. Placeholders use ``str.format``; literal braces are ``{{`` ``}}``.
#: A test walks the whole table to check both languages exist and format cleanly.
MESSAGES: dict[str, dict[str, str]] = {
    # ---------- application and command metadata ----------
    "app.about": {
        "en": "Terminal toolkit for MCP servers: inspect capabilities, call tools, "
              "watch frames, run conformance checks.",
        "zh": "MCP Server 的终端工具链：检查能力、调用工具、观察报文、跑一致性检查。",
    },
    "cmd.ls": {
        "en": "List the tools, resources and prompts a server exposes.",
        "zh": "列出服务端暴露的工具、资源与提示词模板。",
    },
    "cmd.call": {
        "en": "Call a tool and print the complete JSON-RPC round trip.",
        "zh": "调用某个工具，并打印完整的 JSON-RPC 往返报文。",
    },
    # ---------- arguments and options ----------
    "arg.server": {
        "en": "How to start the MCP server (e.g. "
              "'npx -y @modelcontextprotocol/server-filesystem /tmp'), or an http(s):// URL.",
        "zh": "MCP Server 的启动命令（如 "
              "'npx -y @modelcontextprotocol/server-filesystem /tmp'），"
              "或 http(s):// 形式的远程地址。",
    },
    "arg.tool": {
        "en": "Name of the tool to call.",
        "zh": "要调用的工具名。",
    },
    "opt.version": {"en": "Show the version and exit.", "zh": "显示版本号后退出。"},
    "opt.protocol_version": {
        "en": "MCP protocol version to prefer during the handshake.",
        "zh": "握手时优先声明的 MCP 协议版本。",
    },
    "opt.timeout": {"en": "Seconds to wait for a response.", "zh": "等待响应的秒数。"},
    "opt.quiet_server": {
        "en": "Do not relay the server's stderr output.",
        "zh": "不透传服务端的 stderr 输出。",
    },
    "opt.trace": {
        "en": "Mirror every frame sent and received to stderr.",
        "zh": "把每一次收发的报文打到 stderr。",
    },
    "opt.verbose": {"en": "Expand parameter schema details.", "zh": "展开显示参数 schema 细节。"},
    "opt.json": {
        "en": "Emit JSON for scripts. Schema is stable.",
        "zh": "以 JSON 输出，便于脚本消费。schema 稳定。",
    },
    "opt.args": {
        "en": "Tool arguments as a JSON object, e.g. '{{\"city\":\"Beijing\"}}'.",
        "zh": "工具参数，JSON 对象，例如 '{{\"city\":\"北京\"}}'。",
    },
    "opt.no_wire": {
        "en": "Show only the result, without the raw frames.",
        "zh": "只显示结果，不显示原始报文。",
    },
    "opt.full": {
        "en": "Print frames in full instead of truncating to the terminal width.",
        "zh": "报文不截断，完整打印。",
    },
    "err.interrupted": {"en": "Interrupted.", "zh": "已中断。"},
    # ---------- capability names ----------
    "capability.tools": {"en": "tools", "zh": "工具"},
    "capability.resources": {"en": "resources", "zh": "资源"},
    "capability.prompts": {"en": "prompts", "zh": "提示词模板"},
    "capability.logging": {"en": "logging", "zh": "日志"},
    "capability.completions": {"en": "completions", "zh": "补全"},
    "capability.experimental": {"en": "experimental", "zh": "实验特性"},
    # ---------- server info panel ----------
    "render.label.transport": {"en": "transport", "zh": "传输"},
    "render.label.protocol_version": {"en": "protocol", "zh": "协议版本"},
    "render.label.capabilities": {"en": "capabilities", "zh": "能力"},
    "render.label.instructions": {"en": "instructions", "zh": "说明"},
    "render.unnamed": {"en": "<unnamed>", "zh": "<未命名>"},
    "render.undeclared": {"en": "<not declared>", "zh": "<未声明>"},
    "render.none": {"en": "<none>", "zh": "<无>"},
    # ---------- tables ----------
    "render.col.name": {"en": "name", "zh": "名称"},
    "render.col.args": {"en": "arguments", "zh": "参数"},
    "render.col.description": {"en": "description", "zh": "描述"},
    "render.col.uri": {"en": "URI", "zh": "URI"},
    "render.col.mime": {"en": "MIME", "zh": "MIME"},
    "render.tools_title": {"en": "Tools ({count})", "zh": "工具（{count}）"},
    "render.resources_title": {"en": "Resources ({count})", "zh": "资源（{count}）"},
    "render.prompts_title": {"en": "Prompts ({count})", "zh": "提示词模板（{count}）"},
    "render.no_tools": {
        "en": "This server exposes no tools.",
        "zh": "该服务端未暴露任何工具。",
    },
    "render.no_capabilities": {
        "en": "The handshake succeeded, but the server exposes no tools, resources or prompts.",
        "zh": "服务端握手成功，但没有暴露任何工具、资源或提示词模板。",
    },
    # ---------- tool results ----------
    "render.tool_result": {"en": "Tool result", "zh": "工具返回"},
    "render.tool_error": {"en": "Tool error", "zh": "工具返回错误"},
    "render.empty_content": {"en": "<content is empty>", "zh": "<content 为空>"},
    "render.image_block": {
        "en": "<image {mime} · base64 length {size}>",
        "zh": "<图片 {mime} · base64 长度 {size}>",
    },
    "render.resource_block": {"en": "<resource {uri}>", "zh": "<资源 {uri}>"},
    "render.jsonrpc_error_title": {
        "en": "Server returned a JSON-RPC error",
        "zh": "服务端返回 JSON-RPC 错误",
    },
    # ---------- the three-part hint block ----------
    "render.next_steps": {"en": "Next", "zh": "下一步"},
    # ---------- wire view ----------
    # Durations arrive already converted by format_duration: units are a render concern.
    "wire.summary_single": {"en": "1 round trip · {total}", "zh": "1 次往返 · {total}"},
    "wire.summary_multi": {
        "en": "{count} round trips · {total} total · "
              "slowest {mark} {method} {elapsed}",
        "zh": "{count} 次往返 · 总耗时 {total} · 最慢 {mark} {method} {elapsed}",
    },
    # ---------- session ----------
    "session.timeout": {
        "en": "Timed out waiting for {method} ({timeout:.0f}s). The server may have crashed, "
              "or it may not be writing newline-delimited JSON.",
        "zh": "等待 {method} 的响应超时（{timeout:.0f}s）。"
              "服务端可能已崩溃，或未按换行分隔输出 JSON。",
    },
    # ---------- transport ----------
    "transport.empty_command": {
        "en": "The MCP server command cannot be empty.",
        "zh": "MCP Server 启动命令不能为空。",
    },
    "transport.not_started": {
        "en": "Transport has not started. Call start() first.",
        "zh": "传输尚未启动，请先调用 start()。",
    },
    "transport.executable_not_found": {
        "en": "Cannot find executable {command!r}. Make sure it is installed and on PATH.",
        "zh": "找不到可执行文件 {command!r}，请确认它已安装并在 PATH 中。",
    },
    "transport.http_status": {
        "en": "The server at {url} answered HTTP {code}. {detail}",
        "zh": "服务端 {url} 返回 HTTP {code}。{detail}",
    },
    "transport.http_unreachable": {
        "en": "Cannot reach {url}: {error}",
        "zh": "连不上 {url}：{error}",
    },
    "transport.server_gone": {
        "en": "The server closed stdout and exited with code {code}. "
              "Whatever it printed to stderr is usually the reason.",
        "zh": "服务端关闭了 stdout，退出码 {code}。原因通常就写在它的 stderr 里。",
    },
    "transport.server_gone_running": {
        "en": "The server closed stdout but is still running. "
              "It may not be writing newline-delimited JSON.",
        "zh": "服务端关闭了 stdout 但进程还在。它可能没有按换行分隔输出 JSON。",
    },
    # ---------- JSON-RPC wire layer ----------
    "jsonrpc.invalid_json": {
        "en": "Not valid JSON: {error!r}",
        "zh": "不是合法 JSON：{error!r}",
    },
    "jsonrpc.not_an_object": {
        "en": "A frame must be a JSON object, got {kind}.",
        "zh": "报文必须是 JSON 对象，实际是 {kind}。",
    },
    "jsonrpc.bad_version": {
        "en": "Missing or wrong jsonrpc version field: {value!r}",
        "zh": "缺少或错误的 jsonrpc 版本字段：{value!r}",
    },
    "jsonrpc.no_error_message": {
        "en": "The server provided no error message.",
        "zh": "服务端未提供错误信息",
    },
    # ---------- call command ----------
    "call.suggestion": {"en": "Did you mean: {names}?", "zh": "你是不是想调用：{names}？"},
    "call.unknown_tool": {
        "en": "The server has no tool named {tool!r}.",
        "zh": "服务端没有名为 {tool!r} 的工具。",
    },
    "call.available_tools": {"en": "Available tools: {names}", "zh": "可用工具：{names}"},
    "call.args_invalid": {
        "en": "--args is not valid JSON: {error}",
        "zh": "--args 不是合法 JSON：{error}",
    },
    "call.args_example": {
        "en": "Correct form: --args '{{\"city\":\"Beijing\"}}'",
        "zh": "正确写法示例：--args '{{\"city\":\"北京\"}}'",
    },
    "call.args_not_object": {
        "en": "--args must be a JSON object (starting with {{)",
        "zh": "--args 必须是 JSON 对象（以 {{ 开头）",
    },
    # ---------- list separator ----------
    "list.separator": {"en": ", ", "zh": "、"},
    # ---------- check command and report ----------
    "cmd.check": {
        "en": "Run protocol conformance checks and report every violation with evidence.",
        "zh": "跑协议一致性检查，逐条给出违规证据。",
    },
    "opt.badge": {
        "en": "Print a paste-ready Markdown badge instead of the report.",
        "zh": "输出可直接粘贴的 Markdown 徽章，不打印报告。",
    },
    "opt.markdown": {
        "en": "Print the report as Markdown, for a PR comment or a CI summary.",
        "zh": "以 Markdown 输出报告，用于 PR 注释或 CI 摘要。",
    },
    # It names the flags the user actually passed rather than a hardcoded pair: any two
    # or all three are possible, and a fixed pair would point at the wrong one.
    "check.mutually_exclusive": {
        "en": "{flags} cannot be combined.",
        "zh": "{flags} 不能同时使用。",
    },
    "check.title": {"en": "Conformance report", "zh": "一致性报告"},
    "check.summary": {
        "en": "{total} checks · {passed} passed · {failed} failed · {skipped} skipped",
        "zh": "{total} 项检查 · 通过 {passed} · 失败 {failed} · 跳过 {skipped}",
    },
    "check.warnings": {
        "en": "Warnings: {count} (non-blocking, but worth reading)",
        "zh": "警告：{count} 条（不阻塞通过，但值得看）",
    },
    "check.spec": {"en": "spec: {spec}", "zh": "规范：{spec}"},
    "check.evidence": {"en": "evidence: {evidence}", "zh": "证据：{evidence}"},
    "check.skipped_reason": {"en": "skipped — {reason}", "zh": "跳过 —— {reason}"},
    "check.verdict.passed": {
        "en": "All {total} checks passed.",
        "zh": "全部 {total} 项检查通过。",
    },
    "check.verdict.failed": {
        "en": "{failed} of {total} checks failed.",
        "zh": "{total} 项中有 {failed} 项未通过。",
    },
    "badge.label": {"en": "mcpdump conformance", "zh": "mcpdump 一致性"},
    # ---------- Markdown report (PR comment / CI summary) ----------
    "md.server": {
        "en": "**{name}** {version} · protocol `{protocol}`",
        "zh": "**{name}** {version} · 协议 `{protocol}`",
    },
    "md.failed": {"en": "Failed ({count})", "zh": "未通过（{count}）"},
    "md.passing_details": {
        "en": "Show {count} passing check(s)",
        "zh": "展开 {count} 项通过的检查",
    },
    "md.skipped": {"en": "Skipped ({count})", "zh": "已跳过（{count}）"},
    "md.footer": {
        "en": "Generated by [mcpdump]({link}) — the terminal toolkit for MCP servers.",
        "zh": "由 [mcpdump]({link}) 生成 —— MCP Server 的终端工具链。",
    },
    # ---------- check internals ----------
    "check.probe_not_open": {
        "en": "The probe connection is not open. Use it inside its context manager.",
        "zh": "探针连接未打开，请在上下文管理器内使用。",
    },
    "check.probe_timeout": {
        "en": "The server did not answer within {timeout:.0f}s.",
        "zh": "服务端在 {timeout:.0f}s 内没有响应。",
    },
    "check.finding.crashed": {
        "en": "The check itself raised an exception: {error}",
        "zh": "检查项自身抛出异常：{error}",
    },
    "check.finding.no_response": {
        "en": "The request never completed: {error}",
        "zh": "请求未能完成：{error}",
    },
    # ---------- skip reasons ----------
    "check.skip.no_initialize": {
        "en": "No initialize exchange was recorded.",
        "zh": "没有记录到 initialize 往返。",
    },
    "check.skip.no_tools": {
        "en": "The server declares no tools capability.",
        "zh": "服务端没有声明 tools 能力。",
    },
    "check.skip.no_required_tool": {
        "en": "No tool declares required parameters, "
              "so missing-argument handling cannot be probed.",
        "zh": "没有工具声明必需参数，无从探测参数缺失的处理方式。",
    },
    "check.skip.no_smoke_tool": {
        "en": "No tool can be called without arguments, so the result shape cannot be probed.",
        "zh": "没有工具能在参数为空时调用，无从探测返回值的形状。",
    },
    "check.skip.probe_name_taken": {
        "en": "A tool is already named {name!r}; the probe name is taken.",
        "zh": "已有名为 {name!r} 的工具，探针名字被占用了。",
    },
    # ---------- check 1 - handshake protocol version ----------
    "check.handshake_protocol_version.title": {
        "en": "initialize returns protocolVersion",
        "zh": "initialize 返回 protocolVersion",
    },
    "check.handshake_protocol_version.missing": {
        "en": "The initialize result carries no protocolVersion field.",
        "zh": "initialize 返回值里没有 protocolVersion 字段。",
    },
    "check.handshake_protocol_version.empty": {
        "en": "protocolVersion is present but empty.",
        "zh": "protocolVersion 存在但是空串。",
    },
    "check.handshake_protocol_version.mismatch": {
        "en": "The server answered {actual}; mcpdump offered {offered}. "
              "Negotiation is legal, just noting it.",
        "zh": "服务端回了 {actual}，mcpdump 声明的是 {offered}。版本协商本就允许，仅作记录。",
    },
    # ---------- check 2 - handshake order ----------
    "check.handshake_initialized_order.title": {
        "en": "no server request before initialized",
        "zh": "initialized 之前不发请求",
    },
    "check.handshake_initialized_order.request": {
        "en": "The server sent a {method!r} request before receiving notifications/initialized.",
        "zh": "服务端在收到 notifications/initialized 之前就发起了 {method!r} 请求。",
    },
    "check.handshake_initialized_order.notification": {
        "en": "The server sent a {method!r} notification before notifications/initialized.",
        "zh": "服务端在收到 notifications/initialized 之前就发了 {method!r} 通知。",
    },
    # ---------- check 3 - capabilities match methods ----------
    "check.capability_method_consistency.title": {
        "en": "declared capabilities are implemented",
        "zh": "声明的能力确有实现",
    },
    "check.capability_method_consistency.unimplemented": {
        "en": "The server declares {capability!r} but {method} fails: {error}",
        "zh": "服务端声明了 {capability!r}，但 {method} 调不通：{error}",
    },
    "check.capability_method_consistency.undeclared": {
        "en": "The server answers {method} without declaring {capability!r}; clients will skip it.",
        "zh": "服务端能响应 {method}，却没声明 {capability!r}，客户端会主动跳过它。",
    },
    "check.capability_method_consistency.undeclared_error": {
        "en": "The server does not declare {capability!r}, and {method} failed with {error}.",
        "zh": "服务端没有声明 {capability!r}，而 {method} 报错 {error}。",
    },
    "check.capability_method_consistency.no_response": {
        "en": "{method} never completed: {error}",
        "zh": "{method} 未能完成：{error}",
    },
    # ---------- check 4 - inputSchema shape ----------
    "check.schema_valid_json_schema.title": {
        "en": "inputSchema is a JSON Schema object",
        "zh": "inputSchema 是 JSON Schema 对象",
    },
    "check.schema_valid_json_schema.missing": {
        "en": "{tool}: the tool declares no inputSchema.",
        "zh": "{tool}：工具没有声明 inputSchema。",
    },
    "check.schema_valid_json_schema.not_object": {
        "en": "{tool}: inputSchema is {kind}, expected an object.",
        "zh": "{tool}：inputSchema 是 {kind}，应为对象。",
    },
    "check.schema_valid_json_schema.missing_type": {
        "en": "{tool}: inputSchema does not declare type: object.",
        "zh": "{tool}：inputSchema 没有声明 type: object。",
    },
    "check.schema_valid_json_schema.bad_type": {
        "en": "{tool}: inputSchema.type is {value!r}; MCP requires 'object'.",
        "zh": "{tool}：inputSchema.type 是 {value!r}，MCP 要求是 'object'。",
    },
    "check.schema_valid_json_schema.bad_properties": {
        "en": "{tool}: properties must map names to schema objects.",
        "zh": "{tool}：properties 必须是「字段名到 schema 对象」的映射。",
    },
    "check.schema_valid_json_schema.bad_required": {
        "en": "{tool}: required must be an array of strings.",
        "zh": "{tool}：required 必须是字符串数组。",
    },
    # ---------- check 5 - required matches properties ----------
    "check.schema_required_exists.title": {
        "en": "required names exist in properties",
        "zh": "required 的字段都在 properties 里",
    },
    "check.schema_required_exists.missing": {
        "en": "{tool}: required names {names} are not declared in properties.",
        "zh": "{tool}：required 里的 {names} 没有在 properties 中声明。",
    },
    # ---------- check 6 - unknown tool error code ----------
    "check.error_unknown_tool_code.title": {
        "en": "unknown tool returns -32602",
        "zh": "未知工具返回 -32602",
    },
    "check.error_unknown_tool_code.wrong_code": {
        "en": "Unknown tool returned {code}; the spec requires {expected} (Invalid params).",
        "zh": "未知工具返回 {code}，规范要求 {expected}（Invalid params）。",
    },
    "check.error_unknown_tool_code.succeeded": {
        "en": "Calling the nonexistent tool {tool!r} succeeded instead of failing.",
        "zh": "调用不存在的工具 {tool!r} 竟然成功了。",
    },
    # ---------- check 7 - invalid argument error code ----------
    "check.error_invalid_params_code.title": {
        "en": "missing arguments return -32602",
        "zh": "参数缺失返回 -32602",
    },
    "check.error_invalid_params_code.wrong_code": {
        "en": "{tool} returned {code} for missing arguments; the spec requires {expected}.",
        "zh": "{tool} 在参数缺失时返回 {code}，规范要求 {expected}。",
    },
    "check.error_invalid_params_code.is_error_flag": {
        "en": "{tool} reports missing arguments via isError instead of -32602; "
              "clients cannot tell it apart from a tool failure.",
        "zh": "{tool} 用 isError 而不是 -32602 报告参数缺失，客户端分不清它和工具执行失败。",
    },
    "check.error_invalid_params_code.succeeded": {
        "en": "{tool} ran successfully without its required arguments; "
              "the caller thinks its input took effect.",
        "zh": "{tool} 在缺少必需参数的情况下照常执行了，调用方会误以为参数生效了。",
    },
    "check.error_invalid_params_code.truncated": {
        "en": "Probed the first {limit} of {total} tools that declare required parameters.",
        "zh": "共 {total} 个工具声明了必需参数，只探测了前 {limit} 个。",
    },
    # ---------- check 8 - content array ----------
    "check.result_content_array.title": {
        "en": "tools/call returns a content array",
        "zh": "tools/call 返回 content 数组",
    },
    "check.result_content_array.missing": {
        "en": "{tool}: the result carries no content array.",
        "zh": "{tool}：返回值没有 content 数组。",
    },
    "check.result_content_array.not_array": {
        "en": "{tool}: content is {kind}, expected an array.",
        "zh": "{tool}：content 是 {kind}，应为数组。",
    },
    "check.result_content_array.bad_block": {
        "en": "{tool}: content[{index}] has unsupported type {value!r}.",
        "zh": "{tool}：content[{index}] 的类型 {value!r} 不受支持。",
    },
    "check.result_content_array.bad_text": {
        "en": "{tool}: content[{index}] is a text block without a string text field.",
        "zh": "{tool}：content[{index}] 是 text 块，却没有字符串 text 字段。",
    },
    "check.result_content_array.bad_blob": {
        "en": "{tool}: content[{index}] of type {value!r} needs both data and mimeType.",
        "zh": "{tool}：content[{index}] 是 {value!r} 块，必须同时带 data 与 mimeType。",
    },
    "check.result_content_array.bad_resource": {
        "en": "{tool}: content[{index}] is a resource block without a resource object.",
        "zh": "{tool}：content[{index}] 是 resource 块，却没有 resource 对象。",
    },
    # ---------- check 9 - isError declared explicitly ----------
    "check.result_is_error_flag.title": {
        "en": "tools/call states isError",
        "zh": "tools/call 显式声明 isError",
    },
    "check.result_is_error_flag.missing": {
        "en": "{tool}: the result omits isError, so callers must guess whether it failed.",
        "zh": "{tool}：返回值省略了 isError，调用方只能猜这次是不是失败了。",
    },
    "check.result_is_error_flag.not_bool": {
        "en": "{tool}: isError is {kind}, expected a boolean.",
        "zh": "{tool}：isError 是 {kind}，应为布尔值。",
    },
    # ---------- check 10 - stdout purity ----------
    "check.stdout_purity.title": {
        "en": "stdout carries only JSON-RPC frames",
        "zh": "stdout 上只有 JSON-RPC 报文",
    },
    "check.stdout_purity.violation": {
        "en": "stdout carried {count} non-protocol line(s). "
              "Clients parse stdout as the frame stream.",
        "zh": "stdout 上出现了 {count} 行非协议内容。客户端把 stdout 当作报文流来解析。",
    },
    # ---------- watch command ----------
    "cmd.watch": {
        "en": "Sit between a client and a server, forwarding every frame untouched "
              "and showing each one as it goes by.",
        "zh": "挡在客户端与服务端之间，原样转发每一帧，并把它实时显示出来。",
    },
    "opt.record": {
        "en": "Append every frame to a JSONL file as it flows.",
        "zh": "把流经的每一帧实时追加写入 JSONL 文件。",
    },
    "opt.print_config": {
        "en": "Print a ready-to-paste MCP client config for this server, then exit.",
        "zh": "输出可直接粘贴的 MCP 客户端配置，然后退出。",
    },
    # ---------- watch runtime ----------
    "watch.banner": {"en": "mcpdump watch", "zh": "mcpdump watch"},
    "watch.proxying": {"en": "proxying {server}", "zh": "代理 {server}"},
    "watch.channel_note": {
        "en": "stdout carries the protocol; this view goes to stderr.",
        "zh": "stdout 是协议通道，这个视图输出在 stderr。",
    },
    "watch.recording": {"en": "recording to {path}", "zh": "录制到 {path}"},
    "watch.record_failed": {
        "en": "Cannot write the recording to {path}: {error}",
        "zh": "无法写入录制文件 {path}：{error}",
    },
    "watch.record_broken": {
        "en": "Recording stopped after {count} frame(s): {error}",
        "zh": "录制在第 {count} 帧后中断：{error}",
    },
    "watch.summary": {
        "en": "{to_server} sent · {to_client} received · {duration}",
        "zh": "发出 {to_server} 帧 · 收到 {to_client} 帧 · 历时 {duration}",
    },
    "watch.dropped": {
        "en": "{count} pending request(s) fell out of the correlation table.",
        "zh": "关联表已丢弃 {count} 条待响应记录。",
    },
    "watch.render_errors": {
        "en": "{count} frame(s) failed to render; forwarding was not affected.",
        "zh": "{count} 帧渲染失败，转发未受影响。",
    },
    "watch.exited": {"en": "Server exited with code {code}.", "zh": "服务端退出，退出码 {code}。"},
    # ---------- the proxy's classification of frames ----------
    "proxy.unparsable": {"en": "<not JSON>", "zh": "<非 JSON>"},
    "proxy.unknown_frame": {"en": "<unrecognised frame>", "zh": "<无法识别的报文>"},
    "proxy.orphan_response": {
        "en": "response id={id} (no matching request)",
        "zh": "响应 id={id}（没有对应的请求）",
    },
    # ---------- demo command ----------
    # This text carries the whole first-contact explanation: the user most likely arrived
    # from one README line with no context, so every sentence must answer what this does.
    "cmd.demo": {
        "en": "Run the whole toolkit against a built-in example server — "
              "no setup, no network, nothing to install.",
        "zh": "拿内置的示例服务端把整条工具链跑一遍——不用配置、不碰网络、不用装东西。",
    },
    "opt.demo_cmd": {
        "en": "Print the launch command of the built-in example server and exit.",
        "zh": "打印内置示例服务端的启动命令后退出。",
    },
    "demo.title": {
        "en": "mcpdump demo — everything it does, in one command.",
        "zh": "mcpdump demo —— 一条命令，看它全部能做什么。",
    },
    "demo.blurb": {
        "en": "An example MCP server ships inside mcpdump. The three steps below run "
              "against it for real — the same output you get by typing them yourself.",
        # Chinese is held under 74 columns on purpose: this shows on the README first screen,
        # and one character more wraps "MCP Server" mid-word on an 80-column terminal.
        "zh": "mcpdump 自带一个示例 MCP Server，下面三步跑的就是它，"
              "输出和你自己敲一模一样。",
    },
    "demo.step.ls": {
        "en": "What does this server expose?",
        "zh": "这个服务端暴露了什么？",
    },
    "demo.step.call": {
        "en": "What does a tool call actually send and receive?",
        "zh": "调用一个工具，到底发了什么、回了什么？",
    },
    "demo.step.check": {
        "en": "Does it follow the MCP spec?",
        "zh": "它符合 MCP 规范吗？",
    },
    "demo.failed": {
        "en": "The built-in example server failed at this step. That is a bug in "
              "mcpdump itself, not in your setup.",
        "zh": "内置示例服务端在这一步失败了。这是 mcpdump 自身的问题，与你的环境无关。",
    },
    "demo.next.cmd": {
        "en": "mcpdump demo --cmd — print the launch command, to use with any other command",
        "zh": "mcpdump demo --cmd —— 打印启动命令，好交给其他命令用",
    },
    "demo.next.tui": {
        "en": "mcpdump tui <SERVER> — browse tools, call them and watch the frames",
        "zh": "mcpdump tui <SERVER> —— 浏览工具、调用它们、观察报文",
    },
    "demo.next.discover": {
        "en": "mcpdump discover — find MCP servers already configured on this machine",
        "zh": "mcpdump discover —— 找出本机已经配好的 MCP Server",
    },
    # ---------- discover command ----------
    "cmd.discover": {
        "en": "Find MCP servers already configured in Claude Desktop, Cursor, "
              "VS Code and others — no need to retype the launch command.",
        "zh": "找出已经配在 Claude Desktop、Cursor、VS Code 等客户端里的 MCP Server，"
              "不用再把启动命令重打一遍。",
    },
    "opt.use": {
        "en": "Connect to this discovered server right away, without picking.",
        "zh": "直接连上这个已发现的 Server，不必再选。",
    },
    "opt.client": {
        "en": "Only look at this client (repeatable).",
        "zh": "只看这个客户端（可重复）。",
    },
    "opt.project": {
        "en": "Treat this directory as the project root when looking for workspace configs.",
        "zh": "把这个目录当作项目根来找工作区配置。",
    },
    "opt.probe": {
        "en": "Actually connect to each server to verify it. Slower.",
        "zh": "真的连一次来验证。会慢一些。",
    },
    "arg.server_wrap": {
        "en": "Server to wrap. Only a launch command — replay works from a file "
              "and cannot use an http(s):// URL.",
        "zh": "要被包裹的 Server。只接受启动命令——录制回放读的是文件，"
              "不支持 http(s):// 地址。",
    },
    "arg.file": {
        "en": "A session recording written by 'mcpdump record' or 'mcpdump watch --record'.",
        "zh": "由 'mcpdump record' 或 'mcpdump watch --record' 写出的会话记录。",
    },
    "arg.record": {
        "en": "A session recording to compare.",
        "zh": "要参与比较的会话记录。",
    },
    "opt.out": {
        "en": "Where to write the recording (JSONL).",
        "zh": "记录写到哪个文件（JSONL）。",
    },
    "opt.step": {
        "en": "Show the raw frame text alongside each step.",
        "zh": "每一步都带上报文原文。",
    },
    "opt.limit": {
        "en": "How many steps to show. 0 means no limit.",
        "zh": "显示多少步。0 表示不限。",
    },
    "opt.wire": {
        "en": "Also render the recorded frames in wire view.",
        "zh": "另外用报文流视图渲染录到的帧。",
    },
    # ---------- record command ----------
    "cmd.record": {
        "en": "Wrap any MCP client and write the whole session to a JSONL file "
              "you can attach to an issue.",
        "zh": "包裹任意 MCP 客户端，把整场会话写进 JSONL 文件，可以直接附到 issue 里。",
    },
    "record.banner": {"en": "mcpdump record", "zh": "mcpdump record"},
    "record.wrapping": {"en": "wrapping {server}", "zh": "包裹 {server}"},
    "record.summary": {
        "en": "Recorded {count} frame(s) to {path}",
        "zh": "已录制 {count} 帧到 {path}",
    },
    "record.open_failed": {
        "en": "Cannot open {path} for writing: {error}",
        "zh": "无法写入 {path}：{error}",
    },
    # ---------- session recording format ----------
    # Raised by ``services/recorder.py`` while reading a JSONL recording. The line number
    # is part of every message: "bad format" alone leaves the user searching by hand.
    "record.line_missing_field": {
        "en": "Line {lineno} is missing field {name!r}",
        "zh": "第 {lineno} 行缺少字段 {name!r}",
    },
    "record.line_wrong_type": {
        "en": "Line {lineno}: {name!r} must be {expected}, got {actual}",
        "zh": "第 {lineno} 行的 {name!r} 应是 {expected}，实际是 {actual}",
    },
    "record.seq_not_integer": {
        "en": "Line {lineno}: 'seq' is not an integer",
        "zh": "第 {lineno} 行的 'seq' 不是整数",
    },
    "record.elapsed_not_number": {
        "en": "Line {lineno}: 'elapsedMs' must be a number or null",
        "zh": "第 {lineno} 行的 'elapsedMs' 应是数字或 null",
    },
    "record.bad_direction": {
        "en": "Line {lineno}: 'direction' must be {to_server!r} or {to_client!r}, got {actual!r}",
        "zh": (
            "第 {lineno} 行的 'direction' 只能是 {to_server!r} 或 {to_client!r}，"
            "实际是 {actual!r}"
        ),
    },
    "record.label.number": {"en": "number", "zh": "数字"},
    "record.not_a_recording": {
        "en": "Line {lineno} is not a {format} recording (found format={found!r})",
        "zh": "第 {lineno} 行不是 {format} 记录（找到 format={found!r}）",
    },
    "record.version_not_integer": {
        "en": "Line {lineno}: 'version' is not an integer",
        "zh": "第 {lineno} 行的 'version' 不是整数",
    },
    "record.version_too_new": {
        "en": "Recording format version {version} is newer than this build supports "
              "({supported}); upgrade mcpdump to read it.",
        "zh": "记录格式版本 {version} 比本程序支持的 {supported} 新，请升级 mcpdump 后再读。",
    },
    "record.line_not_json": {
        "en": "Line {lineno} is not valid JSON: {error}",
        "zh": "第 {lineno} 行不是合法 JSON：{error}",
    },
    "record.line_not_object": {
        "en": "Line {lineno} is not a JSON object",
        "zh": "第 {lineno} 行不是一个 JSON 对象",
    },
    "record.unreadable": {
        "en": "Cannot read {path}: {error}",
        "zh": "读不了 {path}：{error}",
    },
    "record.empty_file": {
        "en": "{path} is an empty file",
        "zh": "{path} 是空文件",
    },
    # ---------- replay command ----------
    "cmd.replay": {
        "en": "Replay a recorded session frame by frame. No process, no network — "
              "the output depends on the file alone.",
        "zh": "逐帧回放一份会话记录。不启动进程、不碰网络——输出只取决于文件本身。",
    },
    "replay.label.source": {"en": "source", "zh": "来源"},
    "replay.label.steps": {"en": "steps", "zh": "步数"},
    "replay.label.exchanges": {"en": "round trips", "zh": "往返次数"},
    "replay.label.bytes": {"en": "payload", "zh": "报文量"},
    "replay.label.notifications": {"en": "notifications", "zh": "通知"},
    "replay.label.orphan": {"en": "orphan responses", "zh": "孤儿响应"},
    "replay.label.truncated": {"en": "long gaps", "zh": "长间隔"},
    "replay.label.dangling": {"en": "unanswered requests", "zh": "未响应的请求"},
    "replay.bytes_b": {"en": "{count} B", "zh": "{count} 字节"},
    "replay.bytes_kb": {"en": "{count:.1f} KB", "zh": "{count:.1f} KB"},
    "replay.bytes_mb": {"en": "{count:.1f} MB", "zh": "{count:.1f} MB"},
    "replay.orphan_mark": {"en": "(no matching request)", "zh": "（没有对应的请求）"},
    "replay.dangling_mark": {"en": "(never answered)", "zh": "（始终没有响应）"},
    "replay.truncated_mark": {"en": "(slow round trip)", "zh": "（往返很慢）"},
    "replay.empty": {"en": "The recording contains no frames.", "zh": "这份记录里没有任何帧。"},
    "replay.omitted": {
        "en": "{count} more step(s) not shown. Use --limit 0 to see them all.",
        "zh": "还有 {count} 步未显示。用 --limit 0 看全部。",
    },
    "replay.next_steps": {"en": "Next", "zh": "下一步"},
    # The angle brackets are placeholders, not part of the command: a real path goes there.
    "replay.next.diff": {
        "en": "mcpdump diff {path} <other recording>",
        "zh": "mcpdump diff {path} <另一份记录>",
    },
    "replay.next.json": {
        "en": "mcpdump replay {path} --json",
        "zh": "mcpdump replay {path} --json",
    },
    # ---------- diff command ----------
    "cmd.diff": {
        "en": "Compare two session recordings structurally: tools added or removed, "
              "schema changes, capability changes, latency regressions.",
        "zh": "结构化比较两份会话记录：工具增删、schema 变更、能力变更、耗时劣化。",
    },
    "diff.headers": {
        "en": "Comparing {before}  →  {after}",
        "zh": "比较 {before}  →  {after}",
    },
    "diff.unnamed": {"en": "<unnamed server>", "zh": "<未命名服务端>"},
    "diff.tool_count": {"en": "{count} tool(s)", "zh": "{count} 个工具"},
    "diff.no_capabilities": {"en": "no capabilities", "zh": "未声明能力"},
    "diff.identical": {
        "en": "No structural differences — the contract is unchanged.",
        "zh": "没有结构性差异——契约未变。",
    },
    "diff.section_structural": {"en": "Contract", "zh": "契约变更"},
    "diff.section_latency": {"en": "Latency", "zh": "耗时"},
    "diff.total": {"en": "{count} difference(s) found.", "zh": "共发现 {count} 处差异。"},
    "diff.next_steps": {"en": "Next", "zh": "下一步"},
    # ``detail`` is the sentence for a person; ``kind`` and ``subject`` stay machine-stable.
    "diff.tool_added": {"en": "tool added", "zh": "工具新增"},
    "diff.tool_removed": {"en": "tool removed", "zh": "工具移除"},
    "diff.schema_changed": {"en": "schema internal structure changed", "zh": "schema 内部结构变化"},
    "diff.schema_delta_separator": {"en": ", ", "zh": "，"},
    "diff.output_schema_changed": {"en": "output schema changed", "zh": "返回值 schema 变化"},
    "diff.capability_added": {"en": "capability newly declared", "zh": "能力新增声明"},
    "diff.capability_removed": {"en": "capability no longer declared", "zh": "能力不再声明"},
    "diff.protocol_version_changed": {"en": "protocol version changed", "zh": "协议版本变化"},
    "diff.latency_regressed": {
        "en": "latency regressed {old_ms:.0f}ms→{new_ms:.0f}ms",
        "zh": "耗时劣化 {old_ms:.0f}ms→{new_ms:.0f}ms",
    },
    # ---------- mock command ----------
    "cmd.mock": {
        "en": "Serve a recording as a fake MCP server: a real client connects to it "
              "and gets the answers that were recorded.",
        "zh": "把一份会话记录当成假 MCP Server 提供出去：真客户端连上来，"
              "拿到当时录下的响应。",
    },
    "opt.from": {
        "en": "Record this live server first, then serve the recording (instead of "
              "reading an existing FILE).",
        "zh": "先连一次这个服务端并录下来，再拿录到的东西提供出去"
              "（替代读一个已有的 FILE）。",
    },
    "opt.mock_out": {
        "en": "Where to write the recording made by --from.",
        "zh": "--from 录下来的记录写到哪个文件。",
    },
    "mock.out_required": {
        "en": "--from needs a place to write the recording (--out).",
        "zh": "--from 需要一个地方写记录（--out）。",
    },
    "mock.need_one_source": {
        "en": "Give either a FILE to serve, or --from SERVER to record one first "
              "(not both, not neither).",
        "zh": "请二选一：给一个 FILE 直接提供出去，或给 --from SERVER 先录一份"
              "（不能都写，也不能都不写）。",
    },
    "mock.recorded": {
        "en": "Recorded to {path} — reuse it with 'mcpdump mock {path}' or "
              "'mcpdump replay {path}'.",
        "zh": "已录到 {path}——之后可以用 'mcpdump mock {path}' 或 "
              "'mcpdump replay {path}' 重复使用。",
    },
    "arg.record_jsonl": {
        "en": "A session recording written by 'mcpdump record' or 'mcpdump watch --record'.",
        "zh": "由 'mcpdump record' 或 'mcpdump watch --record' 写下的会话记录文件。",
    },
    "opt.max_requests": {
        "en": "Exit after serving this many requests (0 = no limit).",
        "zh": "服务满这么多条请求后退出（0 表示不限）。",
    },
    "mock.banner": {
        "en": "Mocking from {path}",
        "zh": "正在用 {path} 提供假服务端",
    },
    "mock.ready": {
        "en": "Ready on stdio · {count} answerable method(s): {methods}",
        "zh": "已在 stdio 上就绪 · 可回答 {count} 个方法：{methods}",
    },
    "mock.summary": {
        "en": "Served {served} request(s), {unmatched} unmatched, "
              "{notifications} notification(s).",
        "zh": "共服务 {served} 条请求，{unmatched} 条没有答案，"
              "{notifications} 条通知。",
    },
    "mock.unmatched_hint": {
        "en": "Unmatched methods were answered with a JSON-RPC error. "
              "Record a session that includes them if the client needs them.",
        "zh": "没有答案的方法已用 JSON-RPC error 回复。"
              "如果客户端需要它们，请录一份包含这些请求的会话。",
    },
    "mock.unmatched": {
        "en": "No recorded answer for {method}.",
        "zh": "记录里没有 {method} 的答案。",
    },
    "mock.no_initialize": {
        "en": "The recording has no response for 'initialize', so it cannot serve as a mock "
              "server (answerable methods: {methods}). Record a session that contains the "
              "full handshake, for example: "
              "mcpdump record --out session.jsonl '<launch command>'",
        "zh": "这份记录里没有 'initialize' 的响应，无法作为 mock 服务端启动"
              "（记录里可回答的方法：{methods}）。请录一份包含完整握手的会话，例如："
              "mcpdump record --out session.jsonl '<启动命令>'",
    },
    "mock.no_methods": {"en": "none", "zh": "无"},
    "discover.scanned": {
        "en": "{clients} client config(s) found · {servers} server(s)",
        "zh": "找到 {clients} 个客户端配置 · 共 {servers} 个 Server",
    },
    "discover.title": {"en": "Servers", "zh": "发现的 Server"},
    "discover.none_found": {
        "en": "No MCP server configuration found on this machine.",
        "zh": "这台机器上没有找到任何 MCP 客户端配置。",
    },
    "discover.none_found_hint": {
        "en": "mcpdump works on any server, configured or not — just pass the command:",
        "zh": "mcpdump 不依赖客户端配置，任何 Server 都能直接连：",
    },
    "discover.not_found": {
        "en": "Not installed: {clients}",
        "zh": "未找到配置：{clients}",
    },
    "discover.uncertain_note": {
        "en": "Paths for {clients} come from community reports and may be out of date.",
        "zh": "{clients} 的路径来自社区资料，可能已过时。",
    },
    "discover.problem_note": {
        "en": "{count} config entry/entries could not be read — see below.",
        "zh": "有 {count} 条配置读不出来——见下。",
    },
    "discover.static_note": {
        "en": "Availability is from static checks only — no server was started. "
              "Add --probe to really connect.",
        "zh": "可用性只来自静态检查，没有启动过任何 Server。加 --probe 才会真去连。",
    },
    "discover.pick": {
        "en": "Pick a server to inspect [1-{count}, q to quit]: ",
        "zh": "选一个 Server 查看 [1-{count}，q 退出]：",
    },
    "discover.pick_invalid": {
        "en": "Not a valid choice.",
        "zh": "不是有效的选项。",
    },
    "discover.unknown_name": {
        "en": "No discovered server is named {name}.",
        "zh": "没有发现叫 {name} 的 Server。",
    },
    "discover.unknown_client": {
        "en": "Unknown client: {name}. Known: {names}",
        "zh": "不认识这个客户端：{name}。可用的有：{names}",
    },
    "discover.probing": {"en": "probing {name} …", "zh": "正在探测 {name} …"},
    "discover.ambiguous": {
        "en": "{name} appears in {count} clients; narrow it down with --client.",
        "zh": "{name} 出现在 {count} 个客户端里，用 --client 指定一个。",
    },
    "discover.candidates": {"en": "Candidates: {names}", "zh": "可选的：{names}"},
    "discover.status.ready": {"en": "ready", "zh": "就绪"},
    "discover.status.verified": {"en": "verified", "zh": "已实测"},
    "discover.status.blocked": {"en": "blocked", "zh": "不可用"},
    # ---------- discover problem explanations ----------
    # Each must be **actionable**: name the missing variable or the missing command,
    # rather than a vague "the configuration has a problem".
    "discover.problem.config-unreadable": {
        "en": "config file cannot be read: {detail}",
        "zh": "配置文件读不了：{detail}",
    },
    "discover.problem.config-invalid": {
        "en": "config file is not valid JSON: {detail}",
        "zh": "配置文件不是合法 JSON：{detail}",
    },
    "discover.problem.config-not-a-map": {
        "en": "config file's top level is not a JSON object",
        "zh": "配置文件的顶层不是一个 JSON 对象",
    },
    "discover.problem.server-list-malformed": {
        "en": "server list at {detail} has the wrong shape (object vs array)",
        "zh": "{detail} 处的服务器列表形态不对（对象与数组混了）",
    },
    "discover.problem.entry-invalid": {
        "en": "{detail} has neither a usable command nor a url",
        "zh": "{detail} 既没有可用的启动命令，也没有 url",
    },
    "discover.problem.entry-not-a-map": {
        "en": "{detail} is not a JSON object",
        "zh": "{detail} 不是一个 JSON 对象",
    },
    "discover.problem.command-missing": {
        "en": "command not found: {detail}",
        "zh": "找不到命令：{detail}",
    },
    "discover.problem.env-missing": {
        "en": "environment variable not set: {detail}",
        "zh": "环境变量没有设置：{detail}",
    },
    "discover.problem.cwd-missing": {
        "en": "working directory does not exist: {detail}",
        "zh": "工作目录不存在：{detail}",
    },
    "discover.problem.transport-unsupported": {
        "en": "mcpdump cannot drive {detail} transports yet — only stdio",
        "zh": "mcpdump 还不支持 {detail} 传输——目前只支持 stdio",
    },
    "discover.problem.port-closed": {
        "en": "cannot reach {detail}",
        "zh": "连不上 {detail}",
    },
    "discover.problem.port-timeout": {
        "en": "no answer from {detail}",
        "zh": "{detail} 没有响应",
    },
    "discover.problem.port-not-listening": {
        "en": "nothing is listening on {detail}",
        "zh": "{detail} 上没有服务在监听",
    },
    "discover.problem.probe-failed": {
        "en": "connected but the handshake failed: {detail}",
        "zh": "连上了，但握手失败：{detail}",
    },
    # ---------- bare mcpdump ----------
    "app.no_args_found": {
        "en": "Found {count} MCP server(s) already configured on this machine.",
        "zh": "在这台机器上找到了 {count} 个已经配好的 MCP Server。",
    },
    # ---------- TUI four-pane view ----------
    "cmd.tui": {
        "en": "Open an interactive screen: browse tools, call them, watch the frames.",
        "zh": "打开交互式界面：浏览工具、调用它们、观察报文。",
    },
    "opt.script": {
        "en": "Replay a comma-separated key sequence instead of reading the keyboard "
              "(e.g. 'tab,enter,q'). Intended for CI and screen recordings.",
        "zh": "用一串逗号分隔的按键代替键盘输入（如 'tab,enter,q'）。供 CI 与录屏使用。",
    },
    # Pane names. The counted variant is used in the title bar, falling back to the uncounted one.
    "tui.pane.server": {
        "en": "Server",
        "zh": "服务端",
    },
    "tui.pane.tools": {
        "en": "Tools",
        "zh": "工具",
    },
    "tui.pane.tools_count": {
        "en": "Tools · {count}",
        "zh": "工具 · {count}",
    },
    "tui.pane.wire": {
        "en": "Wire",
        "zh": "报文流",
    },
    "tui.pane.wire_count": {
        "en": "Wire · {count}",
        "zh": "报文流 · {count}",
    },
    "tui.pane.wire_offset": {
        "en": "Wire · {index}/{count}",
        "zh": "报文流 · {index}/{count}",
    },
    # Empty states. An empty pane must say why it is empty, or the user assumes a hang.
    "tui.connecting": {
        "en": "connecting…",
        "zh": "正在连接…",
    },
    "tui.no_tools": {
        "en": "this server exposes no tools",
        "zh": "这个服务端没有暴露任何工具",
    },
    "tui.no_tools_filtered": {
        "en": "no tool matches the filter",
        "zh": "没有工具符合过滤条件",
    },
    "tui.no_frames": {
        "en": "no traffic yet — press ⏎ on a tool to call it",
        "zh": "还没有报文——在工具上按 ⏎ 调用一次",
    },
    "tui.older": {
        "en": "⋯ {count} older",
        "zh": "⋯ 更早 {count} 条",
    },
    # Timeline summary and sparkline
    "tui.calls_summary": {
        "en": "Timeline · {count} calls · p50 {p50} · max {max}",
        "zh": "时间线 · {count} 次调用 · p50 {p50} · max {max}",
    },
    "tui.timeline_empty": {
        "en": "Timeline · {count} calls · none succeeded yet",
        "zh": "时间线 · {count} 次调用 · 还没有成功的",
    },
    "tui.call_failed": {
        "en": "failed",
        "zh": "失败",
    },
    # Mode labels and key hints
    "tui.mode.browse": {
        "en": "BROWSE",
        "zh": "浏览",
    },
    "tui.mode.filter": {
        "en": "FILTER",
        "zh": "过滤",
    },
    "tui.mode.args": {
        "en": "ARGS",
        "zh": "参数",
    },
    "tui.hint.browse": {
        "en": "tab panes · ↑↓ move · ⏎ call · / filter · q quit",
        "zh": "tab 换区 · ↑↓ 移动 · ⏎ 调用 · / 过滤 · q 退出",
    },
    "tui.busy": {
        "en": "running…",
        "zh": "运行中…",
    },
    # The three argument-line faults. Reported separately because the fixes differ completely.
    "tui.problem.not_json": {
        "en": "not valid JSON",
        "zh": "不是合法的 JSON",
    },
    "tui.problem.not_object": {
        "en": "must be a JSON object",
        "zh": "必须是 JSON 对象",
    },
    "tui.problem.missing_required": {
        "en": "missing required parameter: {name}",
        "zh": "缺少必需参数：{name}",
    },
    # Why the shell refuses to start. **Refusing is not degrading**: the four panes cannot be
    "tui.need_terminal": {
        "en": "The interactive screen needs a real terminal.",
        "zh": "交互式界面需要真实的终端。",
    },
    "tui.need_terminal.hint": {
        "en": "For a non-interactive listing, run: mcpdump ls <SERVER>",
        "zh": "要非交互地列一遍，请用：mcpdump ls <SERVER>",
    },
    "tui.dumb_terminal": {
        "en": "TERM is '{term}': a dumb terminal cannot address the screen, "
              "and its size is always reported as 80x25.",
        "zh": "TERM 是 '{term}'：哑终端无法定位光标，而且尺寸永远只被报成 80x25。",
    },
    "tui.dumb_terminal.hint": {
        "en": "Unset TERM, or set it to a real terminal type "
              "(e.g. export TERM=xterm-256color).",
        "zh": "取消 TERM，或把它设成真实的终端类型（例如 export TERM=xterm-256color）。",
    },
    # A typo in --script must error: skipping silently gives a false "passed, minus one key".
    "tui.script.unknown_key": {
        "en": "unknown key name in --script: {token}",
        "zh": "--script 里有认不出的按键名：{token}",
    },
    "tui.script.known_keys": {
        "en": "known names: {names}",
        "zh": "可用的名字：{names}",
    },
}


def _detect_language() -> str:
    """``MCPDUMP_LANG`` first, then the locale, falling back to English."""
    explicit = os.environ.get("MCPDUMP_LANG", "").strip().lower()
    if explicit:
        return "zh" if explicit.startswith("zh") else DEFAULT_LANGUAGE
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(variable, "").strip().lower().startswith("zh"):
            return "zh"
    return DEFAULT_LANGUAGE


_language = _detect_language()


def current_language() -> str:
    """The language currently used for output."""
    return _language


def set_language(language: str) -> None:
    """Switch the output language. An unknown value falls back to the default rather
    than raising.

    Note: Typer's command help is evaluated at import time and will not update.
    """
    global _language
    _language = language if language in LANGUAGES else DEFAULT_LANGUAGE


def t(key: str, *, lang: str | None = None, **kwargs: object) -> str:
    """Look up a message and fill in its placeholders.

    ``lang`` is for tests and explicit overrides; normally omit it and let
    ``current_language()`` decide.
    """
    entry = MESSAGES.get(key)
    if entry is None:
        raise KeyError(f"unknown message key: {key!r}")
    template = entry.get(lang or _language) or entry[DEFAULT_LANGUAGE]
    return template.format(**kwargs)


def capability_label(name: str) -> str:
    """Render an MCP capability name as a human-readable label; unknown names pass
    through unchanged.

    **Unknown capabilities must be shown as-is**: a capability outside the spec is
    exactly the thing most worth seeing, and translating it to an empty string or
    hiding it would make a decision on the user's behalf.
    """
    key = f"capability.{name}"
    return t(key) if key in MESSAGES else name
