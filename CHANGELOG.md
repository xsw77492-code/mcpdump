# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [0.1.0] - 2026-09-26

首个公开版本。目标是"能看见"——把 MCP 的协议交互从黑盒变成可读报文。

这一版把它做成了一条完整的工具链：看得见（`ls` / `call`）、判得准（`check`）、
录得下也回得来（`record` / `replay` / `diff`）、跑得进 CI（`--json` / 退出码）。

### 新增
- `mcpdump ls <server>`：握手并列出服务端的身份、协议版本、声明能力、工具、资源与提示词模板
- `mcpdump call <server> <tool>`：调用工具并打印原始 JSON-RPC 请求帧与响应帧，含耗时
- 零依赖 stdio 传输：自行实现 JSON-RPC 2.0 编解码与子进程通信，不引入任何 MCP SDK
- 能力门控：服务端未声明的能力不发起请求，避免协议违规
- 脏 stdout 容忍：服务端往 stdout 打印非协议内容时记录并继续等待，而非崩溃
- `--json`：所有命令支持机器可读输出，字节稳定，可被脚本严格解析
- `--trace`：把每一次收发的报文镜像到 stderr
- 五档退出码语义：`0` 成功 / `1` 用法错误 / `2` 服务端错误 / `3` 超时 / `4` 环境错误
- 工具名拼写纠错：拼错时给出最接近的候选建议
- **示例服务端搬进包内：`python -m mcpdump.demo`**。它原先住在 `examples/echo_server.py`
  ——那是**仓库资产，不是包资产**：`pip install mcpdump` 的人根本拿不到它，README 第一条
  命令对这类用户必崩。现在它在 `src/mcpdump/demo.py`，纯标准库、**零内部依赖**；
  `examples/` 整个删除，测试改从包内启动它（`tests/conftest.py` 的 `DEMO_SERVER_ARGS`，
  一处定义、`-m` 与 argv 两种形态共用），HTTP 测试夹具也仍然复用同一份 `handle`
  （**只有一份 MCP 逻辑**）
- **`mcpdump demo`：第一次接触的入口**。对着内置服务端依次跑 `ls` → `call` →
  `check`，把每一步的真实输出打出来——**不需要参数、不需要配置、不联网**。
  `--help` 里排第一，也是唯一不接受 SERVER 参数的命令。实测 6.5 秒 / 98 行 / stderr 为空。
  `mcpdump demo --cmd` 打印内置服务端的准确启动命令：要启动它的是**装了 mcpdump 的那个
  解释器**，而它不一定是 `PATH` 上的 `python`——`uvx` / `pipx` / 激活的 venv 全部落空，
  报的是 `No module named 'mcpdump'`
- **`mcpdump tui`：交互式四区界面**。服务端身份 / 工具列表 / 报文流 /
  时间线水位图同屏，单键操作（`tab` 切区、`↑↓` 移动、`⏎` 调用、`/` 过滤、`q` 退出），
  **不用组合键**。工具参数从 `inputSchema` 生成一行可编辑的 JSON，改完回车即发
- **报文流实时增量刷新，不整屏重绘**：`Live(screen=False)` 只重写变化区，且重绘由
  **状态变化**驱动而不是定时器——用户没动的时候一帧都不画。退出后最后一屏留在终端
  历史里，可以翻回去当证据
- **时间线水位图**：按半格字符画每次调用的耗时条，与本次会话的**中位数**比，
  相对 `≥1.5×` **且**绝对 `≥+50ms` 才算异常并打 `⚠`。用中位数不用均值——一次
  30 秒超时能把均值拉高十倍，之后所有正常调用都"看起来很快"。失败的调用不画条，
  直接给 `✗` 与原因：给一个长度会让人以为它只是慢
- **非 TTY 与哑终端一律拒绝启动**，并附一条能直接抄的替代命令
  （`mcpdump ls <SERVER>`）。哑终端要单独判：Rich 在 `TERM=dumb` 下把终端尺寸
  **硬编码报成 80x25**，照画必然错位
- `mcpdump tui --script "tab,enter,q"`（隐藏选项）：用脚本化的按键序列驱动界面，
  供 CI 冒烟与录屏复现。未知键名直接报错并列出可用名字——静默跳过会给出
  "跑通了、但少按了一次"的结果，那比直接失败难查得多
- **远程传输：Streamable HTTP**。`ls` / `call` / `check` / `watch` / `record`
  现在都接受 `http(s)://` 地址，与 stdio 服务端**同一套命令、同一套输出**。
  响应是 `application/json` 还是一次性 `text/event-stream` 都能读；
  `Mcp-Session-Id` 会自动记住并回带；退出时按规范发 `DELETE` 结束会话（失败不报错）。
  **用标准库 `urllib` 实现，运行依赖仍然只有 `typer` 与 `rich` 两个**
