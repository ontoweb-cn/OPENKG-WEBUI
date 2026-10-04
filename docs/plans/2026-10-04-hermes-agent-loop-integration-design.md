# Hermes 作为 Agent Loop 的接入方案（自动识别 + 双传输）

日期：2026-10-04
状态：**v1.2 已实施**（2026-10-04；§7.1 十步全部落地，全量测试通过；评审记录见 §10）
上游依赖：hermes-agent v0.21.5+6751.g9cf7960（2026.9.24，canary）实测

## 0. 结论摘要

把本机 Hermes 接入 OPENKG-WebUI 作为 agent loop **不需要改任何 backend 代码**——
openkg-webui 现有的 `AcpAgentLoopBackend` 与 `RunsAgentLoopBackend` 经实测可
直接驱动 Hermes（含流式文本、thinking、工具调用、**审批往返**）。本次工作范围
全部在**配置面与检测面**：

1. `hermes` preset 从 HTTP stub 升级为**双传输**：本地 ACP（默认）+ 远端
   gateway `/v1/runs`；
2. ACP 传输加 **`native_resume=False` 保护**，绕过 hermes 上游的会话恢复
   丢凭据 bug（该 bug 已立案且修复 PR 停滞，见 §9）；
3. 检测器加 **well-known 路径回退**（`~/.local/bin` 等），因为本机 hermes
   装在 venv/托管运行时里、服务进程的 PATH 不保证包含；
4. 设置页在检测到非 PATH 安装时，**一键添加 profile 自动预填绝对路径**。

不做的：不改 hermes 代码（上游 bug 走上游流程）；不新增 backend 家族；不引入
第三种传输（`hermes serve` WebSocket 能力重叠且更重）。

## 1. 本机环境事实（2026-10-04 实测）

| 事实 | 值 |
| --- | --- |
| 版本 | `hermes --version` → v0.21.5+6751.g9cf7960 (2026.9.24)，落后 origin/main 80 提交 |
| 安装形态 | 入口 `~/.local/bin/{hermes,hermes-acp}` → 执行 `~/workspace/hermes-agent` 源码树（托管 Python 3.14.7，`~/.hermes/tools/`） |
| 旧安装 | `~/.hermes/hermes-agent/venv/`（2026-05-17 的快照，已过时但仍在磁盘） |
| ACP SDK | `agent-client-protocol==0.9.0`（hermes 侧的 pin，更新前后未变；**不是** hermes 的版本号） |
| OPENKG 客户端 SDK | `agent-client-protocol==0.12.1`（`pyproject.toml` 的 `[acp]` extra），跨版本互通已实测 |
| provider 配置 | `model.provider: dee-seek`（命名 custom provider，`base_url: https://api.deepseek.com/anthropic`，`api_mode: anthropic_messages`）|
| gateway 配置 | `gateway.multiplex_profiles: true`（影响 api_server 的启用方式，见 §5.7） |

## 2. 实测证据（E2E，均在本机跑通）

探针脚本位于 `/tmp/hermes_acp_probe*.py`、`/tmp/hermes_openkg_backend_e2e.py`、
`/tmp/hermes_runs_e2e.py`、`/tmp/hermes_runs_approval_e2e.py`（建议实施时收入
`tests/` 或 `scripts/`，见 §7）。

### 2.1 ACP 传输（openkg `AcpAgentLoopBackend` ⇆ `hermes-acp`）

- 握手（2.7s）、`session/new`、真实模型对话、`thinking` 流、工具调用
  （`ToolCallStart/Progress` → `tool_call`/`tool_result`）全部正常。
- **审批往返**：`rm -rf ~/...` → `session/request_permission`（5 个选项：
  `allow_once`/`allow_session`/`allow_always`/`deny`/`deny_always`）→
  `respond_approval("once")` → 工具执行 → 回合完成。
- 新版新特性：会话 `modes`（default/accept_edits/dont_ask）开始通告；新增
  `SessionInfoUpdate` 事件（openkg 目前静默忽略，无影响）。
- **跨进程恢复**：`session/load` 回放成功，但恢复后的下一轮对话**失败**
  （详见 §8 已知问题 1）——因此 ACP 传输必须加 `native_resume=False`。

### 2.2 HTTP `/v1/runs` 传输（openkg `RunsAgentLoopBackend` ⇆ `hermes gateway run`）

- `POST /v1/runs`（202 + run_id）→ `GET /v1/runs/{id}/events`（SSE）→
  openkg 翻译为 `thinking`/`content`/`usage`，回合正常收尾。
- SSE 原始事件：`message.delta`（delta 分片）、`reasoning.available`、
  `run.completed`（`output` + `usage` + `runtime.{provider,model}`），字段与
  openkg `http_backend` 的 runs 变体逐一对齐。
- **审批往返**：`tool_call` → `approval_request` → `respond_approval`
  （POST `/v1/runs/{id}/approval` `{"choice":"once"}`）→ `tool_result` →
  `content` → `usage`，全链路跑通。
- **per-turn 模型生效**：请求体带 `"model":"deepseek-v4-flash"` 时
  `runtime.model` 确实切换；**无效模型 id 会让整轮失败**（provider 明确
  400，错误可见于 run 状态）——因此 HTTP 传输声明 `per_turn_model=True`，
  但 composer 的候选列表必须由 profile 的 `models` 字段收敛（§5.1）。
- `/health` 不鉴权（200 探针可用）；端口默认 8642；`API_SERVER_KEY` ≥16 字符。

### 2.3 能力矩阵（openkg 视角）

| 能力 | ACP（本地） | HTTP runs（远端） | 说明 |
| --- | --- | --- | --- |
| 流式文本 | ✅ | ✅ | |
| thinking | ✅ 逐段 | ✅ 单段（`reasoning.available` 是**截断预览**：hermes 侧 `_think_text[:500]`） | |
| 工具调用/结果 | ✅ | ✅ | |
| 审批卡 | ✅（wire 5 选项；卡片按 openkg 词汇给 4 项） | ✅（4 选项） | `respond_approval` 双通道实测；选择保真度见 §8-5 |
| clarify 问答卡 | ❌ | ❌ | **hermes 有意排除**：`hermes-acp`/`hermes-api-server` 工具集注释明确"无交互式 clarify UI"（`toolsets.py`），非缺陷 |
| per-turn 模型 | ❌ 静默无效（§8-2） | ✅（无效 id 明确失败） | ACP 传输声明 `per_turn_model=False` |
| 会话原生续接 | ⚠️ 上游 bug（§8-1） | ✅ 不受影响（**已核验**）：runs 每次全新构建 agent、调用方 history 权威、不读持久化 provider，不会走 ACP 的 `_restore` 凭据路径 | ACP 传输禁用原生续接（§5.3）；runs 由 openkg 内联 history 承担连续性 |
| 审批超时 | hermes `approvals.timeout`（本机 60s，默认 300s）先到先 deny；openkg 侧 park 窗口另算 → 跨系统超时矛盾，见 §8-4 | 同左（超时后 approval POST 返回 409） | |

