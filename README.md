# astrbot_plugin_shore_bridge

把 AstrBot 对话接入 [Shore Memory](https://github.com/Youzini-afk/shore-memory) 的桥接插件：
在每次 LLM 请求前注入长期记忆与 Agent 状态，在每轮对话完成后把 user/assistant 回合写回 Shore，
并提供一组 `/shore` 斜杠命令用于调试、手动记忆管理与会话静音。

- 仓库：<https://github.com/Youzini-afk/astrbot_plugin_shore_bridge>
- 插件版本：`0.2.0`
- 适配 AstrBot：`>=4.18, <5`
- 许可协议：AGPL-3.0-or-later（见 `LICENSE`）

## 功能亮点

### 召回与注入

- 使用共享 `httpx.AsyncClient` 调用 Shore `/v1/context/recall`，区分 connect 与 read 超时。
- 自动组合「当前消息 + 最近 N 轮对话」作为 recall query，避免单条短消息召回质量差。
- 支持 Shore 的 `recipe`（`fast` / `hybrid` / `entity_heavy` / `contiguous`）和 `debug` 开关。
- 生成的 prompt 块会：
  - 按 `recall_min_score` 过滤低分片段
  - 按 `recall_max_chars` 截断总字符数
  - 可选附带 `entities` 实体提示
  - 在 `degraded=true` 时加一条稳健性提示
  - 注入 `agent_state`（`mood` / `vibe` / `mind` …）
- 支持 `system` / `user` 两种注入位置（`inject_mode`）。

### 写回（Turn Writeback）

- 每轮 LLM 回合完成后排队调用 `/v1/events/turn`。
- **后台队列**：主链路不被写回阻塞；失败按指数退避重试（默认最多 3 次）。
- **幂等**：按 `session_uid + created_at + response_id + 文本` 做 SHA1 去重，避免流式/重复事件造成的重复写回。
- 仅在 `is_chunk=False` 的最终响应上触发，保留原始流式体验。

### 身份、会话与作用域

- 每个事件构造稳定身份：
  - `user_uid = {platform}:user:{sender_id}`
  - `channel_uid = {platform}:group:{group_id}` 或 `{platform}:dm:{sender_id}`
  - `session_uid = {unified_msg_origin}#{bucket_id}`，空闲超过 `session_idle_minutes` 会自动轮转到新 bucket
  - `scope_hint` 自动推断为 `group` 或 `private`
- 支持 `platform_agent_map_json` 按平台路由到不同的 Shore `agent_id`。

### 命令集 `/shore`

- 通过 AstrBot 指令组暴露一套可直接用的能力（详见下文）。

### 观测性

- 所有 Shore 请求都带 `x-request-id`（`request_id_prefix` 可配），方便在 Shore 日志里串联。
- 可选启用 Shore `/v1/events` WebSocket 订阅，按 `events_ws_log_types` 过滤后写入 AstrBot 日志。

## 依赖与要求

- **AstrBot**：`>=4.18, <5`
- **Python**：3.10+
- **Shore Memory 服务**：建议 `0.3.x` 或以上
- Python 依赖（`requirements.txt`）：
  - `httpx>=0.28.1`
  - `websockets>=12.0`（仅在启用 `events_ws_enabled` 时使用）

## 安装

### 方式 A：手动克隆

```bash
cd <AstrBot 项目目录>/data/plugins
git clone https://github.com/Youzini-afk/astrbot_plugin_shore_bridge.git
```

然后重启 AstrBot 或在 WebUI 触发插件热重载。

### 方式 B：AstrBot WebUI

在 AstrBot WebUI 的「插件 → 从 Git 安装」中填入：

```text
https://github.com/Youzini-afk/astrbot_plugin_shore_bridge.git
```

### 启动 Shore Memory 服务

参考 [shore-memory](https://github.com/Youzini-afk/shore-memory) 的部署文档。
默认监听 `http://127.0.0.1:7811`。若启用了 `PMS_API_KEY`，请在本插件中同时配置 `api_key`。

## 配置项

全部配置可在 AstrBot WebUI 的「插件配置」里修改；字段与默认值对应 `_conf_schema.json`。

### 基本

| 配置键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | 总开关。关闭后不再注入召回，也不再写回。 |
| `service_base_url` | string | `http://127.0.0.1:7811` | Shore 服务地址。 |
| `api_key` | string | `""` | 可选；对应 Shore 的 `PMS_API_KEY`。 |
| `api_key_mode` | string | `both` | 鉴权头模式：`both` / `bearer` / `x-api-key`。 |
| `agent_id` | string | `shore` | 默认 Shore `agent_id`。 |
| `platform_agent_map_json` | text | `""` | JSON 对象，把 `platform_id` 或 `platform_name` 映射到不同 `agent_id`，例如 `{"qq":"shore-qq","discord":"shore-dc"}`。 |

### 召回

| 配置键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `recall_limit` | int | `8` | 单次召回条数。 |
| `recall_recipe` | string | `""` | Shore recall recipe；留空让服务端自选。 |
| `recall_debug` | bool | `false` | 透传 `debug=true`。 |
| `inject_agent_state` | bool | `true` | 是否把 `mood/vibe/mind` 注入 prompt。 |
| `inject_mode` | string | `system` | 注入位置：`system` 或 `user`。 |
| `recall_min_score` | float | `0.0` | 丢弃低于该分数的片段。 |
| `recall_max_chars` | int | `1600` | 召回段落最大字符数（软上限，整段可跨越）。 |
| `recall_include_entities` | bool | `true` | 是否附带实体提示。 |
| `recall_context_messages` | int | `4` | 组合进 query 的最近用户/助手消息数量。 |
| `recall_on_empty_message` | bool | `true` | 当前事件没有文本时是否仍尝试召回。 |
| `degraded_notice` | bool | `true` | Shore 返回 `degraded=true` 时加提示。 |

### 写回

| 配置键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `writeback_enabled` | bool | `true` | 是否写回完成的回合。 |
| `writeback_max_retries` | int | `3` | 失败写回重试次数。 |
| `writeback_queue_size` | int | `128` | 写回队列容量；溢出时丢弃最新回合并打 warning。 |
| `session_idle_minutes` | int | `30` | 空闲多久后轮转到新 `session_uid` bucket。 |

### 命令与事件流

| 配置键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `commands_enabled` | bool | `true` | 是否启用 `/shore` 指令组。 |
| `events_ws_enabled` | bool | `false` | 是否后台订阅 Shore `/v1/events`。 |
| `events_ws_log_types` | string | `""` | 逗号分隔的事件名白名单；空表示全部。 |
| `remember_default_scope` | string | `auto` | `/shore remember` 的默认作用域；`auto` 会根据 `scope_hint` 选择。 |

### 超时与可观测性

| 配置键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `connect_timeout_seconds` | float | `2.0` | HTTP connect 超时。 |
| `recall_read_timeout_seconds` | float | `4.0` | 召回读超时。 |
| `writeback_read_timeout_seconds` | float | `8.0` | 写回读超时。 |
| `command_read_timeout_seconds` | float | `6.0` | `/shore` 命令读超时。 |
| `request_id_prefix` | string | `shore-bridge` | 生成的 `x-request-id` 前缀。 |

## 命令

所有命令均在 `/shore` 组下（别名：`/memory`）。命令执行后会调用 `stop_event()`，不会继续触发默认 LLM 回复。

| 命令 | 说明 |
| --- | --- |
| `/shore ping` | 调用 Shore `/health`，输出 `worker_available` / `pending_tasks` / `failed_tasks`。 |
| `/shore status` | 打印插件本地状态：`enabled` / `muted` / `service` / `agent_id` / `scope_hint` / `channel_uid` / `session_uid`。 |
| `/shore recall [query]` | 手动召回。留空则使用当前会话上下文构造 query；输出分数与内容预览，错误会直接显示。 |
| `/shore remember <content>` | 直接写一条 `manual_note`（`/v1/memories`）。返回新 `memory_id` 与 `rebuild_queued`。 |
| `/shore forget <memory_id>` | 归档一条记忆（`archived=true`，`source=astrbot_manual`）。 |
| `/shore state` | 拉取 `agent_state`（`/v1/agents/{agent_id}/state`）。 |
| `/shore mute` / `/shore unmute` | 按会话静音/取消静音；静音期间不召回也不写回。 |

## 工作原理

### 请求前注入（`on_llm_request`）

1. 构造身份（见上文「身份、会话与作用域」）。
2. 读取当前对话历史最近 `recall_context_messages` 条用户/助手消息，与当前输入合并成 recall query。
3. 调 Shore `/v1/context/recall`。
4. 根据配置过滤、截断并格式化为 prompt 块：
   - 默认追加到 `system_prompt`
   - 若 `inject_mode=user`，追加到 `extra_user_content_parts`
5. 把 `user_text` / `identity` / `agent_id` 缓存在事件 extra 字段，供写回阶段复用。

### 回合写回（`on_llm_response`）

- 跳过流式中间块（`is_chunk=True`）。
- 按 `session_uid + created_at + response_id + 文本` 生成去重 key；命中则跳过。
- 入队到后台 `BackgroundWriteback`；失败按 `min(8s, 1.5 * 2^attempt)` 退避，最多 `writeback_max_retries` 次。
- 写回到 Shore 的 payload 结构：

```json
{
  "agent_id": "shore",
  "user_uid": "qq:user:1234",
  "channel_uid": "qq:group:5678",
  "session_uid": "qq:GroupMessage:5678#1714900000",
  "source": "astrbot",
  "scope_hint": "group",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "metadata": {
    "platform": "qq",
    "platform_name": "qq",
    "sender_name": "alice",
    "message_type": "GroupMessage",
    "umo": "qq:GroupMessage:5678",
    "response_id": "...",
    "bridge_version": "0.2.0"
  }
}
```

### 会话 bucket

`SessionBucketStore` 为每个 `unified_msg_origin` 维护一个 bucket。两次事件间隔若超过
`session_idle_minutes`，bucket 会滚动到一个新的纪元秒，`session_uid` 随之变化。
这样 Shore 侧能把「同一会话的连续对话」归到一个 session，而长时间停顿后的新对话会进入新 session。

### WebSocket 事件订阅

启用 `events_ws_enabled` 后，会连接到 Shore `/v1/events`，断线按指数退避重连（最长 30s）。
可通过 `events_ws_log_types` 只记录感兴趣的事件类型，如 `memory.created,turn.scored`。

## 开发

### 目录结构

```text
astrbot_plugin_shore_bridge/
├── main.py                 # AstrBot Star 入口 & 命令组
├── metadata.yaml
├── _conf_schema.json
├── requirements.txt
├── bridge/
│   ├── client.py           # 共享 httpx AsyncClient + Shore API 封装
│   ├── config.py           # 配置解析
│   ├── events.py           # WebSocket 订阅
│   ├── identity.py         # 身份构造 + session bucket
│   ├── prompting.py        # recall 块 / 预览 / agent state 渲染
│   └── writeback.py        # 后台写回队列 + 去重
└── tests/
    ├── test_config.py
    ├── test_identity_writeback.py
    └── test_prompting.py
```

### 运行单元测试

```bash
python -m unittest discover -s tests -p "test_*.py"
```

测试只覆盖纯逻辑（配置解析、prompt 构造、身份 bucket、去重、写回重试），不需要真实 Shore 服务。

## 故障排查

- **召回没注入**：确认 `enabled=true`、当前会话未 `/shore mute`；用 `/shore ping` 验证 Shore 可达；必要时打开 `recall_debug`。
- **写回一直失败**：看 AstrBot 日志里 `shore bridge writeback permanently failed`，通常是 Shore 端 4xx/5xx；用 `/shore ping` 检查 worker/pending/failed 状态。
- **鉴权失败**：检查 `api_key` 与服务端 `PMS_API_KEY` 一致；如对接了反向代理只支持某一种头，可把 `api_key_mode` 改为 `bearer` 或 `x-api-key`。

## License

本插件以 **GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later)** 发布。完整文本见仓库根目录的 `LICENSE`。