- **`mcpdump mock`：没服务端也能干活**。`mcpdump mock <记录文件>` 用一份记录
  当答案册作答：客户端问什么就立刻答什么，**不重放时间轴**（那个延迟属于当时那个
  服务端，与现在这个客户端无关）。匹配按**方法名**不按 id——JSON-RPC 的 `id` 由
  发起方定，录制时客户端用的和现在这个不同，响应里的 `id` 会被**改写成当前的**；
  同一个方法被问第二次就用下一条答案（循环）
- `mcpdump mock --from <server>`：先连一次真服务端把能力全问一遍并落盘，再拿这份记录
  起 Mock——**录一次、用很多次**。于是"某个服务端挂了"这种场景可以被固化成一份
  几 KB 的样本，附在 issue 里让所有人复现
- `mcpdump mock --max-requests <N>`：服务满 N 条请求就退出，方便写进 CI 或冒烟脚本
- **未匹配的方法回 `-32601`，不假装成功**：静默返回空结果会让客户端在错的前提上
  继续跑，而它得到的那份"能力表"是假的。没有 `initialize` 答案的记录直接拒绝启动
  ——否则客户端会卡在握手超时，报的是"服务端无响应"，与真因（记录不对）毫无关系
- **通知不会被回应**：`notifications/initialized` 按规范就没有响应，Mock 回它会让
  客户端收到一条它没在等的东西
- **黑匣子三件套：`mcpdump record` / `mcpdump replay` / `mcpdump diff`**。
  从"看当下"跃迁到"查过去"——**记录可以附在 issue 里，别人不需要装你的 Node、
  不需要你的 API key、不需要同一个操作系统，就能看到你当时看到了什么**
- `mcpdump record --out <file> <server>`：包裹任意 MCP 客户端，全程落盘 JSONL。
  与 `watch` 共用同一个代理，区别只在**看什么**——`record` 的主体是文件，
  stderr 上只留一条进度，stdout 上没有任何协议流量之外的东西
- `mcpdump replay <file>`：回放一份记录，**不启动任何进程、不碰网络**。
  输出**只是文件的函数**：不重算耗时（`elapsedMs` 是复现的，不是测量的）、
  不重新排序、不跳过坏帧。同一份记录回放两次，输出逐字节相同
- `mcpdump replay --step`（逐帧附报文原文）、`--limit <N>`（0 = 不限量）、
  `--wire`（复用 `ls` / `call` 的逐帧排版）、`--json`
- `mcpdump diff <a> <b>`：**结构化**差异，不是文本 diff。记录里混着 `seq` / `atMs` /
  `elapsedMs`，逐行比的结果是"整份文件都变了"，真实变化全被淹没。这里比的是契约：
  工具增删、参数 schema 变更、能力与协议版本变更、耗时劣化
- **记录格式有了唯一事实源 `services/recorder.py`**：M5 的 `watch --record` 已经在写
  这个格式，M7 的 `replay` / `diff` 要读它。格式定义两遍，两边必然各自漂移
- **记录文件带 header 与版本号**。拿到不认识的版本**必须拒绝**，而不是尽力而为地
  猜——猜错的后果是"回放结果和录制时不一样"，而用户正好是拿回放当证据的。
  **错误地"成功了"比直接报错危险得多**
- **`--record` 的产物现在可以直接喂给 `replay` / `diff`**，字段完全一致，
  不新增也不改名
- **耗时劣化带双门槛**：相对涨幅（1.5 倍）与绝对增量（50 ms）**同时**满足才报。
  用中位数不用平均值——一次 30 秒的超时能把平均值拉高十倍，而那正是最容易
  骗过"劣化"判断的单点；`800ms → 812ms` 是噪声，`800ms → 3200ms` 才是信号
- **`mcpdump discover`**：扫描本机已装客户端的 MCP 配置，把你**已经配好**的
  Server 找出来，选一个直接连。想试 mcpdump 时最大的阻力不是安装，是"那串启动命令还得
  再打一遍"——可它早就写在配置文件里了。覆盖 **11 个客户端规格**：Claude Desktop /
  Cursor / Claude Code（user + local scope）/ VS Code（工作区 + 便携 + Copilot CLI）/
  Windsurf / Cline / Continue / Zed / OpenClaw