## 3. 目标与非目标

**目标**：本机/同主机 Hermes 能被检测、一键配置、作为 primary agent loop 驱动
turn；危险的终端命令能走 openkg 审批卡；模型选择在 HTTP 传输上可用。

**非目标**：hermes 上游 bug 的修复（走 issue/PR）；hermes 原生会话记忆的
完整保留（用折叠兜底）；clarify 支持（上游有意不支持）。

## 4. 现状缺口（openkg 侧）

1. `hermes` preset 是**单传输 HTTP stub**（`builtin.py`，family=http、
   `turn_path=/agent/turn`、无 `probe_url`）——既没有本地形态，检测也永远
   探不到（HTTP profile 需手填 URL）。
2. `detect_cli` 只做 `shutil.which`——本机 `hermes-acp` 在服务进程 PATH 未必
   出现（实测 ZCode shell 里在 `~/.local/bin`，但服务化部署不保证），会误判
   "未安装"。
3. ACP 会话恢复路径的 hermes bug 会污染 openkg 的默认行为（空闲回收 600s
   后重挂、openkg 重启后重挂），必须隔离。

## 5. 方案

### 5.1 preset 改造（`openkg_webui/services/agent_loop/builtin.py`）

`hermes` 改为双传输（照 `intellect` 先例），ACP 为默认：

```python
AgentLoopPreset(
    name="hermes",
    family="cli",
    default_transport="acp",
    description="Nous Research Hermes: a local `hermes-acp` child, or a remote gateway /v1/runs service.",
    transports=(
        AgentLoopTransport(
            id="acp", family="cli", label="Local CLI (ACP)",
            description="Runs `hermes-acp` on this host: streaming text, thinking, tool calls "
                        "and approvals. The loop runs as a local process with the server's "
                        "privileges. Answer approvals promptly: Hermes denies pending "
                        "requests after its own approvals.timeout (300s by default). "
                        "Per-turn model selection is not available on this "
                        "transport; set the model with `hermes model`. After the agent "
                        "process restarts, the conversation continues from OPENKG-WebUI's "
                        "bounded history fold.",
            command="hermes-acp",
            translator="",            # ACP 传输自带翻译
            cli_transport="acp",
            per_turn_model=False,      # §8-2：显式声明不支持
            native_resume=False,       # §5.3：新字段，绕过 §8-1
            # 检测回退路径（新字段，见 §5.4）；顺序=优先级
            command_paths=(
                "~/.local/bin/hermes-acp",          # 现行安装器/updater 的 launcher 位置
                "/usr/local/bin/hermes-acp",
                "$HERMES_HOME/hermes-agent/venv/bin/hermes-acp",  # 旧源码安装；$HERMES_HOME 未设时按 ~/.hermes 展开
            ),
        ),
        AgentLoopTransport(
            id="http", family="http", label="HTTP service (/v1/runs)",
            description="Hermes gateway api_server (API_SERVER_ENABLED + API_SERVER_KEY in "
                        "~/.hermes/.env). Supports per-turn model, approvals and cancellation.",
            turn_path="/v1/runs",
            protocol="runs",
            probe_url="http://127.0.0.1:8642/health",
            per_turn_model=True,
        ),
    ),
)
```

新增两个 transport 字段（默认值保持现状、零行为变化）：

| 字段 | 默认 | 语义 |
| --- | --- | --- |
| `native_resume: bool` | `True` | ACP 传输是否使用 agent 原生会话续接（`load_session` + `acp_session_store`）。False = 每次 spawn 开新会话、靠 G-1 折叠兜底（§5.3） |
| `command_paths: tuple[str, ...]` | `()` | CLI 检测的 well-known 回退路径（`~`/`$HERMES_HOME` 展开；仅检测/预填用，§5.4） |

实现注意（评审发现）：`_transport_from_preset`（单传输 preset 的合成器）**需要同步拷贝新字段**，否则未来某个单传输 preset 用上这两个字段时会静默丢失；两侧（`AgentLoopPreset` 与 `AgentLoopTransport`）都加，或合成器显式转发。

### 5.2 迁移：存量 `hermes` profile

存量 profile 可能已把 `hermes` preset 配成 HTTP 服务（family=http、
`/agent/turn`）。改造后其 `transport=""` 会解析到默认传输 `acp` → 行为漂移。
照 `intellect` 先例（`runtime_settings.py` 的 `_normalize_agent_loop_profile`）：
**`preset=="hermes"` 且配了 `url` 且未显式选 `transport` → 重写为
`custom-http`**，保持旧行为逐位一致（custom-http 就是"任何说 turn 契约的服务"
的归属，正是旧 hermes stub 的语义）。

**评审发现：迁移必须覆盖三条入口，只在 `_normalize_agent_loop_profile` 里加会漏两条**（HIGH）：

1. **落盘/保存路径**（`_normalize_agent_loop_profile`）：按上述先例即可。
2. **环境变量覆盖路径**（`_apply_agent_loop_env_overrides`，`runtime_settings.py` ~984-1042；二轮更正函数名）：
   该路径先 `_normalize_agent_loop_profile` 把 `transport` 解析成 `"acp"`，
   随后 `not transport` 的守卫不再成立——`OPENKG_WEBUI_AGENT_LOOP_BACKEND=hermes`
   + `..._URL=…` 会静默变成本地 ACP。修复：在原始读取点之后、字段赋值之前，
   对**原始三元组**重写（`backend=="hermes" and url and not transport_env →
   backend="custom-http"`）。
3. **设置 API 的 PUT 路径**（`_agent_loop_profile_block`，`api/routers/settings.py` ~1053-1066）：
   它先 `profile_transport_id("hermes","")` 把空 transport 解析成 `"acp"`，
   旧客户端重放 `{preset:"hermes", url}` 会直接存成 ACP。修复：同样在原始
   payload 上先做重写（新 UI 总是显式带 transport，只影响历史客户端）。

