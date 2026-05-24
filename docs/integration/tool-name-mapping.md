# Web alias → Bot agent name 映射参考

> **对接 handoff**:[../../.claude/handoff/2026-05-23-python-service-consolidation.md](../../.claude/handoff/2026-05-23-python-service-consolidation.md) §4.4
> **配套文档**:[web-consolidation-decisions.md](web-consolidation-decisions.md) §4
> **目标读者**:Phytomni-Web chat-ai 改造侧(LLM tool selection prompt + 前端 mapping 表)、Phytomni-Bot dev team

本文件是 Web 旧 `tool_name` 别名与 Bot 规范 agent 名的**完整对照表**。Bot HTTP 路由只接受 Bot 规范名;chat-ai 端自行维护 alias→slug 表翻译旧 LLM prompt 选出的 `tool_name`。

______________________________________________________________________

## 1. 完整对照表

| Web 旧 `tool_name` 别名             | Bot 规范名(MCP tool)    | `/v1/chat/completions` 的 `model` | `/v1/agents/{slug}/runs` 的 `slug` | origin | obs 能力 | stream 能力 |
| ----------------------------------- | ----------------------- | --------------------------------- | ---------------------------------- | ------ | -------- | ----------- |
| `ChatAgent`                         | `ChatAgent`             | `phyto-chat`                      | `chat`                             | local  | ✅       | ✅          |
| `KnowledgeAgent`, `KnowledgeAgents` | `KnowledgeAgent`        | `phyto-knowledge`                 | `knowledge`                        | local  | ✅       | ❌ 400      |
| `DataAgent`, `DatabaseAgents`       | `DataAgent`             | —(无 chat-completions 入口)       | `data`                             | local  | ❌       | ❌ 400      |
| `ReviewAgent`, `ReviewAgents`       | `ReviewAgent`           | `phyto-review`                    | `review`                           | local  | ✅       | ❌ 400      |
| `AnalystAgent`, `AnalysisAgents`    | `AnalystAgent`          | —                                 | `analyst`                          | remote | ❌       | ❌ 400      |
| `DeepGenomeAgent`                   | `DeepGenomeAgent`       | —                                 | `deep_genome`                      | remote | ❌       | ❌ 400      |
| `InSilicoResearchAgent`             | `InSilicoResearchAgent` | —                                 | `research`                         | remote | ❌       | ❌ 400      |

**字段含义**:

- **`model`(chat-completions)**:`/v1/chat/completions` 请求 body 的 `model` 字段值。`—` 表示该 agent 不通过 OpenAI 兼容接口暴露,只能走 native `/v1/agents/{slug}/runs`。
- **`slug`(agent-runs)**:`POST /v1/agents/{slug}/runs` URL 路径段。所有 agent 都有此入口。
- **origin = `local`**:agent 在 Bot 进程内同步执行,响应 HTTP 200 + `status="succeeded"`。
- **origin = `remote`**:agent 提交远端任务,响应 HTTP 202 + `status="running"` + `task_ids`,客户端轮询 `GET /v1/runs/{run_id}` 或 `GET /v1/runs/{run_id}/logs` 获取进度。
- **obs 能力**:`obs_file_list: List[str]` 入参是否被该 agent schema 接受(详见 `_OBS_CAPABLE_TOOLS`)。✅ 接受 / ❌ 提供时返 400。
- **stream 能力**:`stream: true` 在 `/v1/chat/completions` 上是否支持(详见 [web-consolidation-decisions.md](web-consolidation-decisions.md) §3 OQ-3)。仅 `phyto-chat` 支持。

______________________________________________________________________

## 2. Bot 增值 agent(Web 旧 7 alias 之外)

Bot 注册了 10 个 agent,其中 3 个不在 handoff §4.4 的 Web 旧别名列表中。chat-ai UI 是否新接入这 3 个由 Web 团队评估:

| Bot 规范名(MCP tool) | `model`            | `slug`       | origin | obs 能力             | stream 能力 | 用途说明                                                  |
| -------------------- | ------------------ | ------------ | ------ | -------------------- | ----------- | --------------------------------------------------------- |
| `BriefGeneAgent`     | `phyto-brief-gene` | `brief_gene` | local  | ❌(只接单个 gene_id) | ❌ 400      | 单基因功能查询;支持 `resolve_gene_id` LLM 预处理(详见 §5) |
| `DigitalDesignAgent` | —                  | `design`     | remote | ❌                   | ❌ 400      | 蛋白质 + 启动子设计                                       |
| `GeneNetworkAgent`   | —                  | `network`    | remote | ❌                   | ❌ 400      | 性状关联基因网络分析                                      |

**chat-ai 接入建议**:若用户场景涉及单基因解读 / 蛋白设计 / 基因网络,优先用 native `/v1/agents/{slug}/runs`(非 `chat-completions` 入口)。

______________________________________________________________________

## 3. 数据源(源代码 line 引用)

本文件的所有 mapping 必须与下列代码源同步:

- **`PhytomniAgents` enum**(MCP tool 规范名):`src/mcp_server_phytomni/mcp/schemas.py:491`
- **`MODEL_TO_TOOL`**(chat-completions model → MCP tool):`src/mcp_server_phytomni/api/openai_mapping.py:32`
- **`_AGENT_SLUG_TO_TOOL`**(agent-runs slug → MCP tool):`src/mcp_server_phytomni/api/app.py:95`
- **`_REMOTE_AGENT_SLUGS`**(origin 判定):`src/mcp_server_phytomni/api/app.py:112`
- **`_OBS_CAPABLE_TOOLS`**(obs 能力):`src/mcp_server_phytomni/api/openai_mapping.py:41`
- **`_RESOLVE_GENE_ID_CAPABLE_TOOLS`**(resolve_gene_id 能力):`src/mcp_server_phytomni/api/openai_mapping.py:46`