- **裸跑 `mcpdump` 不再甩一屏帮助**：先扫描本机有什么，列出来；一个都没找到才回落帮助
- `mcpdump discover --use <名字>`：直接连上，不必先看列表再选
- `mcpdump discover --client <名字>`：只看某个客户端，名字或 id 都认，可重复
- `mcpdump discover --project <目录>`：把该目录当作项目根来找工作区配置
- `mcpdump discover --probe`：真去连一次。stdio 走完整握手，URL 只探端口
- `mcpdump discover --json`：机读通道，含每个客户端的配置路径与每个 Server 的可用性
- **退出码契约有了单一事实源**：新增 `exits.py`。此前它只写在文档和 `cli.py` 的
  docstring 里，代码侧则是各写各的——`check` 用了常量，`ls` / `call` 写的是字面量
  `return 2`。文档里写着"已完成"、代码里没有单一事实源，这种状态最危险
- **`ui.format_duration()`**：把毫秒写成人一眼能读出的量纲（`12.3 ms` → `1.00 s` →
  `3m 3.5s` → `1h 00m`）。**量纲换算属于渲染层，不属于文案层**——把 `{total:.1f} ms`
  写进文案表，等于要求每个译者都知道 183472.5 ms 是 3 分 3.5 秒
- **`tests/test_architecture.py`：15 项结构不变量检查**，用 AST 静态分析源码，把文档里的
  分层铁律变成**会失败的断言**。规则用白名单而非黑名单：新加一条依赖必须显式登记，
  那正是"约束"两个字的含义——黑名单只会漏掉还没人想到的那条边
- **`py.typed`（PEP 561）**：项目 100% 带类型注解，但缺了这个空文件，下游装了 mcpdump
  也用不上——类型检查器会直接当它没有注解。CI 里加了一道打包断言守住它，
  因为空文件最容易在某次重构里被顺手丢掉，而且丢了之后本地一切正常
- **`mcpdump watch`**：透明代理。把 `mcpdump watch "<server 启动命令>"` 当成服务端
  交给任意 MCP 客户端，mcpdump 再去启动真正的服务端，**原样搬运**两个方向上的每一帧，
  同时把每一帧实时显示出来。客户端与服务端都不用改一行代码
- `mcpdump watch --record <path>`：把每一帧实时落盘成 JSONL，字段为
  `seq` / `atMs` / `direction` / `method` / `elapsedMs` / `line`。
  **每写一行即 flush**——watch 通常以 Ctrl-C 收场，攒在缓冲区里的记录会跟着进程一起消失，
  而最后几帧恰恰是出问题时最想回看的部分
- `mcpdump watch --print-config <server>`：输出可直接粘贴进 MCP 客户端配置的 JSON 片段，
  键名从启动命令里猜（`npx -y @acme/weather-mcp` → `weather-mcp`）
- `mcpdump watch --quiet-server` / `--full`：压掉服务端自己的 stderr / 报文不截断
- **`core/proxy.py`**：三路泵（上游 stdin → 子进程 stdin、子进程 stdout → 上游 stdout、
  子进程 stderr → mcpdump stderr）+ 帧观测。全程二进制搬运，**不做"解码再编码"**——
  那会顺手把 Windows 的 CRLF 规范化掉，看起来无害，实际上改变了线上字节
- `ui.wire` 拆出逐帧接口（`request_frame` / `response_frame` / `build_frame` / `render_frame`），
  与 `call` 的整批接口共用同一套帧构造与排版。`watch` 边转发边画，不攒批——
  上万帧攒着就是内存泄漏
- `tests/fixtures/line_echo_server.py`：**不懂 MCP** 的最小对端。代理的职责是字节级搬运，
  用真 MCP 服务端测会把"代理对不对"和"服务端对不对"混在一起
- **`mcpdump check`**：对任意 MCP Server 跑 10 项协议一致性检查，
  逐条给出规范条款、失败原因与**原始报文证据**。人读报告里通过的项只占一行，失败项才展开
- `mcpdump check --json`：机读通道，含 `summary` 汇总与每项的 `findings`（附证据原文）
- `mcpdump check --badge`：输出可直接粘进 README 的 shields.io 徽章。等级由通过率决定
  （全绿 / 良好 / 及格 / 不及格），回链带 `utm_source=badge` 便于归因
- **检查项插件机制**：`services/checks/` 下每项一个文件，导出 `CHECKS` 即被自动发现，
  核心代码零改动。文件名的数字前缀决定**执行顺序**——`stdout-purity` 必须最后跑，
  它读的是整场会话累积下来的 stdout 异常
- `tests/fixtures/broken_server.py`：故意违规的服务端，10 个 fault 可单独开关，
  用于验证"每个 fault 只打中对应的那一项检查"
