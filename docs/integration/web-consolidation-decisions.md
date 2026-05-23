# Web Python Service 合并 - Bot 侧决策参考

> **对接 handoff**:[../../.claude/handoff/2026-05-23-python-service-consolidation.md](../../.claude/handoff/2026-05-23-python-service-consolidation.md)
> **目标读者**:Phytomni-Web 维护方(chat-ai / Go service 改造侧)、Phytomni-Bot dev team、运维
> **状态**:Bot 团队侧固化决策,供 Web T1 review

本文件把 handoff §10 的 10 个开放问题(OQ-1..10)在 Bot 仓内固化为对外可引用的决策表。所有后续 Bot 侧 HTTP endpoint 的实现细节以本文件为准。

______________________________________________________________________

## 1. OQ-1 Persistence:沿用 SQLite

**决策**:Bot 沿用现有 SQLite 双库结构,**不引入 MySQL 依赖**。

- `api_keys.sqlite` — API key 存储,默认路径 `.cache/phytomni/api_keys.sqlite`(`API_KEYS_DB_PATH` / `PHYTOMNI_API_KEYS_DB`)
- `server_tasks.db` — `runs` + `tasks` 双表共享,默认相对路径(`API_TASKS_DB_PATH` / `PHYTOMNI_TASKS_DB`)

**关键约束**:SQLite 在 NFS / 网络文件系统上 WAL 模式会 deadlock,详见 `src/mcp_server_phytomni/config/defaults.py`(`ApiConfig` 文档串)。**禁止把 DB 路径设到共享挂载**。

**多实例部署**:

- 单 worker 单进程:开箱即用
- 多 worker(`uvicorn --workers > 1`):需上层 LB 做 sticky session,或后续迁移到 MySQL/Postgres
- 不在 v1 计划内的演进:Redis 集中限流(替代当前进程内 sliding window)、外部 task queue(替代 SQLite tasks 表)

**Web Go 侧影响**:Bot 不要求 Web 提供任何持久层基础设施;Web 删除 `nky_client_python/` 后老 MySQL 表(`s_question_agent_logs` 等)是否保留 / 迁移见 OQ-6。

______________________________________________________________________

## 2. OQ-2 user_id 语义:不透明字符串

**决策**:Bot 把 `user_id` 视作不透明字符串,不做格式校验,不做 normalization。

Web Go 推荐传 **email**(对齐旧 `s_question_agent_logs.user_name` 字段),也可传 internal id / hash / 任意稳定标识。

**影响**:

- `POST /v1/api-keys` 的 `user_id` 字段:任意 string
- `runs.user_id` / `tasks.user_id` 列:存原值,owner-scope 比较走精确匹配
- delegated `GET /v1/runs?user_id=<x>` 查询:精确匹配 `user_id` 列

**约束**:同一 Web 账户在不同 endpoint 调用 Bot 时必须传**完全相同的 `user_id` 字符串**(包括大小写、空格),否则 Bot 视作不同用户,owner-scope 隔离会拒绝读对方数据。

______________________________________________________________________

## 3. OQ-3 SSE Streaming:仅 phyto-chat 支持

**决策**:`POST /v1/chat/completions` `stream: true` 在 v1 仅 `phyto-chat` 模型支持 token-level SSE;其他 model 返 400(响应 message 形如 `streaming not supported for phyto-knowledge`)。

**支持矩阵**:

| 模型               | stream=false             | stream=true        |
| ------------------ | ------------------------ | ------------------ |
| `phyto-chat`       | ✅                       | ✅ token-level SSE |
| `phyto-knowledge`  | ✅                       | ❌ 400             |
| `phyto-review`     | ✅                       | ❌ 400             |
| `phyto-brief-gene` | ✅(含 `resolve_gene_id`) | ❌ 400             |

**SSE 格式**:OpenAI 兼容 `chat.completion.chunk` JSON + `data:` 前缀(后跟一个空格)+ `\n\n` 分隔,末尾 `data: [DONE]\n\n`。auth / rate-limit / request-id / OBS file 处理全部在流开始前完成。

