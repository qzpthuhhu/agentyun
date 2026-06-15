# Agent Cloud Drive

> Open-source, key-based cloud memory layer for AI agents.
> 类似 WPS 云备份 / 百度网盘的"无缝多端同步"心智，但主用户是 Agent，不是人。

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
![Version](https://img.shields.io/badge/version-v0.2.0-purple.svg)

## v0.2 新增能力

- **后台 Sync Daemon** —— `ac.sync.daemon_start()` 启动后台线程，本地变化自动 push 云端；定时拉远端增量
- **Embedding 语义搜索** —— 服务端用 sentence-transformers（all-MiniLM-L6-v2）自动 embed，cosine 相似度返回 top-k
- **Web 时间线 UI** —— `/web/timeline` 暗色主题，htmx 无刷新添加，关键词 + 语义搜索

![timeline](docs/screenshot-timeline.png)

## 核心心智

- **Key = 身份**：没有账号体系，一个 key = 一个 agent 的完整身份
- **后台自动同步**：本地操作 → 后台增量备份到云
- **跨设备跨 agent**：输入同一个 key → 拉回历史记忆
- **分享 = 把 key 给别人**（或导出只读子 key）

## 仓库结构

```
agentyun/
├── packages/
│   ├── sdk/        # Python SDK (agentyun)
│   ├── cli/        # CLI 工具 (agentyun 命令)
│   └── cloud/      # FastAPI 后端服务
├── docker-compose.yml
├── docs/
│   ├── architecture.md
│   └── screenshot-*.png
└── README.md
```

## 快速开始

### 1. 启动 Cloud 服务（开发模式，SQLite）

```bash
cd packages/cloud
uv venv --python python3.9 .venv
source .venv/bin/activate
uv pip install -e .
uvicorn app.main:app --reload --port 18000
```

或直接用 CLI：

```bash
agentyun server start
```

### 2. 注册一个 agent（设备A）

```bash
agentyun register --label my-agent
# 输出: 你的 master key (保存好!)
#   abc123...
# Saved to: /Users/you/.agentyun/credentials.json
```

### 3. 写记忆 & 启动后台同步

```bash
agentyun memory add "用户喜欢简洁回答" --type preference --tag user:zhang
agentyun memory add "今天讨论了产品设计" --type fact --tag project
agentyun sync daemon --start          # 后台自动 push/pull
agentyun memory list
agentyun memory search "用户偏好" --top 3
```

### 4. 在另一台设备登录（设备B）

```bash
agentyun login --key abc123...
agentyun sync daemon --start
agentyun memory list  # 看到了!
```

### 5. Web 时间线（任何浏览器）

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
| `/v1/memory` | POST | 加一条记忆（生成 memory.add 事件，自动 embed） |
| `/v1/memory` | GET | 列记忆（按 type/tag 过滤） |
| `/v1/memory/search` | POST | 语义搜索（cosine 相似度） |
| `/v1/assets/upload` | POST | 上传资产 |
| `/v1/assets/{id}/download` | GET | 下载资产 |
| `/web/home` | GET | Web UI 登录页 |
| `/web/timeline` | GET | Web UI 时间线（?key= 或 ?token=） |
| `/web/add` | POST | htmx 添加记忆 |

OpenAPI 文档：`http://localhost:18000/docs`

## Python SDK

```python
from agentyun import AgentCloud

# 注册（首次）
ac = AgentCloud.register("http://your-server:8000", label="my-agent")
ac.save()

# 启动后台 daemon
ac.sync.daemon_start(push_interval=1.0, pull_interval=5.0)

# 写记忆（daemon 自动 push）
ac.memory.add("用户喜欢简洁回答", type="preference", tags=["user:zhang"])

# 语义搜索
hits = ac.memory.search("用户偏好什么", top_k=5)
for h in hits:
    print(h.score, h.content)

# 同步状态
print(ac.sync.daemon_status())
```

## CLI 速查

```bash
agentyun register --label my-agent
agentyun login --key <KEY>
agentyun memory add "..." --type preference --tag user
agentyun memory list
agentyun memory search "query" --top 5
agentyun sync once
agentyun sync daemon --start          # 后台运行（Ctrl+C 停）
agentyun sync daemon --status
agentyun sync daemon --stop
agentyun status
agentyun whoami
agentyun server start                 # 启动 cloud 服务（开发模式）
```

## 数据模型：Event Sourcing

所有变更都写入 append-only event log。记忆、资产都是事件的查询视图。

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
```

### 同步幂等性

每次写事件传一个 `client_event_id`（建议 `uuid4().hex`）。如果同样的 `(key_id, client_event_id)` 已存在，服务器返回原 `event_id`，不会重复插入。

### Embedding 存储

服务端在写入 `memory.add` 事件时自动 embed content（sentence-transformers all-MiniLM-L6-v2，384 维），结果存到 `payload._embedding` 字段。搜索时 scan 所有 memory events（当前 key），cosine 相似度排序。

### 冲突解决

v0.1/v0.2 使用 **Last-Write-Wins**。v0.3 计划加入向量时钟或 CRDT。

## 部署

### 开发模式（SQLite + 本地磁盘）

```bash
agentyun server start
# 等价于：
cd packages/cloud && uvicorn app.main:app --reload
```

### 生产模式（Postgres + S3 + Cloud）

```bash
docker compose up -d
```

需要的环境变量：

```
AGENTYUN_DATABASE_URL=postgresql://user:pass@host:5432/agentyun
AGENTYUN_ASSET_STORAGE_DIR=/var/agentyun/assets
AGENTYUN_JWT_SECRET=<change-me>
```

## Roadmap

- [x] **v0.1**：Cloud + SDK + CLI + 同步闭环
- [x] **v0.2**：后台 daemon + Embedding 语义搜索 + Web 时间线 UI
- [ ] **v0.3**：分享子 key / 向量时钟冲突解决 / 向量索引优化（pgvector/sqlite-vec）
- [ ] **v1.0**：E2E 加密 / 团队协作 / 企业版 SLA

## 协议设计哲学

1. **主用户是 agent，不是人** —— 所有 API 设计优先 agent 编程友好，不优化给人看的 Web 流程
2. **Key = 身份，零注册摩擦** —— 没有邮箱/密码/手机号/验证码，5 秒上手
3. **Append-only log** —— 天然支持回放、时间旅行、审计
4. **开放数据格式** —— 不绑死某个 agent runtime，event 走 JSON 自描述

## License

Apache 2.0