- **输出语言可切换**：界面**默认英文**——star 的主要来源是英文社区。
  `MCPDUMP_LANG=zh` 或中文 locale 自动切中文，`--help` 一并跟随。
  文案表在 `src/mcpdump/i18n.py`：一个键一条消息、两种语言相邻放置，无构建步骤
- **报文流视图**：`mcpdump call` 的每一帧带方向符号、方法名、右对齐耗时与原始 JSON。
  超长帧按终端宽度截断，末尾给出往返次数、总耗时与最慢一帧
- `mcpdump call --full`：报文不截断，完整展开
- **设计系统层**：统一的语义色板、符号语言、东亚宽字符宽度计算与三段式输出骨架
  （`ui/theme.py` · `ui/symbols.py` · `ui/width.py` · `ui/layout.py`）
- `ui.styled_line()`：把多个 `(文本, 样式)` 片段拼成**同一行**的 `Text`
- `MCPDUMP_ASCII=1`：符号降级为 ASCII，供无法渲染 Unicode 的终端使用
- `mcpdump ls` 输出补上「下一步」提示块，给出可直接照抄的命令
- `--json` 的 `exchanges[]` 新增 `error` 字段
- **GitHub Action：一行接入，把一致性报告作为 PR 注释贴回去**。
  `action.yml` 是 composite action：装 mcpdump → 跑 `check --markdown` → 报告写进
  job summary → 在 PR 上**原地更新**自己上次那条注释（靠一行 HTML 标记认领，推十次
  也只留一条）→ 最后才用 mcpdump 自己的退出码失败。报告一定先进 summary 再判失败，
  否则失败原因跟着一起丢了
- `check --markdown`：把报告输出成 Markdown，给 PR 注释或 CI 摘要用。失败项展开、
  通过项折叠进 `<details>`、证据再折一层。**代码围栏长度按内容里最长的连续反引号算**
  ——证据是服务端返回的原文，围栏短一格，后面的 Markdown 全部错乱
- `services/markdown.py`：Markdown 报告与终端报告（`report.py`）分开。后者产出带样式、
  按终端宽度折行的 `Text`，前者一条都不共享：不受宽度影响、不带 ANSI、不依赖 `ui`。
  两者唯一的共同输入是 `CheckReport`。**服务端自述里的 Markdown 元字符会被转义**——
  一个叫 `a*b` 的服务端不该让整行变成斜体，这和终端报告里"服务端输出不能当 Rich
  markup 用"是同一条规矩，只是换了一套元字符

### 修复
- Windows 下命令拆分：`shlex` 在 posix 模式会把路径中的反斜杠当转义符吃掉，改为按平台选择拆分策略
- `--json` 输出绕开富文本渲染层，避免 Rich 加缩进、折行、高亮破坏可解析性
- **`check` 的裸探针忽略了配置里的 `argv`**。`SessionOptions` 里"给人看的字符串"和
  "真正拿来启动的命令"是两个字段（`server` 与 `launch_spec`），探针此前用了前者。
  后果：含空格的路径（`C:\Program Files\nodejs\node.exe`）被拆坏、`discover --use <name>`
  场景下**同一个服务端 `ls` 连得上而 `check` 连不上**，报错只说找不到可执行文件，
  与真因（拿错了参数）毫无关系。这条是改 `mcpdump demo` 时被端到端测试逼出来的
- **本地回环地址不再走代理**。`urllib` 默认读 `HTTP_PROXY`，而开发机几乎都设着它、
  通常又不设 `no_proxy`——后果是 `mcpdump ls http://127.0.0.1:3000/mcp` 的请求被发去
  代理，代理连不上后端回一个 **502**，用户于是去查那个**根本没被访问到**的服务端
- **HTTP 连接超时不再漏出原始异常**。socket 层超时抛的是 `TimeoutError`，不是
  `URLError`，原先的 `except` 接不住——用户看到的是一条不含 URL 的 `timed out`，
  不知道是哪个地址出的问题
- **`--timeout` 在 HTTP 路径上不再静默失效**。stdio 的超时在会话手里、HTTP 的在
  传输层 socket 里，是两个旋钮；此前只传了前者，用户显式设的值在 HTTP 下不起作用
  且不报错
- **悬空请求在回放里完全消失**：`iter_steps` 只在响应到达时产出一条步，请求进了
  待响应表就再也没有人负责把它吐出来。于是"客户端发了 `initialize`、服务端立刻
  崩了"这种记录回放出来是**空的**——而那恰恰是最需要回放的一种：用户拿它来证明
  "我一个字节都没收到"。而 `pending_requests()` 的 docstring 明明写着"它们就是
  悬空请求"，却永远返回空列表。现在文件读完后会把表里剩下的请求按**发出时刻**
  补吐出来（按时刻而不是按 `id` 字典序——后者会让回放顺序取决于客户端随便起的 `id`）