**评审发现（MEDIUM）**：`_reject_unauthorized_workdirs`（`api/routers/settings.py`
~1256-1261）用的是 preset 的**默认 family**（`preset.family`）而不是
`preset_family(preset, transport)` 解析结果；hermes 默认 family=cli 后，一个
显式 `transport=http` 且带越界 workdir 的 profile 会在保存时被误拒。此缺陷在
`intellect` 上已存在，hermes 改造会扩大它——顺手修为 transport-aware（加一条
`hermes:http` 带越界 workdir 的用例）。

### 5.3 `native_resume` 保护（`acp_backend.py`）

动机：hermes 的 ACP 恢复路径有上游 bug（§8-1）——`load_session` 之后的新回合
必失败（凭据解析错 → HTTP 404）。openkg 无法在 turn 前探测该故障，因此对
hermes 的 ACP 传输**默认关闭原生续接**：

- `AcpAgentLoopBackend.__init__` 增加 `native_resume: bool = True`；
- `False` 时：`ensure()` 不读 `acp_session_store`（磁盘重挂禁用）、
  `_spawn_session` 不写 store、崩溃重挂不携带 `previous_id`——每次 spawn 都是
  `new_session` + `session_reset=True`；
- 既有 G-1 折叠机制自动接管：openkg 把最近 30 条/12k 字符的历史折进首个
  prompt（`_fold_history_for_reset`），并给出一行 "session was reset" 进度
  提示。**行为等价于"无原生 resume 的 CLI 传输"**，确定性、可测试。
- 权衡：hermes 丢失自己的压缩链/记忆（它只能看到折叠尾巴）；完整转录仍在
  工作区文件（L0，`session_workspace` 开启时由 manifest 指向）。
- 空闲回收（默认 600s）与 openkg 重启都会触发新会话——这正是保护的目的：
  让这两条路径退化为"折叠后继续对话"而不是"永久失灵"。
- 上游修复落地（PR #74648 类）后，把该传输的 `native_resume` 翻回 `True`
  即可恢复原生续接（一行改动 + 回归）。

**评审发现（BLOCKER，须先修再启用本保护）**：`_fold_history_for_reset`
（`acp_backend.py` ~89-109）取 `history[-30:]` 后**从最旧端累加、到 12k 字符
截断**——超限时丢弃的是**最新**轮次（复核：40 条 ×900 字符，保留 m10–m22，
最新 m23–m39 全部丢失），而 header 文案却写 "most recent last"。修复算法
（二轮定案）：取 `history[-30:]` 的可用项，**倒序（新→旧）**累加直到 12k
预算，收集结果再反转输出（最终顺序仍旧→新）；**最新一条必须保留**（哪怕
单独超预算，截其内容至预算内）；上限维持 30 条/12k 字符。header 保留
`[Conversation context — ` 前缀（既有测试 `test_acp_backend.py` ~:887 断言
该前缀，**不在**破坏清单里，不能动）；对 `session-transcript.md` 的无条件
宣称改为**条件参数**：`_fold_history_for_reset(history, transcript_note="")`
新增可选参数，`run()` 在会话工作区确有转录文件时传入一句说明（每次 reset
一次 stat），否则 header 不提文件。加单测：超预算时最新一条必须保留。
（`cli_backend._prompt_with_history` 的折叠由 ContextBuilder 上游预算，不属
本次修复面。）

**评审发现（MEDIUM，实现方式）**：`ensure()` 属于 `AcpSessionManager`
（模块级单例、按 command+env 哈希分组），`native_resume` 不能只放在
`AcpAgentLoopBackend.__init__`——需一并传入 manager（构造参数或
`set_spawner` 同级的 setter），并让崩溃重挂的 `previous_id` 携带逻辑同查该
标志。否则"不读 store"这一步没有落点。当前单例按 command+env 分组，同
command 的两份 backend 无法区分标志——文档如实标注：**标志以 transport 声明
为准，同一 command 只应有一种续接策略**。

**评审发现（MEDIUM，已知限制）**：consult 回合构造的请求**不带 history**
（`capability.py` ~605-612），`native_resume=False` 下每次重挂折叠为空 →
consult 会话跨回合失忆且不显示任何提示。取舍：要么给 consult 补一段有界
QA 转录，要么在 §8 明确"consult 在重挂/回收后无上下文"（当前建议后者，
consult 本身有预算上限、影响面小）。

### 5.4 检测（`detect.py`）

- `detect_cli` 在 `shutil.which` 失败后，依次探测 `command_paths`；命中即
  `available=True`，`detail` 返回**解析到的绝对路径**（设置页用它预填
  `command`）。
- **评审修正（MEDIUM）：`detail` 不能作为"是否经由回退命中"的判据**——现有
  `detect_cli` 对所有 PATH 命中本来就返回绝对路径（claude/codex 同理）。
  给 `DetectResult` 增加结构化字段（`path: str`、`via_fallback: bool`），
  预填逻辑读 `via_fallback`（§5.5），不解析 `detail` 文案。
- **评审修正（MEDIUM）：profile 级探测也要享受回退**——`detect.py` 的
  profile 分支（~135-139）只在 profile 自带 `command` 时探测；command 为空
  （回退到 transport 默认）的已存 hermes ACP profile 需要把该 transport 的
  `command_paths` 一起传入，否则出现"preset 芯片亮、profile 卡片黑"的错位。
  显式配置的 profile command 保持权威。
- **评审修正（MEDIUM）：Test 端点自己先做裸 PATH 探测**（`settings.py`
  ~1365-1378），在到达 `probe()` 握手前就可能报 "not found on the server
  PATH"。两处统一：Test 端点同样走 transport 回退解析（或明确依赖预填的
  绝对路径已存入 `command`——建议前者，行为自洽）。
- **评审修正（BLOCKER 降级为 MEDIUM，处置）**：`command_paths` 里保留
  旧源码安装路径（`$HERMES_HOME/hermes-agent/venv/bin/hermes-acp`）但置于
  **最末位**，且 `$HERMES_HOME` 未设时按 `~/.hermes` 展开而不是留字面量；
  命中失败（不存在/不可执行）静默跳过。理由：本机该路径存在但是 2026-05-17
  的**过时快照**——顺序保证现行 `~/.local/bin` launcher 优先命中；只有在
  没有现行安装的机器上才会落到它（那时它确实是唯一可用安装）。范围说明：
  Windows（`%LOCALAPPDATA%\hermes\bin`）与 Termux（`$PREFIX/bin`）本期不入
  列表，文档标注为"检测范围=POSIX 常见安装位"。
- 判决函数用 `os.access(p, os.X_OK)`（对齐 `shutil.which` 语义），不仅
  `isfile`。
- 保持"页面加载探测零副作用"原则：回退探测只是 stat，不起进程；权威校验
  （ACP 握手）仍留给显式 Test 按钮。