**Web 侧改造**:chat-ai 升级 axios 为 EventSource 或 fetch + ReadableStream;`resolve_gene_id=true` 与 stream=true 不能同时使用。

______________________________________________________________________

## 4. OQ-4 Tool 名映射:Bot 不接受 Web alias 作路由 slug

**决策**:Bot HTTP 路由(`/v1/agents/{slug}/runs` 与 `/v1/chat/completions` 的 `model`)**只接受 Bot 规范名**;Web 旧 alias(`KnowledgeAgents` / `DatabaseAgents` 等)由 chat-ai 端维护 alias→slug 映射表自行翻译。

Bot 端唯一暴露的兼容性元数据是 `GET /v1/agents` 响应行新加的 `legacy_aliases: List[str]` 字段(Bot dev team 团队侧详见 [tool-name-mapping.md](tool-name-mapping.md))。

**理由**:

- 避免 Bot 引入"长期不会移除的兼容性技术债"
- chat-ai 端 LLM tool selection prompt 一次性改完后老 alias 即可下线
- Bot agent 名 = MCP 规范名,与 MCP stdio 客户端 / 现有 e2e / CLI 一致

______________________________________________________________________

## 5. OQ-5 File ingestion:Bot 接管 `POST /v1/files`

**决策**:Bot 实现 `POST /v1/files` multipart upload endpoint。chat-ai 把附件 POST 给 Bot,Bot 落 OBS 并返 obs path,chat-ai 把 path 放入后续 `obs_file_list`。

**关键参数**:

| 配置           | env                    | 默认                 |
| -------------- | ---------------------- | -------------------- |
| 单文件大小上限 | `API_UPLOAD_MAX_BYTES` | `26214400` (25 MiB)  |
| OBS key 前缀   | `API_UPLOAD_PREFIX`    | `agent_data/uploads` |

**OBS 对象 key 形状**:`{prefix}/{user_id}/{request_id}/{file_id}/{safe_filename}`,例如:

```text
agent_data/uploads/test%40example.com/req_abc/file_xyz/research.pdf
```

`user_id` 与 `safe_filename` 都经过 `storage/path_policy.safe_path_segment` 防 path traversal。

**响应**:

```json
{
  "id": "file_xyz",
  "object": "file",
  "filename": "research.pdf",
  "bytes": 12345,
  "purpose": "agent_context",
  "obs_path": "/obs/phytomni/agent_data/uploads/.../research.pdf",
  "path": "/obs/phytomni/agent_data/uploads/.../research.pdf",
  "created_at": "2026-05-24T01:00:00Z"
}
```

`path` 是 `obs_path` 的别名,兼容现有 `obs_file_list` 入参语义。

**错误码**:400 invalid filename / empty / path traversal;413 oversize;401 unauth;500 unexpected storage failure(走统一 envelope)。

**Web 侧影响**:Web Go 端**不需要**新写 file proxy。chat-ai 直接 POST 文件给 Bot。

______________________________________________________________________

## 6. OQ-6 数据迁移:默认 X(只读老库),Y(ETL)为可选

**决策**:Bot 不单方面决定 ETL 策略。本规划默认选项 X(Web MySQL 历史只读 + Bot 新会话写自家 SQLite),实施时双方再共同确认是否启动 ETL phase。

**选项 X(默认)**:

- Bot:新会话写 `runs` 表,带 `dialogue_id` 元信息
- Web:`nongke` 库 `s_question_agent_logs` 保留只读,chat-ai history 页前期同时读两源,3-6 月后老数据自然过期
- ETL 不需要执行

**选项 Y(可选,需三方拍板)**:

- 一次性 ETL 脚本 `scripts/etl_web_mysql_history.py` 把 `s_question_agent_logs` 行映射为 `runs` 行
- 字段映射:`f_id or id` → `run_id`;`user_name` → `user_id`;`tool_name` → `agent` + `tool_name`;`query` → `query`;`answer` 落到 `result_json.answer`;`task_id` 复用,`task_log` 复用
- OBS key 不迁移(Web 老 hardcoded 凭证废弃后老 key 自然作废,新会话用 Bot 自家 OBS 路径)
- 生产 MySQL DSN 由 Web 提供,不入库