- **`diff` 的耗时劣化检测静默失效**：`build_contract` 扫的是**请求**帧上的
  `elapsedMs`，而代理层把它记在**响应**帧上（实测：`{"seq": 2, "direction":
  "to_client", "elapsedMs": 1081.957}`）——请求帧上永远是 `null`。于是耗时表
  永远是空的，`diff` 看起来一切正常，只是**永远不会说"这条变慢了"**。
  这类"看起来在工作、其实什么都没做"的缺陷比直接报错危险得多。现在耗时读响应帧、
  方法名按 `id` 回请求帧取
- **`diff` 漏报一切参数变化**：`_schema_contract` 把 `properties` 的**键名**当成
  "非契约字段"整个丢掉，`properties` 于是变成空 `dict`——参数增删、改名、改类型
  **全部看不见**，`diff` 就只剩下"工具增删"这点用了。`properties` 的值是一张
  "名字 → 子 schema"的映射，**键名本身携带语义**，必须整张带下去
- **`diff` 渲染把 `detail` 整个丢弃**：原先把它挤在 `subject` 后面，再按剩余宽度
  决定显示还是丢弃。于是长路径配长 `subject` 时，"到底改了什么"那句最关键的信息
  被吞掉，用户只看到 `~ echo.inputSchema`。改成一行不够就换行缩进
- **回放的符号按错了依据**：原先按"这一步成功还是失败"挑箭头，于是 `initialize`
  这个**请求**被画成了 `←`。箭头表达的是**方向**（`→` 客户端发往服务端、
  `←` 服务端返回、`⇢` 单向通知、`✗` 永远等不到回程），不是成败
- **`notifications/initialized` 被误报成"始终没有响应"**：它按规范就没有 `id`、
  也不该有响应。混为一谈会让**每一份正常记录**都带一条"有 1 个未响应的请求"
  ——警告天天响，真的悬空请求反而被淹没
- **`--client` 用了可变默认值**：`cli.py` 里写的是 `typer.Option([], "--client")`，
  那个 `[]` 会在多次调用之间共享；更糟的是它躲在一次函数调用里，ruff 的 B006
  （可变默认值）**看不见它**。参数声明统一改成 `Annotated`——默认值回到签名上，
  读的人一眼看见，检查器也看得见。改完 B006 立刻开始工作，正说明原来那条规则
  是被绕过的，不是不存在
- **`ui.key_value_lines()` 的入参类型漏了 `None`**：函数体里明明写着
  `"" if v is None else str(v)`，类型却声明成 `tuple[str, str]`。是测试传
  `("a", None)` 时被类型检查器抓出来的——注解比实现窄，等于在骗调用方
- **测试夹具把可选参数写成了必填**：`wire_server` / `broken_server` 的返回类型写成
  `Callable[[str], str]`，宣布参数必填；而两个工厂的参数**都有默认值**，
  测试里最常见的 `wire_server()` 反而被判成调用错误。新增 `ServerCommand` 别名说清这件事
- **`call_tool()` / `read_resource()` 的返回注解从未被核对**：`_call()` 给出的是 `Any`，
  顺着公开签名一路漏给下游，声明 `dict[str, Any]` 却没人验过。新增 `_call_object()`
  把"按 MCP 规范 result 是对象"这条事实集中记录一次，而不是在每个调用点各写一遍
- **代理里 `130` 是魔法数字**：`exits.py` 里就有 `EXIT_INTERRUPTED`，`core/proxy.py`
  却两次直接写 `130`。契约有了单一事实源、代码里却还有人绕开它，等于没有
- **`--no-color` 是个幽灵选项**：文档与 `ui/layout.py` / `ui/width.py` 共 3 处引用它，
  CLI 里**根本没有这个选项**。而那两个模块还拿它当"本模块不依赖 Rich"的论据——
  设计理由建立在不存在的东西上。已改为引用真实存在的 `NO_COLOR`（Rich 原生支持）
- **服务端 stderr 的前缀绕过了主题层**：`core/transport.py` 直接拼 `\033[2m`，
  后果是 `NO_COLOR` 管不住它（Rich 认那个环境变量，手写的转义码不认），
  重定向到文件时会留下 `^[[2m` 这样的垃圾。现在这个 handler 住在 `runtime.py`，
  走 `ui.styled_line`——渲染本来就不该是 `core` 的事