- HTTP 传输的 `probe_url`（`:8642/health`，不鉴权）让 preset 卡片在未配置
  profile 时也能亮"本地可达"（前端渲染前提见 §5.5 的修正）。

### 5.5 设置页预填（`AgentLoopSettingsSection.tsx`）

现有管线已就绪：挂载即调 `/detect`、`addProfile(preset, transportId)` 支持
多传输、`DraftProfile.command` 进保存 payload。增强两点：

1. **预填**：`addProfile` 选中 transport 后，按 `chosenTransport.detect_key`
   查 `detects`；命中且 `via_fallback` 为真时，把 `path` 预填进 `draft.command`
   （用户可改；显式配置优先）。
2. **评审修正（MEDIUM）：`hermes:http` 徽章当前不会渲染**——preset 选择器
   的 transport 徽章只画 `family === "cli"` 的选项（~1436-1439），"Local
   detection" 芯片也过滤 CLI。若保留"http 可达性芯片"的产品陈述，需要在
   前端放行 http transport 的 detect 条目（`entry.detect_key` 渲染，仅本地
   URL 时展示）；否则把该陈述从文档与描述中删掉。建议前者（信息有用、
   改动小）。

### 5.6 自动接管

复用既有 `_auto_primary_agent_loop`：hermes ACP profile 是本地 cli family，
在无 Intellect profile 的机器上会被"任意本地 profile"档选中成为 primary。
不改优先级、不做静默自动建档（配置可审计；"检测到 → 一键添加"是正确程度）。

**评审提示（LOW）**：hermes 获得本地 cli 传输后，hermes profile 的 turn 会走
`agent_loop_cli` 授权维度（request_preparer 按 family 解析）——运维在新
primary 生效前需确认该 grant 已按部署策略开放（多用户部署下 CLI family 的
授权语义与 HTTP family 不同）。

### 5.7 运维启用步骤（HTTP 传输，实测口径）

1. `~/.hermes/.env` 写入：
   ```
   API_SERVER_ENABLED=true
   API_SERVER_KEY=<≥16 字符>
   ```
2. `hermes gateway run`（或 `hermes gateway start` 装服务）；
3. openkg profile：`url=http://127.0.0.1:8642`、`api_key=<同一 key>`、
   `transport=http`；`models` 尽力收敛为 hermes 可服务的 id 列表。

> **实测注意（评审修正）**：本机 `gateway.multiplex_profiles: true` 下，
> `API_SERVER_ENABLED/HOST/PORT/CORS_ORIGINS` 是**全局** env（作用域内仍读
> 进程环境），而 **`API_SERVER_KEY` 是 profile-scoped**——multiplex 的主
> profile 配置在作用域内重载时，作用域内**拿不到** shell export 的 KEY
> （这正是本机实测里"export 了却不起"的直接原因；ENABLED 单独 export 是
> 可见的，只有 KEY 缺失）。`~/.hermes/.env` 是默认根的作用域秘密源，两者
> 写进去都生效。因此统一按 .env 方式操作，不要 export。

## 6. 测试计划

**单元（沿用既有测试文件）**：

| 用例 | 文件 |
| --- | --- |
| hermes preset：双传输、默认 acp、`per_turn_model`/`native_resume`/`probe_url`/`turn_path` 各就各位 | `tests/services/agent_loop/test_backends.py`（照 `test_community_intellect_*` 先例，:493-544）|
| hermes ACP 后端构建时 `native_resume=False` 透传；HTTP 构建为 runs 变体 | 同上 + `__init__.py` 工厂 |
| ACP `native_resume=False`：不读/不写 `acp_session_store`、无 `load_session` 调用、`session_reset` 触发折叠（fake spawner） | `tests/services/agent_loop/test_acp_backend.py`（既有 resume 用例所在，:309-420） |
| **折叠修复单测**：超 12k/30 条预算时，**最新**一条必须保留（§5.3 的 blocker） | 同上 |
| 迁移：`hermes`+url+无 transport → `custom-http`；显式 `transport=acp/http` 不被改写；**env 覆盖路径与 PUT 路径同样迁移**（§5.2 三入口） | `tests/services/config/test_agent_loop_settings.py`（先例 :249-284）+ `tests/api/test_agent_loop_settings_router.py` |
| 检测：`command_paths` 回退命中 → `available` + `via_fallback`/`path` 结构化字段；`$HERMES_HOME` 未设按 `~/.hermes` 展开；不可执行文件不算命中 | detect 单测（新文件或并入 router 测试） |
| 检测 payload：`hermes:acp`/`hermes:http` 两个 detect_key 都在 | 同上 |
| workdir 门禁 transport-aware：`hermes:http` + 越界 workdir 按 HTTP family 放行（§5.2 修正） | `tests/api/test_agent_loop_settings_router.py` |
| §8-5 审批选择保真：`_permission_response` 按 option_id 优先；once/session/always/deny 四种选择各一用例（防选项重排） | `tests/services/agent_loop/test_acp_backend.py` |
| §8-4 迟到应答可见性：`resolve_pending` 返回 False → `respond_approval` 上报未生效；能力层补发提示；runs 409 → 同样上报 | 同上 + `tests/capabilities/` 既有审批用例旁 |

**既有用例的预期破坏清单（评审发现，务必一并更新）**：

| 文件:行 | 现状 | 改为 |
| --- | --- | --- |
| `tests/services/agent_loop/test_settings.py:20` | `profile_family({"preset":"hermes"}) == "http"` | `"cli"`（默认传输变了）|
| `tests/services/agent_loop/test_backends.py:1003-1004` | hermes + `models` → `per_turn_model` True | False（ACP 默认；HTTP 用例显式 pin transport）|
| `tests/services/agent_loop/test_backends.py:1078,1092` | `{"backend":"hermes","url":…}` 构建 HTTP backend | 显式 `transport:"http"`，否则会静默变 ACP |
| `tests/services/session/test_turn_access_gate.py:57-66, 391-402` | hermes 走 `agent_loop` 授权 | 本地传输走 `agent_loop_cli` |
| `tests/api/test_agent_loop_settings_router.py:243-279` | `len(http) == 3` | 4（新增 `hermes:http` 探针）|
| `tests/api/test_agent_loop_settings_router.py:336-355` | PUT `{preset:"hermes", url, workdir:"../outside"}` 期望 200 | 迁移修正后仍 200（或按新语义断言）|
| `tests/api/test_agent_loop_settings_router.py:242-279`（二轮补） | fixture 的无 URL hermes profile"不被探测" | 改造后它按 CLI 探测（transport 解析为 acp），cli 结果集与注释都要更新 |
| `tests/services/agent_loop/test_acp_backend.py` ~:887（二轮补） | 断言折叠 header 含 `"Conversation context"` | **保留前缀**即可不破——header 只改 transcript 宣称部分 |
| `tests/capabilities/test_chat_capability.py` ~:670（二轮补） | `_ControlledBackend.respond_approval` 返回 None | 布尔契约后需 `return True`，否则误触发迟到提示 |
| `tests/services/config/test_agent_loop_settings.py:133-172` | env 覆盖测试只断言 preset/url | 增加 transport/family 断言 |
| `web/contracts/generated/api.ts` | — | **无需改动**（agent-loop 路由是无类型 dict，未进 OpenAPI schema）|