> 行号会随上游 import / 装饰器调整漂移;给单行起点而非范围,定位时配合 `grep -n "<symbol>" <file>` 验证。

Bot 侧每次新增 agent 或调整能力栏,必须同时更新本文件与 `GET /v1/agents` 响应中的 `legacy_aliases` 字段(详见 §4)。

______________________________________________________________________

## 4. `GET /v1/agents` 响应扩展:`legacy_aliases` 字段

Bot 不接受 Web 旧 alias 作为路由 slug,但**在 `GET /v1/agents` 响应行中暴露 `legacy_aliases: List[str]` 元数据**,方便 chat-ai 自动生成翻译表。

响应形状(扩展后):

```json
{
  "object": "list",
  "data": [
    {
      "slug": "knowledge",
      "tool": "KnowledgeAgent",
      "origin": "local",
      "legacy_aliases": ["KnowledgeAgent", "KnowledgeAgents"]
    },
    {
      "slug": "data",
      "tool": "DataAgent",
      "origin": "local",
      "legacy_aliases": ["DataAgent", "DatabaseAgents"]
    },
    {
      "slug": "analyst",
      "tool": "AnalystAgent",
      "origin": "remote",
      "legacy_aliases": ["AnalystAgent", "AnalysisAgents"]
    }
  ]
}
```

**注意**:

- `legacy_aliases` 数组**包含**当前规范名(`KnowledgeAgent`),以便 chat-ai 用 `if tool_name in row["legacy_aliases"]: slug = row["slug"]` 直接匹配
- 新增 agent(如 `brief_gene` / `design` / `network`)的 `legacy_aliases` 仅含规范名,无历史别名

______________________________________________________________________

## 5. chat-ai 端 mapping 实现建议

Bot 不实现兼容层,chat-ai 改造时按如下 3 步落地:

### 5.1 启动时拉一次 `/v1/agents`

chat-ai bootstrap 阶段调一次 `GET /v1/agents`,把响应缓存为内存表:

```typescript
// chat-ai 伪代码
const agentsResp = await fetch('/v1/agents', { headers: { 'Authorization': `Bearer ${userKey}` } });
const aliasToSlug: Record<string, string> = {};
for (const row of agentsResp.data) {
  for (const alias of row.legacy_aliases) {
    aliasToSlug[alias] = row.slug;
  }
}
// 结果示例:
// { ChatAgent: 'chat', KnowledgeAgent: 'knowledge', KnowledgeAgents: 'knowledge', ... }
```

### 5.2 LLM tool selection 后翻译

当旧 LLM tool selection prompt 返回 `tool_name: "KnowledgeAgents"` 时:

```typescript
const slug = aliasToSlug[llmToolName];  // "knowledge"
if (!slug) throw new Error(`unknown agent: ${llmToolName}`);
const url = `/v1/agents/${slug}/runs`;
```

### 5.3 长期:升级 LLM tool prompt 用规范名

chat-ai 一次性把 LLM tool selection prompt 里的旧 alias 改写为 Bot 规范名(slug 形式),老 alias 下线。Bot 端 `legacy_aliases` 字段保留过渡期,3-6 个月后由 Bot 团队评估是否清理。

______________________________________________________________________

## 6. resolve_gene_id 标志(仅 BriefGene)

`BriefGeneAgent` 支持 HTTP-only 入参 `resolve_gene_id: bool`,开启时 Bot 用 LLM 把自由文本预处理为单个 gene id(例如 "AT1G01010 这个基因是干嘛的?" → `AT1G01010`)。

**约束**:

- 仅 `model=phyto-brief-gene`(chat-completions)或 `slug=brief_gene`(agent-runs)接受此标志;其他 tool 提供 → 返 400
- `resolve_gene_id=true` 与 `stream=true` 不能同时使用(BriefGene streaming v1 不支持)
- 解析失败 → 返 400 `BriefGeneResolveError`,**不**回降到原 query

**响应**:成功时响应 `metadata` 块多三个键供客户端展示翻译过程:

```json
{
  "metadata": {
    "original_query": "AT1G01010 这个基因是干嘛的?",
    "resolved_gene_id": "AT1G01010",
    "resolve_gene_id": true
  }
}
```

详见 `agents/brief_gene/resolve_query.py:resolve_brief_gene_user_query`。

______________________________________________________________________

## 7. obs_file_list 入参约定

`obs_file_list: List[str]` 是 chat-completions 与 agent-runs 共通的附件入参,值是 OBS 对象路径列表。

**两种合法格式**:

1. `/obs/<bucket>/<key>` — obsfs 挂载点路径(推荐;由 `POST /v1/files` 返回)
1. `obs://<bucket>/<key>` — 标准 OBS URI

**示例**:

```json
{
  "model": "phyto-knowledge",
  "messages": [{"role": "user", "content": "总结这篇论文"}],
  "obs_file_list": [
    "/obs/phytomni/agent_data/uploads/alice/req_xyz/file_abc/research.pdf"
  ]
}
```

**不支持的 model 提供 obs_file_list** → 400(详见上文 obs 能力栏)。

______________________________________________________________________

## 8. 变更记录

| 日期       | 改动                                                                           |
| ---------- | ------------------------------------------------------------------------------ |
| 2026-05-24 | 初版:7 个 Web alias + 3 个 Bot 增值 agent 的完整映射 + legacy_aliases 字段约定 |