- **退出码散落成魔法数字**：`ls.py` / `call.py` 写的是 `return 2` / `3` / `4`，
  而 `check.py` / `watch.py` 用的是常量。这条契约没有单一事实源，
  加命令的人不知道该写几，也没有东西拦得住他写错。现在统一到 `exits.py`
- **配置里的 argv 被拆两次**：`discover` 从配置里拿到的是**已经拆好的**启动参数，
  拼成字符串再让 `split_command` 拆一次，含空格或反斜杠的路径
  （`C:\Program Files\nodejs\node.exe`）就可能被拆坏，而且拆坏了不报错。
  新增 `SessionOptions.argv`，让这条路彻底绕开字符串往返
- **服务端 stdout 关闭与"超时"混为一谈**：`recv()` 两种情况都返回 `None`，
  于是一个启动即崩的服务端要用户干等满整个 `--timeout`（默认 30 秒）才看到一句
  "超时"，而真正的原因（退出码、stderr）早就躺在屏幕上。现在拆开：
  超时照旧，stdout 关闭抛 `ServerGoneError` 并带上退出码——
  `ls` / `call` 立即以 4 退出，`check` 以 2 退出
- **代理渲染时帧与帧交错**：上行泵与下行泵是两条线程，同时往同一个 stderr 写字，
  一帧的头部与载荷之间会插进别的帧，读起来像报文被改过。现在一帧只发一次 `print`，
  且"写出去 + 记下来"整体串行
- **响应抢在引发它的请求前面被记录**：原先先转发、后记账，子进程完全可能在
  你还等着拿锁的时候就答完了。顺序反过来，写与记账放进同一把锁
- **子进程退出后丢最后几帧**：`wait()` 返回即收工，管道里还没搬完的帧跟着消失。
  现在等两条泵（stdout + stderr）搬完，`DRAIN_TIMEOUT` 兜底
- **上游 EOF 没传给子进程 stdin**：子进程一直等输入，`wait()` 永不返回。
  这不是"慢"，是死锁——技术验证阶段实测复现
- **主目录缩写只在一种分隔符下生效**：`expanduser` 在 Windows 上返回反斜杠形式，
  而用户输入常见斜杠形式，只比一种写法会让缩写**安静地失效**
- **耗时永远以毫秒显示**：一场几分钟的 `watch` 会话会被写成 `183472.5 ms`，
  读不出量级。现在按量纲分档，且每档先把值落到将要显示的那一位再定档——
  否则 59.999 s 会写成 `60.00 s`、999.99 ms 会写成 `1000.0 ms`，看起来像漏了进位
- **循环导入导致整个 CLI 无法启动**：`ui/report.py` 引用 `services` 违反了
  「依赖只能向下」，形成 `runtime → ui → services → runtime`。报告渲染移到
  `services/report.py`（`services` 依赖 `ui` 是合法方向）
- **服务端发起的请求被误当成通知**：`_call` 把有 `id` 的请求也收进
  `server_notifications`，且不回复——服务端会一直等它的答复。现在单独记录到
  `server_requests` 并回一条 `-32601`
- **`check` 只探第一个工具**：手写服务端常常是逐工具写参数校验的，只探一个样本
  会漏掉后面所有同类缺陷，用户就得"改一个、跑一次"。现在逐个探测（上限 10 个）
- **`examples/echo_server.py` 缺参数时照常执行**：`echo` 与 `add` 声明了必需参数，
  却在参数缺失时拿默认值把请求跑完——调用方会以为参数生效了。现在回 `-32602`
- **Rich markup 注入**：报文、工具名等外部数据里的 `[` `]` 会被当成样式标签解析。
  所有输出改为构造 `Text` 对象，不再拼接 markup 字符串
- **`--trace` 回显的 markup 注入**：`runtime.py` 曾用 f-string 拼样式标签再插入原始报文，
  含 `[` `]` 的帧会破坏 stderr 输出
- **能力标签重复定义**：`core/session.py` 与 `ui/render.py` 各维护一份能力名映射，
  已合并到 `ServerInfo.describe_capabilities()` 一份
- **未识别的 capability 被丢弃**：能力面板只显示白名单内的能力，
  不在规范里的（如 `experimental`、第三方扩展）会被静默隐藏
- 文案表占位符一致性测试过严：`str.format` 按名字取参，中文语序与英文不同是正常的
  （`{total} 项中有 {failed} 项` vs `{failed} of {total} checks`），改为比较多重集