**触发条件**:Web 团队 + 产品 + Bot 团队三方确认选项 Y 后,Bot 启动 ETL phase。

______________________________________________________________________

## 7. OQ-7 OBS 凭证:envelope-encrypted `.env`

**决策**:Bot OBS 凭证走 envelope-encrypted `.env` 流程,Web 旧 hardcoded ak/sk 接管后**立即作废**。

**Bot 凭证加载链**(详见 `src/mcp_server_phytomni/config/settings.py:_resolve_license_key`):

1. `PHYTOMNI_TESTING=1` → dummy 凭证(测试只跑)
1. `PHYTOMNI_LICENSE_KEY` env 或 `config/.license_key` 文件 → 解 `.env.encrypted`(`PHYBOT01` magic 头 AES-256-GCM)→ `SensitiveConfig` 装载
1. 否则 → 明文 `.env`(开发环境)

凭证内存常驻,**永不持久化或入日志**。

**Web 侧影响**:

- Web Go 不再传 OBS ak/sk 给 Bot
- Web 老 hardcoded 在 `nky_client_python/nky_client.py:564-566` 的 ak/sk 在 cutover 当日必须 Huawei 控制台轮换作废
- 现有 `s_gene_example.server_file_path` 指向的老 OBS 对象保持原 key,Bot 用新凭证读写

______________________________________________________________________

## 8. OQ-8 部署:独立 `phytomni-api` 进程

**决策**:Bot 以独立 `phytomni-api` 进程方式运行,与 Web Go service 同机 / 不同机均可。

**Bot 不约束 Web 部署拓扑**,仅约定:

- Bot 通过环境变量配置(`API_HOST` / `API_PORT` / `API_TASKS_DB_PATH` 等,见 [http-api.md](../http-api.md))
- Web Go 通过 service token 调用(详见 §11);URL 由部署侧配置
- Bot service 启动单元(systemd / docker)由 ops 自行编排

**部署 URL 占位**:

| 环境    | URL           | 持有方 |
| ------- | ------------- | ------ |
| dev     | TBD(部署后填) | ops    |
| staging | TBD           | ops    |
| prod    | TBD           | ops    |

______________________________________________________________________

## 9. OQ-9 Rate limit:沿用 per-key sliding window

**决策**:沿用现有 `api/ratelimit.py` 的进程内 per-key sliding window。

- 配置项:`API_RATE_LIMIT_PER_MIN`(默认 120)
- 触发:返 429 `{Retry-After: <seconds>}`
- 范围:user key 与 service token 各自独立计数

**限制**:单进程内存。多 worker 部署需上层 LB 或换 Redis(future work,不在 v1)。

______________________________________________________________________

## 10. OQ-10 监控 / 日志:沿用 stdlib + X-Request-Id

**决策**:Bot 沿用 stdlib `logging` + 每请求 `X-Request-Id` header,不集成 OTEL / Prometheus。

- `X-Request-Id`:`request_context_middleware` 注入,每 response 回写
- log 配置:由部署侧 logging.conf 决定
- 错误 envelope 携带 `request_id` 字段,便于跨服务串联

**Future work**(不在 v1):OTEL trace 注入、Prometheus metrics endpoint(`/metrics`)、结构化 JSON log 输出。

______________________________________________________________________

## 11. 服务间 Auth 设计(Service token vs User key)

Bot 提供**两种独立的鉴权凭证**,严格隔离:

| 凭证类型                     | 颁发方式                                                                               | header                                                                        | 用途                                              | 失效                                        |
| ---------------------------- | -------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------- | ------------------------------------------- |
| **User API key** (`ptm_...`) | `POST /v1/api-keys`(Web Go 调)或 `phytomni-api-key create` CLI                         | `Authorization: Bearer ptm_...` 或 `X-API-Key: ptm_...`                       | chat-ai 等终端用户客户端的常规调用                | revoke(`DELETE` 或 CLI)或 `expires_at` 到期 |
| **Service token**            | ops 配置 `PHYTOMNI_API_SERVICE_TOKEN` env(envelope 加密 `.env` 内,或 systemd unit env) | `Authorization: Bearer <service-token>` 或 `X-Service-Token: <service-token>` | Web Go 颁发 user key / delegated 查询其他用户历史 | 改 env + 重启服务                           |

**关键设计**:

- Service token 是**独立环境变量**,不入 `api_keys.sqlite`,不复用 `ApiKeyStore` 任何代码路径——降低普通 user key 提权风险面
- 未配置 service token 时,`/v1/api-keys/*` 与 `?user_id=<x>` delegated 查询全部返 503("admin path not enabled"),production 必须显式启用
- Service token 等同 root 凭证,**90 天轮换**(runbook 落地)

______________________________________________________________________

## 12. 错误 envelope 对照

Bot 现有统一错误 envelope(`api/schemas.py:ApiErrorResponse`):

```json
{
  "error": {
    "type": "bad_request",
    "code": 400,
    "message": "...",
    "request_id": "req_..."
  }
}
```

`type` 取自 `_ERROR_TYPES`(`api/app.py`)对应 status code 的语义化字符串。

**与 Web 旧 `{code, message}` 对照**:

| Web 旧                                         | Bot 新                                                  | 转换                                                       |
| ---------------------------------------------- | ------------------------------------------------------- | ---------------------------------------------------------- |
| `{code: 200, message: "success", data: {...}}` | HTTP 2xx + body 直接是 data                             | chat-ai 端去掉 `data` 解包                                 |
| `{code: 400, message: "..."}`                  | HTTP 4xx + `{error: {code, message, type, request_id}}` | chat-ai 读 `error.message`;`request_id` 用于跨服务日志关联 |
| `{code: 408, message: "请求超时"}`             | HTTP 504 + envelope                                     | 客户端按 HTTP code 处理                                    |
| `{code: 500, message: "..."}`                  | HTTP 5xx + envelope                                     | 同 4xx                                                     |

______________________________________________________________________

## 13. 凭证管理 Runbook 要点

1. **首次部署**:

   - ops 生成 `PHYTOMNI_API_SERVICE_TOKEN`(`openssl rand -hex 32`),写入部署系统的 secret store
   - 启动 `phytomni-api`,验证 `curl http://<bot>/healthz` 返 200
   - 用 service token 调 `POST /v1/api-keys` mint 一把测试 user key,验证 `POST /v1/chat/completions`

1. **轮换 service token**:

   - 新 token 写入 secret store(覆盖)
   - 重启 `phytomni-api`(新 env 生效)
   - 通知 Web Go 团队同步更新

1. **撤销 user key**:

   - Web Go 调 `DELETE /v1/api-keys/{prefix}`(若有 service token)
   - 或 ops 直接 `phytomni-api-key revoke --prefix <prefix>`

1. **凭证泄漏应急**:

   - service token 泄漏 → 立即轮换(见步骤 2)+ 审计 `api_keys.sqlite` 近期 created_at 行
   - user key 泄漏 → revoke 单 key + 重新 mint

______________________________________________________________________

## 14. Future Work(明确不在 v1 内)

- OTEL 链路追踪 + Prometheus metrics
- Redis 集中限流(多 worker 部署需要)
- MySQL/Postgres 持久层(多写实例需要)
- Web `nongke` 历史 ETL(选项 Y,需三方拍板)
- gRPC 接口(目前只 HTTP)
- 客户端 SDK(目前依赖 OpenAI 兼容 SDK)
- 文件预览 / 元信息提取(目前 `/v1/files` 仅落盘返路径)

______________________________________________________________________

## 变更记录

| 日期       | 改动                                                      |
| ---------- | --------------------------------------------------------- |
| 2026-05-24 | 初版:OQ-1..10 决策固化,service token 与 user key 隔离设计 |
