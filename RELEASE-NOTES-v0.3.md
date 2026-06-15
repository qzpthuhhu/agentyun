# v0.3.0: Share Tokens + Production-Grade Vector Index

## What's New

### 🔑 Share Tokens (sub-key sharing)

Let specific people/agents read your agent's memory — no account required.

```bash
# Owner: create a share
agentyun share create --permissions read_memory --expires-in 86400 --label for-friend
# Output: a URL-safe token (43 chars)

# Consumer: read the shared memory
agentyun share consume <TOKEN>
agentyun share consume <TOKEN> -q "user preferences"
```

**Permissions**:
- `read` — list memory only
- `read_memory` — list + semantic search (default)
- `full` — list + search + assets (planned v0.4)

**SDK**:
```python
# Owner
token, info = ac.share.create(permissions="read_memory", expires_in=3600)

# Consumer (no credentials)
shared = AgentCloud.connect_share(token, server_url="http://...")
items = shared.timeline(limit=20)
hits = shared.search("user preferences", top_k=5)
```

Tokens are SHA-256 hashed at rest; show-once; revocable.

### ⚡ Vector Index (sqlite-vec / pgvector)

Server-side search now uses a proper ANN index instead of scanning all events in memory.

| Backend | When | Performance |
|---------|------|-------------|
| `numpy` (fallback) | no extensions | O(N) scan |
| `sqlite-vec` (default dev) | SQLite mode | 10-100× faster |
| `pgvector` (default prod) | Postgres | ANN cosine distance |

Auto-detected from `AGENTYUN_DATABASE_URL`. Override with `AGENTYUN_VECTOR_BACKEND`.

### 🛠 Lightweight Migrations

`init_db()` now detects missing columns and `ALTER TABLE`s them in. v0.2 databases auto-upgrade to v0.3 schema (added `shares.label`) without manual migration.

## Tests

15/15 passing (3 v0.1 + 6 v0.2 + 6 v0.3).

## What's Next (v0.4)

- Vector clock conflict resolution (CRDT or per-field LWW)
- Asset upload to S3 (currently local disk in v0.3)
- Web share-by-link page (paste a token, see the timeline)
- MCP / LangChain / AutoGen integration packages