- `core/transport.py` 的 `__all__` 误把 `typing.Any` 当作公开 API 导出
- 提示块回显完整启动命令导致折成多行：超过 40 列改用 `<SERVER>` 占位符
- 26 处 `ruff check` 告警（`E501` / `F401` / `B008` / `B007` / `I001` / `UP012` / `UP035` / `UP041`）。
  其中 **E501 是显示宽度感知的**：一个中日韩字符算 2 列，含中文的行实际只有约 50 字预算——
  按字符数估算是错的，这条是实测出来的

### 变更
- **CI 拆成三个 job**：`lint`（ruff + mypy，单平台跑一次）、`test`（三平台 × 四版本矩阵）、
  `package`（构建 wheel 并断言 `py.typed` 进包）。原先静态检查挂在矩阵里，
  同一份检查重复 9 遍，还会把"真失败"淹没在 8 个绿里
- **CI 矩阵补上 Python 3.11**：`classifiers` 里声明支持它，却一直没测——声明了不测等于没声明
- **`exits` 从"只有命令层能引用"升级为零依赖词表**：任何层都可以引用它。
  判据不是"它重不重要"，而是"它有没有可能反向依赖"——它内部零导入，
  引用它既不会成环，也不会把上层逻辑渗进下层。`i18n` 同理，两者现在由
  `VOCABULARY` 统一放行，白名单里不再逐层重复登记
- **静态检查全部走配置，命令行不留参数**：`mypy` 不带参数运行，检查哪些路径由
  `pyproject.toml` 的 `files` 决定。本地与 CI 各写一份迟早会漂开
- `watch` 的退出码是**真服务端的退出码**，不是代理自己的——把它自己的状态码盖上去等于撒谎。
  只有用法错误（1）与环境错误（4）例外，那时还没有服务端可以退出
- `watch` 的 stdout 是**协议通道**，横幅、逐帧视图、服务端 stderr 全部走 stderr
- `ProxyEvent` 带 `at_ms`（相对本次代理开始的毫秒数）而不是墙上时钟：
  相对时间才能跨机器比较、才能回放
- 代理的关联表（请求 id → 方法名）有上限（`MAX_PENDING = 512`），
  超限丢最旧并计数——服务端永不回应时内存不能一路涨上去
- 会话层把「服务端主动发来的报文」拆成三类：`non_protocol_output`（非协议输出，
  协议违规）、`server_notifications`（合法通知）、`server_requests`（服务端发起的请求）
- 新增 `core/jsonrpc.encode_error_response()`，用于拒绝服务端发起的不支持请求
- `MCPSession.describe_capabilities()` 移到 `ServerInfo`：能力属于服务端自述，
  与连接状态无关
- 已知能力按固定顺序展示（`CAPABILITY_ORDER`），不再跟随服务端声明顺序——
  `mcpdump ls` 的输出现在可做快照 diff
- 命令 `mcpdump test` 更名为 `mcpdump check`，避免与「运行本项目测试」混淆
- 外部模块统一从 `mcpdump.ui` 导入，不再直接引用子模块
- `Symbols` 未知属性的异常信息改为英文：它出现在 traceback 里，属于面向程序员的
  信息，不进文案表
- **`check` 的对外出口从两个变三个**（`--json` / `--badge` / `--markdown`）。互斥检查
  改为按"选了几个"判，且报错时列出**用户实际传的那几个**——原先写死成"`--badge` 与
  `--json` 不能同时用"的字符串比较，加第三个出口时必然漏改一处，而且用户传的是另外
  两个时会指错

### 测试
- **全量 869 → 910 项**（`910 passed in 679.28s`，2026-09-26 实测）：
  新增 `test_markdown.py` 30 项、`test_action.py` 5 项、`test_check_cli.py` 扩容 6 项
- **测试数 375 → 488**，M7 新增 **110 项**（`test_recorder.py` 25 / `test_replayer.py` 22 /
  `test_diff.py` 37 / `test_record_replay_cli.py` 26）
- 新增 `test_recorder.py`（25 项）：读写往返、header 版本门（**新版本必须拒绝**、
  旧版本要能读）、坏行**报行号与字段名**、`bool` 不被当成 `seq`、
  `iter_frames` 真的是生成器（只持一行）
- 新增 `test_replayer.py`（22 项）：**通知不是悬空请求**、孤儿响应不被丢弃、
  待响应表有上限且**丢最旧的不丢最新的**、两次回放逐字节相同、耗时是复现的
- 新增 `test_diff.py`（37 项）：**参数增删/改名/类型/必填/嵌套/数组项/枚举**
  七种 schema 变化全部断言，外加"不该报的也别报"——`description` 改错别字、
  `required` 顺序、噪声级耗时抖动、方法只在一侧出现
