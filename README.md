# AgentCloud

> Open-source, key-based cloud memory layer for AI agents.
> 类似 WPS 云备份 / 百度网盘的"无缝多端同步"心智，但主用户是 Agent，不是人。

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
![Version](https://img.shields.io/badge/version-v0.3.0-purple.svg)
![Tests](https://img.shields.io/badge/tests-15%2F15_passing-brightgreen)

## v0.3 新增能力

- **分享子 key（Share Tokens）** —— `ac.share.create()` 生成可撤销、可设过期、可设权限（read / read_memory / full）的 token；他人凭 token 消费你的记忆 timeline + 语义搜索，无需账号
- **生产级向量索引** —— 自动选择后端：dev 用 **sqlite-vec**（10-100x 快于 numpy 扫表），生产用 **pgvector**（ANN cosine 距离），零配置 fallback 到 numpy
- **轻量级 schema 迁移** —— `init_db()` 检测缺失列并自动 ALTER TABLE

## v0.2 已有能力

- 后台 Sync Daemon（`ac.sync.daemon_start()`）
- Embedding 语义搜索（sentence-transformers all-MiniLM-L6-v2）
- Web 时间线 UI（暗色主题、htmx 无刷新）

![timeline](docs/screenshot-timeline.png)

## 核心心智

- **Key = 身份**：没有账号体系，一个 key = 一个 agent 的完整身份
- **后台自动同步**：本地操作 → 后台增量备份到云
- **跨设备跨 agent**：输入同一个 key → 拉回历史记忆
- **分享 = 把 key 给别人**（或用 `agentcloud share create` 生成可撤销的子 token）

## 仓库结构

```
agentcloud/
├── packages/
│   ├── sdk/        # Python SDK (agentcloud)
│   ├── cli/        # CLI 工具 (agentcloud 命令)
│   └── cloud/      # FastAPI 后端服务
├── docker-compose.yml
├── docs/
│   ├── architecture.md
│   └── screenshot-*.png
└── README.md
```

## 快速开始

### 1. 启动 Cloud 服务（开发模式，SQLite + sqlite-vec）

```bash
cd packages/cloud
uv venv --python python3.9 .venv
source .venv/bin/activate
uv pip install -e .
uvicorn app.main:app --reload --port 18000
```

或直接用 CLI：

```bash
agentcloud server start
```

### 2. 注册一个 agent

```bash
agentcloud register --label my-agent
# 输出: 你的 master key (保存好!)
```

### 3. 写记忆 & 后台同步

```bash
agentcloud memory add "用户喜欢简洁回答" --type preference --tag user:zhang
agentcloud memory add "今天讨论了产品设计" --type fact --tag project
agentcloud sync daemon --start          # 后台自动 push/pull
agentcloud memory list
agentcloud memory search "用户偏好" --top 3
```

### 4. 跨设备 / 跨 agent

```bash
# 设备 B
agentcloud login --key <KEY>
agentcloud sync daemon --start
agentcloud memory list  # 看到了!
```

### 5. 分享给特定人（v0.3 新）

```bash
# 主人：创建分享
agentcloud share create --permissions read_memory --expires-in 86400 --label for-friend
# 输出: share token (给对方一次)

# 对方：消费
agentcloud share consume <TOKEN>
agentcloud share consume <TOKEN> -q "用户偏好什么"
```

### 6. Web 时间线

打开 `http://localhost:18000/web/home`，输入 master key 即可浏览时间线 + 语义搜索。

## API 概览

| 路径 | 方法 | 说明 |
|------|------|------|
| `/v1/auth/register` | POST | 注册新 agent，返回 key + recovery_code |
| `/v1/auth/login` | POST | 用 key 换 JWT |
| `/v1/auth/recover` | POST | 用 recovery_code 重置 key（保留身份） |
| `/v1/auth/me` | GET | 当前身份 |
| `/v1/events` | POST | 批量追加事件（idempotent） |
| `/v1/events` | GET | 拉取事件（增量同步） |
| `/v1/memory` | POST | 加一条记忆（自动 embed + 写向量索引） |
| `/v1/memory` | GET | 列记忆（按 type/tag 过滤） |
| `/v1/memory/search` | POST | 语义搜索（走向量索引） |
| `/v1/share` | POST | 创建分享 token（owner 端） |
| `/v1/share` | GET | 列出我的所有 share |
| `/v1/share/{id}` | DELETE | 撤销 share |
| `/v1/share/{token}/info` | GET | 公开查询 share 元信息 |
| `/v1/share/{token}/timeline` | GET | 通过 share token 读 timeline |
| `/v1/share/{token}/search` | POST | 通过 share token 语义搜索 |
| `/v1/assets/upload` | POST | 上传资产 |
| `/v1/assets/{id}/download` | GET | 下载资产 |
| `/web/home` | GET | Web UI 登录页 |
| `/web/timeline` | GET | Web UI 时间线（?key= 或 ?token=） |
| `/web/add` | POST | htmx 添加记忆 |

OpenAPI 文档：`http://localhost:18000/docs`

## Python SDK

```python
from agentcloud import AgentCloud

# 注册（首次）
ac = AgentCloud.register("http://your-server:8000", label="my-agent")
ac.save()

# 启动后台 daemon
ac.sync.daemon_start(push_interval=1.0, pull_interval=5.0)

# 写记忆
ac.memory.add("用户喜欢简洁回答", type="preference", tags=["user:zhang"])

# 语义搜索
hits = ac.memory.search("用户偏好什么", top_k=5)

# 分享给特定人
token, info = ac.share.create(permissions="read_memory", expires_in=86400)
# 把 token 发给朋友

# 消费别人的分享（无需账号）
shared = AgentCloud.connect_share(token, server_url="http://friend-server:8000")
their_items = shared.timeline(limit=20)
their_hits = shared.search("用户偏好", top_k=3)
```

## CLI 速查

```bash
# 注册 / 登录
agentcloud register --label my-agent
agentcloud login --key <KEY>
agentcloud whoami

# 记忆
agentcloud memory add "..." --type preference --tag user
agentcloud memory list
agentcloud memory search "query" --top 5

# 同步
agentcloud sync once
agentcloud sync daemon --start          # 后台运行（Ctrl+C 停）
agentcloud sync daemon --status
agentcloud sync daemon --stop
agentcloud status

# 分享（v0.3）
agentcloud share create --permissions read_memory --expires-in 86400 --label for-friend
agentcloud share list
agentcloud share revoke <share_id>
agentcloud share consume <TOKEN>
agentcloud share consume <TOKEN> -q "user preferences"

# Cloud 服务
agentcloud server start
```

## 数据模型：Event Sourcing

所有变更都写入 append-only event log。

```sql
events (
  event_id        BIGSERIAL PK,
  key_id          TEXT FK,
  type            TEXT,           -- 'memory.add', 'memory.update', 'asset.upload', ...
  payload         JSONB,          -- 含 content, tags, meta, _embedding
  client_ts       TIMESTAMPTZ,
  server_ts       TIMESTAMPTZ,
  client_event_id TEXT
)

shares (
  share_id        TEXT PK,
  parent_key_id   TEXT FK,
  token_hash      BYTEA,          -- SHA-256 of the raw share token
  permissions     TEXT,           -- 'read' | 'read_memory' | 'full'
  label           TEXT,
  expires_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ,
  revoked_at      TIMESTAMPTZ
)
```

### 同步幂等性

每次写事件传一个 `client_event_id`（建议 `uuid4().hex`）。如果同样的 `(key_id, client_event_id)` 已存在，服务器返回原 `event_id`，不会重复插入。

### 向量索引

服务端在写入 `memory.add` 事件时自动 embed content（sentence-transformers all-MiniLM-L6-v2，384 维）。v0.3 起写入到 `VectorIndex`，自动选择后端：

| 后端 | 何时使用 | 性能 |
|------|---------|------|
| **numpy**（默认 fallback） | 无 sqlite-vec / pgvector | O(N) 扫表 |
| **sqlite-vec** | SQLite dev mode（默认） | 10-100x 快 |
| **pgvector** | Postgres 生产（默认） | ANN cosine distance |

可强制指定：`AGENTCLOUD_VECTOR_BACKEND=auto|numpy|sqlite_vec|pgvector`。

### 冲突解决

v0.1-v0.3 使用 **Last-Write-Wins**。

## 部署

### 开发模式（SQLite + 本地磁盘 + sqlite-vec）

```bash
agentcloud server start
# 等价于：
cd packages/cloud && uvicorn app.main:app --reload
```

### 生产模式（Postgres + S3 + Cloud + pgvector）

```bash
docker compose up -d
```

环境变量：

```
AGENTCLOUD_DATABASE_URL=postgresql://user:pass@host:5432/agentcloud
AGENTCLOUD_ASSET_STORAGE_DIR=/var/agentcloud/assets
AGENTCLOUD_JWT_SECRET=<change-me>
AGENTCLOUD_VECTOR_BACKEND=pgvector  # or auto
```

## Roadmap

- [x] **v0.1**：Cloud + SDK + CLI + 同步闭环
- [x] **v0.2**：后台 daemon + Embedding 语义搜索 + Web 时间线 UI
- [x] **v0.3**：分享子 key + 向量索引（pgvector / sqlite-vec）
- [ ] **v0.4**：vector clock 冲突解决 / 资产 S3 切换 / Web share 页面
- [ ] **v1.0**：E2E 加密 / 团队协作 / 企业版 SLA

## 协议设计哲学

1. **主用户是 agent，不是人** —— 所有 API 设计优先 agent 编程友好
2. **Key = 身份，零注册摩擦** —— 没有邮箱/密码/手机号/验证码，5 秒上手
3. **Append-only log** —— 天然支持回放、时间旅行、审计
4. **开放数据格式** —— 不绑死某个 agent runtime，event 走 JSON 自描述
5. **Share = 主权延伸** —— 不是"平台权限"，是 owner 主动签署的"代理证书"

## License

Apache 2.0