**集成（脚本化回归，建议收入仓库 `scripts/` 或 `tests/integration/`）**：

- `/tmp/hermes_acp_probe.py`（握手+对话）、`probe2`（跨进程恢复——加了保护后
  应表现为"新会话+折叠继续"，而不是 404）；
- `/tmp/hermes_openkg_backend_e2e.py`（AcpAgentLoopBackend：对话+审批）；
- `/tmp/hermes_runs_e2e.py` + `/tmp/hermes_runs_approval_e2e.py`（Runs 后端：
  对话+模型切换+审批）。

**手工**：设置页 detect 徽章（PATH 内/外两种场景）→ 一键添加（预填路径）→
Test 按钮（ACP 握手）→ 真实 turn（审批卡、停一轮、模型选择在 http 传输）。

## 7. 实施清单（文件级）

| 文件 | 改动 |
| --- | --- |
| `openkg_webui/services/agent_loop/builtin.py` | hermes preset 双传输；`native_resume`、`command_paths` 字段（两侧 dataclass + `_transport_from_preset` 转发）；模块 docstring :15-17 的"Named HTTP presets"表述 |
| `openkg_webui/services/agent_loop/detect.py` | `command_paths` 回退探测；`DetectResult` 增加 `path`/`via_fallback`；profile 级探测同样走回退 |
| `openkg_webui/services/agent_loop/__init__.py` | 工厂把 `native_resume` 透传给 `AcpAgentLoopBackend`（:80-88） |
| `openkg_webui/services/agent_loop/acp_backend.py` | `native_resume` 保护（经 manager 落地，§5.3）；**`_fold_history_for_reset` 近端优先修复** + header 文案如实化；`_permission_response` option_id 优先（§8-5）；`respond_approval` 上报未生效（§8-4） |
| `openkg_webui/services/agent_loop/http_backend.py` | runs `respond_approval` 检查 409/404 并上报未生效（§8-4） |
| `openkg_webui/capabilities/chat/capability.py` | 应答路径收到"未生效"结果时，**同一个** decision 事件换文案（携带卡片元数据，先 await 再发，§8-4） |
| `openkg_webui/services/i18n.py` | 新键 `agent_loop.approval_decision_ignored`（en/zh）；`agent_loop.session_reset` 文案从"重新附着"改为"折叠续写"（二轮补） |
| `openkg_webui/services/config/runtime_settings.py` | 迁移三入口（落盘 :1442-1447、env 覆盖 :1012-1030、PUT 路径在 settings.py） |
| `openkg_webui/api/routers/settings.py` | `_agent_loop_profile_block` 迁移；`_reject_unauthorized_workdirs` transport-aware（:1256-1261）；Test 端点回退解析（:1365-1378） |
| `web/features/settings/sections/AgentLoopSettingsSection.tsx` | `DetectInfo` 类型加 `path`/`via_fallback`；预填 `via_fallback` 路径；http transport 的 detect 徽章放行（仅 `local` 时，:1436-1439 附近） |
| `tests/...`（§6 两张表） | 新用例 + 既有破坏清单 |
| **文档更新**（评审补全）：`ARCHITECTURE.md` :27 / :89 / :323；`docs/backend-architecture.md` :200；`docs/backend-llm-deployment.md` :740 / :744；`docs/knowledge-center-port-design.md` :134 / :278；`web/locales/zh/app.json` :55 / :558；`web/features/settings/navigation/settings-nav.ts` :302 | hermes 从"HTTP agent 服务"改为双传输描述 |
| `docs/plans/2026-10-04-hermes-agent-loop-integration-design.md` | 本文档 |

### 7.1 实施顺序（二轮评审定案；迁移必须先于 preset 翻转）

1. **折叠修复**（`_fold_history_for_reset` 近端优先 + `transcript_note` 参数）+ 单测——纯函数，对现行 resume 流零影响；
2. **传输字段**（`builtin.py`：`native_resume`/`command_paths` 两侧 dataclass + `_transport_from_preset` 转发 + docstring）+ preset 单测；
3. **迁移三入口**（`_normalize_agent_loop_profile`、`_apply_agent_loop_env_overrides`、`_agent_loop_profile_block`——PUT 与 Test 端点共用后者）+ **workdir 门禁 transport-aware** + 配置/router 测试——此步对现行单传输 hermes 是行为等价改造，必须先于第 5 步落地（`load_system` 每次读取都规范化，翻转后存量 `hermes`+url profile 会在首次读取时被重写为 ACP）；
4. **`native_resume` 接线**（manager 属性 + backend `__init__` kwarg + 工厂透传 + `ensure`/`_spawn_session` 守卫）+ ACP 测试；
5. **hermes preset 翻转**（双传输、默认 acp）+ §6 破坏清单清账；
6. **检测器**（`DetectResult.path`/`via_fallback`、`detect_cli` 回退 + 展开、profile 级回退、Test 端点统一）+ 检测测试；
7. **`respond_approval` 布尔契约**（基类 + ACP deadline/pending 结清 + runs 409/404 + capability 换文案 + i18n en/zh + `_ControlledBackend` 修正）+ §8-4 测试；
8. **`_permission_response` option_id 映射表** + 四选择保真测试（可与 7 并行）；
9. **前端**（`DetectInfo` 类型、`addProfile` 预填、http 徽章放行 `detects[key]?.local` 条件）——依赖 5、6；
10. **文档/locale**（§7 表所列 9 处 + 设置页内联英文文案）+ `/tmp` 探针脚本收入 `scripts/`。

## 8. 已知问题（hermes 侧）与缓解

