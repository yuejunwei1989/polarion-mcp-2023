# Polarion MCP Server — 2023 兼容版

一个面向 **Siemens Polarion ALM** 的 [Model Context Protocol](https://modelcontextprotocol.io) 服务器，专门解决现有开源 Polarion MCP server **只支持 Polarion 2025+** 的问题。

支持 Polarion **2304 / 2310（2023.04 / 2023.10）**，同时可在 2404+ 上运行（自动降级/升级能力）。

---

## 为什么需要这个项目

现有主流开源实现（如基于 2025 OpenAPI 自动生成工具集的方案）在 2023 上会在**第一次请求**就失败，原因有三个，且都不是"改个版本号"能解决的：

| 现有实现的假设 | Polarion 2023 的实际情况 |
|---|---|
| 启动时调用 `GET /rest/v1/metadata` 获取版本与 REST 配置 | **该端点 2404 才引入**，2023 返回 404，启动即失败 |
| 用 `@modelcontextprotocol` 全量生成 2025 OpenAPI 的 200+ 工具 | 其中 custom fields 配置、fields metadata、license、jobs、pages CRUD、collections 等端点在 2023 **不存在** |
| 只支持 Personal Access Token（Bearer） | 2023 部署大量使用 **Basic Auth**；PAT 仅在管理员开启 `restApiToken.enabled` 后可用 |

本项目的做法：

1. **不依赖 `/metadata`**：用每个版本都存在的 `/projects` 做连通性与凭据校验，通过探测结果判定 flavor。
2. **能力矩阵而非全量生成**：只暴露 2023 真实存在的端点，按版本阈值启用/禁用能力。
3. **双认证**：Basic Auth 与 PAT 都支持，`auto` 模式自动选择。
4. **不追求工具数量**：32 个语义化工具，覆盖需求、追溯、LiveDoc、测试管理的高频场景，对 LLM 更友好。

---

## 版本与能力对照

| 能力 | 2304 | 2310 | 2404 | 本项目在 2023 的行为 |
|---|:--:|:--:|:--:|---|
| 项目 / 工作项 CRUD、Lucene 查询 | ✅ | ✅ | ✅ | 完整支持 |
| 分页（`page[size]` / `page[number]`） | ✅ | ✅ | ✅ | 完整支持，自动翻页 |
| LiveDoc Spaces / Documents / Parts（读） | ✅ | ✅ | ✅ | 完整支持 |
| 枚举（Enumerations） | ✅ | ✅ | ✅ | 完整支持 |
| Test Run / Test Record | 部分 | ✅ | ✅ | 完整支持 |
| Plans、Revisions、Workflow Actions | — | ✅ | ✅ | 完整支持 |
| 评论、附件 | ✅ | ✅ | ✅ | 完整支持 |
| `GET /metadata` | — | — | ✅ | 不调用，改为探测 |
| 自定义字段配置 / 字段元数据 API | — | — | ✅ | 不调用，从工作项实例推断 |
| License / Jobs / Pages CRUD / Collections | — | — | ✅ / 2410 | 不暴露 |

> 2310 的 REST 层已覆盖 Test Management、Plans、Revisions、Workflow Actions；2304 已引入分页、LiveDoc 和枚举端点。2404 新增的是**管理/发现类**端点，这正是 2025-only server 在 2023 上崩掉的部分。

---

## 快速开始

### 1. 安装

```bash
git clone <this-repo> && cd polarion-mcp-2023
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

或使用 `pipx` / `uv`：

```bash
pipx install .
# 或
uv pip install .
```

### 2. 配置

```bash
cp .env.example .env
```

最小配置（2023 推荐的 Basic Auth）：

```bash
export POLARION_URL=https://polarion.example.com
export POLARION_USERNAME=alice
export POLARION_PASSWORD=secret
export POLARION_DEFAULT_PROJECT=MyProject
```

如果实例已启用 PAT（2310+）：

```bash
export POLARION_TOKEN=pat-xxxxxxxx
export POLARION_AUTH_MODE=token
```

### 3. 先做连通性检查（只读，不写数据）

```bash
python scripts/doctor.py
```

输出会逐项检查 REST 可达性、凭据、项目、工作项、Spaces、Test Runs、枚举，并打印探测到的版本与能力。**在接 AI 客户端之前先跑这一步**，可以排除 90% 的配置问题。

### 4. 接入 MCP 客户端

**Claude Desktop / Cursor / 通用 stdio 客户端：**

```json
{
  "mcpServers": {
    "polarion": {
      "command": "polarion-mcp-2023",
      "env": {
        "POLARION_URL": "https://polarion.example.com",
        "POLARION_USERNAME": "alice",
        "POLARION_PASSWORD": "secret",
        "POLARION_DEFAULT_PROJECT": "MyProject",
        "POLARION_READ_ONLY": "1"
      }
    }
  }
}
```

**Claude Code：**

```bash
claude mcp add polarion \
  -e POLARION_URL=https://polarion.example.com \
  -e POLARION_USERNAME=alice \
  -e POLARION_PASSWORD=secret \
  -e POLARION_DEFAULT_PROJECT=MyProject \
  -- polarion-mcp-2023
```

**Docker：**

```bash
docker build -t polarion-mcp-2023 .
docker run -i --rm \
  -e POLARION_URL=https://polarion.example.com \
  -e POLARION_USERNAME=alice -e POLARION_PASSWORD=secret \
  polarion-mcp-2023
```

**HTTP 传输（团队共享 / 远程客户端）：**

```bash
python -m polarion_mcp_2023 --transport streamable-http --host 0.0.0.0 --port 8000
# MCP endpoint:  http://<host>:8000/mcp
# Health check:  http://<host>:8000/health  -> {"status":"ok","ready":true,"flavor":"2023",...}
```

> HTTP 模式没有内置鉴权。请只绑定内网地址，或在前面放反向代理做认证。

---

## 配置项

| 变量 | 默认 | 说明 |
|---|---|---|
| `POLARION_URL` | —（必填） | 服务器地址。可写 `https://host`、`https://host/polarion` 或完整 REST URL，自动规范化 |
| `POLARION_AUTH_MODE` | `auto` | `auto` / `basic` / `token` |
| `POLARION_USERNAME` / `POLARION_PASSWORD` | — | Basic Auth |
| `POLARION_TOKEN` | — | Personal Access Token（Bearer） |
| `POLARION_DEFAULT_PROJECT` | — | 省略 `project_id` 时使用 |
| `POLARION_READ_ONLY` | `0` | `1` 时只暴露读工具 |
| `POLARION_VERIFY_SSL` | `true` | 自签名证书设为 `false` |
| `POLARION_TIMEOUT` | `30` | 单请求超时（秒） |
| `POLARION_PAGE_SIZE` | `100` | 每页条数 |
| `POLARION_MAX_PAGE_SIZE` | `100` | 2023 默认上限；2404+ 由 `/metadata` 覆盖 |
| `POLARION_MAX_ITEMS` | `1000` | 单次工具调用最多扫描的条目数 |
| `POLARION_API_FLAVOR` | `auto` | `auto` / `2023` / `2025`；强制模式用于排障 |
| `POLARION_LOG_LEVEL` | `INFO` | 日志级别（输出到 stderr，不污染 stdio） |

---

## 工具清单（32 个）

### 项目与发现

| 工具 | 作用 |
|---|---|
| `polarion_server_info` | 版本、flavor、认证方式、能力矩阵、不支持项。**排障第一步** |
| `polarion_list_projects` | 列出可访问项目，支持本地过滤 |
| `polarion_get_project` | 项目详情 + 工作项类型 + 项目级能力探测 |
| `polarion_list_work_item_types` | 类型发现（关系 → 枚举 → 采样三级回退） |

### 工作项

| 工具 | 作用 |
|---|---|
| `polarion_search_work_items` | Lucene 查询，自动分页，描述截断 |
| `polarion_get_work_item` | 单条详情 + 自定义字段 + 追溯链接 |
| `polarion_get_work_items` | 批量获取，缺失 id 单独报告 |
| `polarion_create_work_item` | 创建，支持自定义字段、指派、`dry_run` |
| `polarion_update_work_item` | PATCH，只改传入字段，`dry_run` |
| `polarion_delete_work_item` | 删除，`dry_run` |
| `polarion_get_work_item_revisions` | 修订历史（2310+） |

### 追溯链接

| 工具 | 作用 |
|---|---|
| `polarion_get_linked_work_items` | 出链/入链 + role + suspect |
| `polarion_link_work_items` | 建立 `verifies` / `implements` 等链接 |
| `polarion_unlink_work_items` | 删除链接，自动尝试 2023 的路径变体 |

### LiveDoc

| 工具 | 作用 |
|---|---|
| `polarion_list_spaces` | 列出 Spaces |
| `polarion_list_documents` | 列文档；无项目级端点时自动遍历 Spaces |
| `polarion_get_document` | 读文档 HTML，可带 Parts |
| `polarion_get_document_parts` | 读章节/段落，可回填 Work Item 内容 |

### 测试管理

| 工具 | 作用 |
|---|---|
| `polarion_list_test_runs` | 列 Test Run |
| `polarion_get_test_run` | 详情 + 记录 + pass/fail 汇总 |
| `polarion_create_test_run` | 创建 Test Run（可指定模板） |
| `polarion_add_test_record` | 记录测试结果 |
| `polarion_get_test_records` | 列记录 + 汇总 |

### 元数据与协作

| 工具 | 作用 |
|---|---|
| `polarion_list_enumerations` | 枚举定义与允许值 |
| `polarion_list_users` | 用户列表 |
| `polarion_add_comment` / `polarion_get_comments` | 工作项评论 |
| `polarion_get_workflow_actions` / `polarion_perform_workflow_action` | 工作流动作（2310+） |
| `polarion_list_attachments` / `polarion_download_attachment` / `polarion_upload_attachment` | 附件 |

所有写工具都支持 `dry_run=True`，先返回将要发送的 `method` / `path` / `body`，确认后再真正执行。

### 内置 Prompt（3 个）

| Prompt | 用途 |
|---|---|
| `review_requirement` | 按"单一、可验证、可测试"标准审查需求，报告歧义与验证覆盖缺口 |
| `traceability_gaps` | 找出缺少 implements / verifies 链接的需求 |
| `prepare_test_run` | 从需求范围推导测试运行，**只预览、不擅自写入** |

Prompt 不直接改数据，只约束 AI 的调用顺序与写前确认，降低误操作。

---

## 2023 兼容是怎么做的

### 1. 版本探测不依赖 2025 端点

```
GET /rest/v1/metadata
  ├─ 200 → 解析 polarionVersion / restApiProperties，flavor=2025
  └─ 404/405/400 → flavor=2023，release 取基线 2310
         ↓
GET /rest/v1/projects?page[size]=1   ← 每个版本都有，同时验证凭据与 URL
```

`POLARION_API_FLAVOR=2023` 可跳过探测（适合被反向代理干扰的部署）。

### 2. 能力矩阵驱动工具行为

`capabilities.py` 用版本阈值描述每项能力，工具据此决定调用还是给出明确说明，而不是抛一个裸 404：

```
2310 → work_items, documents, enumerations, test_runs, plans,
       revisions, workflow_actions, attachments, comments
2404 → metadata, custom_field_config, fields_metadata, license,
       jobs, pages_crud, bulk_backlink, document_search
2410 → collections
```

### 3. 认证降级

`auto` 模式：有 `POLARION_TOKEN` 用 Bearer，否则用 Basic。401 时错误信息直接给出排查路径（凭据 / REST 开关 / PAT 是否启用）。

### 4. 分页与上限

Polarion 2023 默认 `maxPageSize=100`（2404+ 才能从 `/metadata` 读到真实值）。客户端按 `page[size]` 翻页，用 `meta.totalCount` 判断终止，并带 1000 页防御性上限。

### 5. 自定义字段

2023 没有字段元数据 API，因此：

- **读**：`fields[workitems]=@all` 返回的 `attributes` 中，非内建字段全部归入 `custom_fields`；
- **写**：自定义字段 id 直接平铺进 `attributes`（Polarion REST 的实际行为）；
- **类型发现**：项目关系 → `enumerations/workItemType` → 枚举索引 → 采样工作项，四级回退。

### 6. 错误映射

统一解析 JSON:API 的 `errors[]`，并把 401/403/404/405/422 转成带 `hint` 的结构化错误。404 的提示会明确说明"该端点可能属于 2404+，请运行 `polarion_server_info`"。

---

## 已知限制

- **不提供管理类能力**：custom field 配置、license、jobs、collections、pages CRUD 在 2023 上不存在，本 server 也不伪造。
- **不创建/编辑 LiveDoc 文档**：2310 的 Document/Parts 写入在不同构建上差异较大，本项目只做可靠读取。需要创建文档请在 Polarion UI 完成。
- **多值对象枚举**：Polarion 在 2404 之前对 Builds、Collections、Documents & Pages 等 OOTB 对象枚举的 REST 支持不完整，写入这类自定义字段可能 422。这是服务端限制，不是工具缺陷。
- **附件下载落盘**：返回本地路径而不是 base64，避免撑爆模型上下文。若 server 跑在 Docker 里，该路径是容器内路径，需要挂载卷或改用 HTTP 模式下的客户端直连。
- **HTTP 模式无内置鉴权**：见上文，用反向代理或仅内网暴露。

### 验证状态

- ✅ 协议层（stdio / streamable-http）、工具 schema、分页、错误映射、只读模式、启动失败降级：由 58 个自动化测试覆盖，运行在一个**严格模拟 2310 的假服务器**上（该假服务器刻意不实现 `/metadata` 和 `/projects/{id}/documents`）。
- ✅ 端点形状与 2304/2310 的 REST 文档、2310 发布说明一致。
- ⚠️ 未在真实客户实例上回归：不同项目模板（尤其是 workflow action、test record 的必填字段）可能有差异。**请先用 `POLARION_READ_ONLY=1` + `scripts/doctor.py` 验证读路径**，写路径先用 `dry_run`。

---

## 开发与测试

```bash
pip install -e ".[dev]"

# 启动假 Polarion 2310（可单独调试）
python tests/fake_polarion.py --port 8899 --verbose

# 全量测试（含 MCP stdio 端到端）
python -m pytest -v

# 静态检查
python -m pyflakes src tests
```

测试覆盖：

| 文件 | 内容 |
|---|---|
| `test_config.py` | URL 规范化、认证模式、配置校验 |
| `test_probe.py` | 2023 探测、强制 2025 失败、项目能力、PAT |
| `test_workitems.py` | 查询、分页、CRUD、dry_run、批量、修订 |
| `test_documents.py` | Spaces 回退、文档解析、Parts、Work Item 回填 |
| `test_testruns.py` | Test Run / Record / 汇总 |
| `test_errors.py` | 401/404/422、只读、能力说明 |
| `test_mcp_stdio.py` | 真实 MCP 握手、tools/list、tools/call |
| `test_server_surface.py` | 32 个工具与 3 个 Prompt 的名称/schema 稳定性、`dry_run` 覆盖 |

---

## 安全

- 凭据只从环境变量读取，不写入日志、不出现在工具返回值中。
- 建议初始以 `POLARION_READ_ONLY=1` 运行，确认读路径后再开放写。
- Polarion 按凭据所有者的权限鉴权；工具不能绕过项目权限。
- 工具返回的数据会发送给 AI 提供商，请遵守组织的数据策略。
- 上传附件限制 50 MB；下载文件名经 `basename` 清洗，防止路径穿越。

## License

MIT