- 新增 `test_record_replay_cli.py`（26 项）：以 `mcpdump ls` 当客户端穿 `record` 录真会话，
  再回放与 diff；`--print-config` 是纯 JSON；确定性在命令层逐字节成立
- 测试数 30 → 375，新增设计系统层、报文流层、渲染原语层、文案表层、代理层、
  发现层、检查层、报告层、架构层
- 新增 `test_discovery.py`（94 项）：**三平台路径全部断言**（`platform` / `environ`
  可注入，Windows 上也能测 macOS 的路径）、四种客户端格式、密钥遮蔽、
  诊断分级、JSONC 解析
- 新增 `test_discover_cli.py`（28 项）：密钥不外泄、非 TTY 不阻塞、`--use` 直连、
  歧义名字被拒绝而不是猜、退出码落在契约内
- 新增 `test_architecture.py`（15 项）：**用 AST 把文档里的铁律变成会失败的测试**——
  依赖白名单、运行时无环、`core` 不引用 `ui`、`ui` 不引用 `services`、
  `i18n` / `exits` 是叶子、无硬编码 ANSI、无硬编码用户文案、退出码无魔法数字
- 新增 `test_render.py`（20 项）：逐档钉死耗时量纲的切换边界——1 s 以下保持毫秒、
  秒档保留两位小数、分钟档保留一位、小时档丢掉秒；每一档都断言进位正确，
  不允许出现 `60.00 s` 或 `1000.0 ms`
- 新增 `test_proxy.py`（13 项）：字节保真（输出与输入**完全相等**）、
  因果顺序（响应不可能先于引发它的请求）、渲染失败不中断转发、关联表有上限、
  上游 EOF 不死锁、子进程退出不丢帧
- 新增 `test_watch_cli.py`（12 项）：stdout 上只有协议帧、人看的全在 stderr、
  真 mcpdump 会话穿过代理、`--record` 产物可解析、`--print-config` 是纯 JSON、
  退出码跟随服务端
- 新增 `test_runtime.py`（3 项）：主目录缩写在两种分隔符下都生效
- 新增 `test_checks.py`（20 项）：基线全过 ＋ 每个 fault 只打中对应检查项
- 新增 `test_report.py`（12 项）：报告行数、证据截断与展平、双语
- 新增 `test_check_cli.py`（18 项）：退出码、`--json` 可解析、`--badge` 无转义序列、
  三个出口互斥且只报用户传的那几个、`--markdown` 在子进程里的字节形态
- 新增 `test_markdown.py`（30 项）：围栏长度随内容里的反引号变长、`<details>` 折叠、
  三种状态前缀、失败项顺序、末尾恰好一个换行、块之间恰好空一行、
  **不受终端宽度影响**（300 字符的证据必须原样出现）、
  **服务端自述里的 Markdown 元字符被转义**（一个叫 `a*b` 的服务端不该把整行变成斜体）、
  三处 `<details>` 前面都空了一行（紧贴上一行会被并进去，折叠块直接失效）。
  测试助手靠 `<details>` 块定位围栏而不是"整行都是反引号"——证据本身就可能是一整行
  反引号，那样认出来的头尾全是证据，围栏反而漏掉
- 新增 `test_action.py`（5 项）：`action.yml` 传的每个开关都能在 `check --help` 里找到、
  装的包名取自 `pyproject.toml`（**改名最容易漏的就是这种不在 import 图里的文件**）、
  两份 README 都写了 Action
- **CI 新增 `action.yml` job**：用 `uses: ./` 把 Action 在 GitHub 上真跑一遍，
  断言 outputs、报告文件与 job summary。`install: false` 让它走工作树里的版本——
  不关掉的话它会去 PyPI 装一个还没发布的包，这个 job 在首个版本发布前必然红
- 新增断言：全角 / 组合符 / emoji / ANSI 宽度、截断预算、markup 安全
- **测试语言显式钉死**：进程内用夹具切英文，子进程用 `MCPDUMP_LANG`。
  断言不再随开发机 locale 变化（此前中文断言会在英文环境下必然失败）
- 新增：`MCPDUMP_LANG=zh` 端到端中文输出、`--help` 随语言切换、
  文案表占位符跨语言一致性、AST 检测重复文案键

### 已知限制
- README 演示 GIF 尚未录制
- 超长启动命令在能力面板中仍会被折叠（已用 `~` 缩写缓解）

### 计划中
- 文档站上线，含完整示例与真实输出（v1.0）
- `mcpdump tui` 的大流量压测：10,000 帧报文流下的内存占用与帧率（v1.0）

[0.1.0]: https://github.com/xsw77492-code/mcpdump/releases/tag/v0.1.0