**1. ACP 会话恢复丢凭据（上游 bug，未修）**
- 现象：`load_session` 成功后，下一轮失败（v0.21.5 上表现为 HTTP 404）。
- 根因链：`_persist` 把 `agent.provider`（已被归一化为裸 `"custom"`）写入
  `model_config` → `_restore`/`_make_agent` 用 `"custom"` 重新解析 → 
  `resolve_runtime_provider` 抛 `AuthError`（`_raise_for_credentialless_bare_custom`）
  → 异常被 `resolve_error` 分支吞掉，agent 仍被构建 → 丢失 `api_mode`，
  请求打到错误协议端点 → 404。
- 上游状态：issue #63681 / #74628（open）；修复 PR #74648（open，2026-08-07
  起未更新）。已在 #74628 补充 canary 实测证据（见 §9）。
- 修复工具已存在于上游：`canonical_custom_identity(base_url=…, model=…)` 能把
  `"custom"` 修复为 `"custom:dee-seek"` 并取回完整凭据（TUI 路径已这么用，
  ACP 路径未接）。
- openkg 缓解：§5.3 的 `native_resume=False`；**不做**版本嗅探（行为差异
  按传输声明，不按版本号猜测）。
- **评审核验补充**：该 bug **只影响 ACP 通道**。runs 通道每次运行全新构建
  agent（`_create_agent` 从当前配置解析 provider），调用方 `conversation_history`
  为权威、命中时跳过 SessionDB 加载；持久化 `model_config.provider` 的恢复
  路径（`requested_provider="custom"` → `AuthError`）只存在于 ACP
  `_restore`/`_make_agent` 与 session-chat 端点。因此 HTTP 传输无需同款保护。

**2. ACP 模型切换静默无效（上游 bug，未修）**
- `set_config_option("model", …)` 返回成功但只写 `state.config_options` 字典，
  不切换模型；真正的切换走 `session/set_model`，而 openkg 客户端 SDK
  （0.12.1）未暴露该方法（只有 `_` 前缀扩展方法可达）。
- 上游状态：issue #105383（模型选择器走 configOptions，含 set 路径）+ 停滞
  PR #75358；已在 #105383 补充"set 静默无效"的实测。修复后 openkg 的
  `_extract_model_options`（读 `config_options` 中 id=model 的 select）可原样
  受益，届时把 ACP 的 `per_turn_model` 翻 `True`。
- 现缓解：ACP 传输声明 `per_turn_model=False`（UI 不显示选择器，避免"选了
  没反应"）；需要按轮切模型走 HTTP 传输。

**3. clarify 不可用（上游有意为之，非缺陷）**
- `hermes-acp` 与 `hermes-api-server` 工具集定义明确排除 `clarify`（"无交互式
  UI 工具"）。openkg 侧无动作；文档与产品预期里写明"hermes 有审批卡、无
  clarify 卡"。

**4. 审批超时的跨系统矛盾（评审发现；已定案按方案 a 实施）**
- hermes 侧自己在 `approvals.timeout`（默认 300s，本机 60s）到点时 **deny**：
  ACP 的 `request_permission` future 被取消，runs 的审批 POST 在超时后返回
  409（`approval_not_pending`）。openkg 侧的审批 park 窗口独立（profile 的
  `approval_timeout_seconds`，上限 600s），且 `respond_approval` 不检查结果
  ——用户可能在 hermes 已 deny 之后才点"批准"，UI 显示"已批准"而 turn 端
  实际已按拒绝走。
- **决策（2026-10-04）：采用方案 a**——两个 backend 的 `respond_approval`
  检查应答结果，迟到/失效时向用户发可见提示：
  - ACP：`AcpSessionManager`/`_AcpClientHandler.resolve_pending` 本就返回
    `bool`（未知/已失效 → False），`respond_approval` 把 False 上抛为结构化
    结果（不抛异常——控制面调用在 turn 流之外，异常只会变成 500）；
  - runs：`POST …/approval` 检查响应状态，409/404 视为"已不在等待"；
  - 能力层（`capabilities/chat/capability.py` 的应答路径）在收到"未生效"
    结果时向会话补发一条进度/系统提示（"the agent already timed out and
    denied this request"），替代当前无条件打印的 "decision: <choice>"。
  - 方案 b（文档约定超时对齐）作为预设描述里的运维提示一并保留。
- 已实测路径不受影响（我们在超时前应答）；该问题是**迟到应答的可见性**，
  不改变审批本身的安全性（hermes 端 fail-closed）。

**二轮评审补充（HIGH，实施必须含）**：
- **残留缺口**：hermes 超时 deny 是它进程内的 `future.cancel()`，**没有 wire
  取消**——openkg 的 parked future 在 hermes deny 后仍 pending，
  `resolve_pending` 会返回 True（"已送达"）。单靠"检查返回值"探测不到这种
  迟到。修复组合：① ACP backend 声明 `approval_timeout_limit`（沿用
  `protocol.py` 既有机制，钳制 profile 的 park 窗口 ≤ hermes 默认 300s）；
  ② `_AcpClientHandler.request_permission` 的 `await future` 加
  `asyncio.wait_for(timeout=审批 park 预算)`——超时即回 hermes
  "cancelled"（与它自己的 deny 竞争，无害）并**同时把 openkg 侧 pending
  按 deny 结清**；此后用户的迟到应答命中 done-future → False → 提示生效。
  若运维把 openkg 窗口调得比 hermes `approvals.timeout` 还大，(60s, N] 区间
  的缺口仍在——预设描述已注明对齐。
- **能力层顺序与卡片元数据**：现有无条件 decision 事件（`capability.py`
  ~472-484）携带 `ask_user_resolved` 等把待答卡片翻转为已答的元数据，且在
  `respond_approval` **之前**发出。实现必须是：先 `await respond_approval`
  拿结构化结果，再发**同一个**事件、按结果切换文案（新增 i18n 键
  `agent_loop.approval_decision_ignored`，en/zh），不得追加第二个事件
  （否则卡片不翻转）。调用链已核实为 turn 任务内 inline await，布尔契约
  兼容；runs 侧今日丢弃 httpx Response，409/404 分支可直接加。

**5. ACP 审批选择保真度（评审发现；二轮已定精确映射表）**
- hermes 的 wire 选项是 5 个（`allow_once/allow_session/allow_always/deny/
  deny_always`）；openkg 卡片按自身 4 词汇（once/session/always/deny）回传，
  且 `_permission_response` 对 `"session"`/`"always"` 都按 kind
  `allow_always` 匹配**第一个**命中——hermes 的回复映射里
  `allow_session→session`、`allow_always→always`，因此选"always"实际发出
  `allow_session`（降级为会话级，不会误放大）。安全方向是 fail-closed，但
  依赖选项顺序；实现时改为 **option_id 优先**匹配，并给 4 种选择各加一个
  单测（防未来选项重排把 session 变成永久批准）。
- **精确映射表（二轮定案）**——choice → 依次尝试的 wire `option_id`，再
  回落 kind，最后回落"首个 allow*"，全空则 cancelled：
  `once → ("allow_once",)`；`session → ("allow_session",)` 后回落 kind
  `allow_always`；`always → ("allow_always", "allow_session")` 后回落 kind
  `allow_always`；`deny`/未知 → 维持现状
  `{"outcome":{"outcome":"cancelled"}}`（fail-closed，被既有测试钉死，**不**
  改为挑选 wire 的 deny 选项）。kind 回落必须保留：openkg 测试假 agent 用
  不透明 id（`opt-allow`），Intellect 亦可能；hermes 按 option_id 映射回
  语义，id 命中即保真。注意 hermes 选项列表是动态的（once_only 场景只有
  2 项、`allow_permanent=False` 时无 `allow_always`），映射须对缺失项鲁棒。

## 9. 上游跟踪

| # | 标题 | 类型 | 状态 |
| --- | --- | --- | --- |
| [#63681](https://github.com/NousResearch/hermes-agent/issues/63681) | Provider namespace lost during ACP session persist → restore fails | issue(bug) | open |
| [#74628](https://github.com/NousResearch/hermes-agent/issues/74628) | session/load fails for named custom providers after restart | issue(bug) | open；[已补 canary 证据](https://github.com/NousResearch/hermes-agent/issues/74628#issuecomment-5978183820) |
| [#105383](https://github.com/NousResearch/hermes-agent/issues/105383) | ACP model picker missing — legacy `models` vs configOptions | issue(bug) | open；[已补 set 静默无效证据](https://github.com/NousResearch/hermes-agent/issues/105383#issuecomment-5978184090) |
| [#74648](https://github.com/NousResearch/hermes-agent/pull/74648) | fix(acp): restore named custom provider sessions | PR | open（停滞） |
| [#75358](https://github.com/NousResearch/hermes-agent/pull/75358) | fix(acp): expose session model selector via configOptions | PR | open |

**回切检查单**（当 PR 合并且发布）：
1. `native_resume` 翻 `True`，跑 `scripts/hermes_acp_probe2.py` 回归（恢复后对话
   应成功）；
2. ACP `per_turn_model` 翻 `True`，验证 composer 模型列表与切换；
3. 更新本文档状态与 `builtin.py` 描述文案。

## 10. 评审记录

**第一轮 v1 → v1.1（2026-10-04，两个独立评审代理）**

评审方式：两个独立评审代理对照源码核验——(A) 落地触点核验（8 个区域逐条
file:line 求证），(B) 对抗性审查（攻击设计前提、边界与超时语义）。

| # | 来源 | 级别 | 发现 | 处置 |
| --- | --- | --- | --- | --- |
| 1 | B | **BLOCKER** | `_fold_history_for_reset` 逐条复核确认：超预算时丢弃**最新**轮次（40×900 字符 → 丢 m23–m39），与 header 文案相反。它是 `native_resume=False` 下 hermes 唯一记忆通道 | §5.3：折叠函数改为近端优先收集；加"最新一条必保留"单测；列入 §7 文件清单与 §6 测试 |
| 2 | B | HIGH | `command_paths` 第二项是本机的**过时快照**（2026-05-17），且 `$HERMES_HOME` 未设时字面量不命中 | §5.1/§5.4：顺序=优先级（`~/.local/bin` 首位，旧 venv 末位）；`$HERMES_HOME` 未设按 `~/.hermes` 展开；范围标注 POSIX |
| 3 | B | HIGH | 审批超时跨系统矛盾（hermes 先 deny vs openkg 迟到应答显示"已批准"） | §8-4：新增已知问题 + 两选一处置（backend 检查结果并提示 / 超时对齐约定）|
| 4 | A | HIGH | 迁移有**三条入口**，只改 `_normalize_agent_loop_profile` 会漏 env 覆盖路径与 PUT 路径 | §5.2：三入口逐一列明 + 测试 |
| 5 | B | MEDIUM | "always" 在 ACP 上降级为 "session"（kind 匹配 + 取第一个） | §8-5：已知问题；实现改为 option_id 优先 + 4 选择单测 |
| 6 | B | MEDIUM | §5.7 因果解释错误：`API_SERVER_ENABLED` 是全局 env，`API_SERVER_KEY` 才是 scoped——**已亲自复核 `agent/secret_scope.py` 证实**（注释明确 "KEY is a credential: NOT here"） | §5.7 改为正确解释；操作建议不变（两者都写 .env）|
| 7 | B | MEDIUM | runs 不受恢复 bug 影响的判断由"未验证"升级为已核验 | §2.3 矩阵与 §8-1 按核验结论改写 |
| 8 | A | MEDIUM | `detect.detail` 无法区分"是否经由回退命中"（PATH 命中也返回绝对路径）；profile 级探测缺回退；Test 端点自带裸 PATH 探测 | §5.4/§5.5：`DetectResult` 增加 `path`/`via_fallback`；profile 级同走回退；Test 端点统一解析 |
| 9 | A | MEDIUM | `_reject_unauthorized_workdirs` 用 preset 默认 family（现存缺陷，hermes 改造会扩大） | §5.2 末尾：修 transport-aware + 用例 |
| 10 | A | MEDIUM | `native_resume` 需经 `AcpSessionManager` 落地（单例、按 command+env 分组），不能只加在 backend `__init__` | §5.3 实现注意 |
| 11 | B | MEDIUM | consult 请求不带 history，`native_resume=False` 下 consult 跨重挂失忆 | §5.3 已知限制（本期文档标注，不做 QA 转录）|
| 12 | A+B | LOW-MED | 既有测试破坏清单（8 处）与文档/locale 更新面（9 处） | §6 破坏清单表 + §7 文档行 |
| 13 | A | LOW | `_transport_from_preset` 不转发新字段 | §5.1 实现注意 |
| 14 | B | LOW | HTTP thinking 是截断预览（500 字符）；ACP 审批卡片 4 项 vs wire 5 项 | §2.3 措辞修正 |
| 15 | B | LOW | 预设描述泄露开发文档引用、缺权限提示；workdir 是"启动目录控制"非沙箱 | §5.1 描述改写；§5.6 授权提示 |
| 16 | B | — | 核验通过、无需动作：preset 新增字段零行为变化；迁移先例形状成立；probe/model-listing 不受 `native_resume=False` 影响；reap=600s；health 无鉴权；密钥 ≥16 字符；i18n 惯例；web contracts 无需改 | — |

第一轮遗留的 §8-4 处置选型已定案（方案 a，backend 检查审批应答结果并提示），见 §8-4。

**第二轮 v1.1 → v1.2（2026-10-04，两个独立代理：v1.1 修订核验 + 实施就绪度）**

综合结论：READY-WITH-DECISIONS/FIXES——16 项处置全部落实且代码事实复核无误；
新增 10 项发现已全部折入 v1.2 并定案：

| # | 级别 | 发现 | 处置 |
| --- | --- | --- | --- |
| R2-1 | HIGH | §8-4 的 ACP bool 探测不到 hermes 超时 deny（本地 cancel 无 wire 取消，parked future 仍 pending → resolve_pending 误报 True） | §8-4 补"残留缺口"与修复组合：`approval_timeout_limit` 声明 + handler `wait_for` 超时并结清 pending |
| R2-2 | HIGH | capability 的 decision 事件携带卡片翻转元数据且先于 respond_approval 发出；naive 实现会破坏卡片 | §8-4 定案"先 await 再发同一事件、仅换文案"；调用链核实为 inline await |
| R2-3 | MEDIUM | §8-5 需精确映射表（hermes 选项列表动态：once_only 2 项、permanent=False 无 allow_always） | §8-5 表格定案；kind 回落保留（兼容不透明 id）；deny 维持现状 |
| R2-4 | MEDIUM | 折叠 header 的 transcript 宣称无条件、函数无管道知晓文件存在；且既有测试钉死 header 前缀 | §5.3 定案 `transcript_note` 可选参数；保留 `[Conversation context — ` 前缀 |
| R2-5 | MEDIUM | env 重写点确认在 `_apply_agent_loop_env_overrides`（synthetic profile 分支无 url，normalize 内重写永不生效） | §5.2 更正函数名与重写点 |
| R2-6 | MEDIUM | router fixture 的无 URL hermes profile 改造后会被 CLI 探测（多一处破坏） | §6 破坏清单 +1 行 |
| R2-7 | LOW | `agent_loop.session_reset` 文案说"重新附着"，与新机制（折叠续写）矛盾 | §7 加 i18n.py 行 |
| R2-8 | LOW | `test_acp_backend.py` ~:887 断言 header 前缀、`test_chat_capability.py` ~:670 返回 None——两处隐藏破坏 | §6 破坏清单 +2 行 |
| R2-9 | LOW | §10 首轮 row 15 的"workdir 非沙箱"半句未落 | 已在 §5.1 描述与 §5.6 注明 |
| R2-10 | NIT | env 入口函数名勘误 | §5.2 已改 |

实施就绪度审查另给出全部关键实现决策（manager 属性接线、折叠算法、
`DetectResult`/`DetectInfo` 字段、PUT/Test 共用重写点、respond_approval 布尔
契约、§8-5 表、前端触点、空 models 列表时隐藏 picker 的小守卫）与 10 步
实施顺序（§7.1）——迁移先于 preset 翻转是硬约束。

**第三轮：实施后质量与安全评审（2026-10-04，两个独立代理，对照 git diff）**

安全结论：**无 blocker**——spawn 输入全部来自代码注册表/管理员门禁设置/运维 env；detect 端点管理员门禁且纯 stat；审批流 fail-closed 保持（deadline 取消的 future 不可能再被解析为 selected；每请求恰好一条 wire 响应）；迁移只向 HTTP 重写、无 workdir 门禁绕过；`command_paths`/`native_resume` 不进 UI payload。质量结论：发现 1 个 HIGH + 若干 MEDIUM/LOW，已全部修复：

| # | 级别 | 发现 | 处置 |
| --- | --- | --- | --- |
| R3-1 | HIGH | 旗舰 deadline 测试是**空转**的：fake agent 不支持 `approval-then-hang` 场景，审批从未发生，测试平凡通过 | fake agent 增加场景；测试重写为确定性（断言 DeniedOutcome + 迟到应答 False），去掉 4.8s sleep |
| R3-2 | MEDIUM | `_agent_loop_profile_block` 未 strip preset——`" hermes "` 绕过迁移重写 | 比较前 strip |
| R3-3 | MEDIUM | zh locale 缺改版检测描述键（中文用户看到英文） | 补 zh 翻译（旧键保留） |
| R3-4 | MEDIUM | Test 端点对显式 command 也套回退路径，与检测器口径不一致 | 仅默认 command 使用回退 |
| R3-5 | MEDIUM | 预填只在"添加"时刻发生；检测完成前建的 draft 拿不到路径 | 新增 effect：检测落地后回填**空** CLI command（不覆盖已填值）；http 芯片在 detecting 期间也渲染（spinner） |
| R3-6 | LOW | runs 401/403 被报为"已送达" | 401/403/404/409 → False |
| R3-7 | LOW | 绝对路径存在但不可执行时报 "not found on PATH" | detect/Test 文案区分"存在但不可执行" |
| R3-8 | LOW | `/bin/sh` 测试依赖、en `session_reset` 文案漂移、脚本硬编码个人路径、探针删除目标未先创建 | 全部修正（脚本 `__file__` 推导 + 先建目录） |
| R3-9 | MEDIUM（本机） | /tmp 残留 hermes env 备份含真实 API key | 已删除备份与全部探针产物 |
| R3-10 | INFO | deadline（自 agent 请求起算）可在 profile≥300s 时抢先卡片超时；option_id 优先信任 agent 自报 id；`delivered=False` 文案过度归因 | 前两条为有意识取舍（fail-closed），文案已软化；接受并记录 |

## 附录 A：探针脚本（复现步骤）

```
# ACP 基础：握手 + 真话 + 跨进程恢复（probe2 含审批）
.venv/bin/python scripts/hermes_acp_probe.py
.venv/bin/python scripts/hermes_acp_probe2.py

# openkg 真实后端 E2E
.venv/bin/python scripts/hermes_openkg_backend_e2e.py        # ACP + 审批
.venv/bin/python scripts/hermes_runs_e2e.py                   # HTTP + 模型
.venv/bin/python scripts/hermes_runs_approval_e2e.py         # HTTP + 审批

# HTTP 侧前置（写 ~/.hermes/.env 后）：
hermes gateway run    # 监听 127.0.0.1:8642
```

注意：探针会真实调用模型（小额费用）并会在 `~/.hermes/state.db` 留下
`source="acp"` 的测试会话；审批探针仅删除 `~/hermes-*-probe` 这类探针目录
（先创建、再演练删除，用户目录的既有文件不受影响）。
