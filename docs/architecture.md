# Architecture

## Overview

Agent Cloud Drive is a key-based cloud memory layer for AI agents. The model is
"WPS-style cloud backup for agent memory":

```
┌─────────────────────────────────────────────────────────┐
│  Python SDK  (agentcloud)                                  │
│  CLI  (agentcloud login / sync / ls / share / restore)    │
├─────────────────────────────────────────────────────────┤
│  Sync Daemon (本地常驻进程) [v0.2+]                       │
│  Local Cache  (SQLite WAL)                               │
└─────────────────────────────────────────────────────────┘
                              ↕ HTTPS / JWT
┌─────────────────────────────────────────────────────────┐
│  Cloud (FastAPI + SQLAlchemy)                            │
│  ├─ Auth: Key (32B) + Recovery Code (24B)               │
│  ├─ Storage: Event log (Postgres) + Asset (S3/disk)      │
│  ├─ Indexing: pgvector (Postgres)  [v0.2]              │
│  └─ Sharing: sub-key / share token  [v0.3]             │
└─────────────────────────────────────────────────────────┘
```

## Identity

There is **no traditional user account system**. Identity is purely the key:

- **Master key**: 32 random bytes, base58-encoded (~44 chars). SHA-256 hashed for storage.
- **Recovery code**: 24 random bytes, base58-encoded (~32 chars). Bcrypt hashed.
- After `POST /v1/auth/login`, server returns a **JWT access token** (30-day TTL).
- All subsequent calls send `Authorization: Bearer <jwt>`.

`POST /v1/auth/recover` accepts a recovery code, **preserves the same key_id**, and issues a new master key. Old key is invalidated.

## Data model

### Events (append-only log)

```sql
events (
  event_id        BIGSERIAL PK,
  key_id          TEXT FK,
  type            TEXT,           -- 'memory.add', 'memory.update', 'memory.delete', 'asset.upload', ...
  payload         JSONB,
  client_ts       TIMESTAMPTZ,
  server_ts       TIMESTAMPTZ DEFAULT now(),
  client_event_id TEXT             -- client-side dedup key
)
```

Memory items are a derived query view over `events WHERE type IN ('memory.add', 'memory.update')`.

### Assets

```sql
assets (
  asset_id     TEXT PK,
  key_id       TEXT FK,
  filename     TEXT,
  mime         TEXT,
  size         BIGINT,
  storage_path TEXT,        -- local path or S3 key
  meta         JSONB,
  created_at   TIMESTAMPTZ
)
```

### Keys

```sql
keys (
  key_id        TEXT PK,
  key_hash      BYTEA UNIQUE,    -- SHA-256(master_key)
  recovery_hash BYTEA,           -- bcrypt(recovery_code)
  label         TEXT,
  created_at    TIMESTAMPTZ,
  revoked_at    TIMESTAMPTZ NULL
)
```

## Sync protocol

```
Client                                Server
  |                                      |
  |  POST /v1/auth/login {key}           |
  |------------------------------------->|
  |                                      |
  |<-- {access_token, expires_at, ...}   |
  |                                      |
  |  POST /v1/memory {content,type,...}  |
  |------------------------------------->|
  |                                      |
  |<-- {event_id}                        |
  |                                      |
  |  GET /v1/events?since=N              |
  |------------------------------------->|
  |                                      |
  |<-- {events: [...], has_more, next_since}
  |                                      |
```

### Idempotency

Clients should send a stable `client_event_id` (e.g. `uuid4().hex`). If the same
pair `(key_id, client_event_id)` already exists, the server returns the existing
`event_id` instead of inserting a duplicate. This makes sync safe under retries
and offline re-uploads.

### Conflict resolution

For v0.1 we use **Last-Write-Wins** keyed by `server_ts`. There is no
multi-master conflict resolution. For v0.2 we may add CRDT or vector clocks.

## Why Event Sourcing?

- **Offline-first**: client can write to local WAL when network is down, sync later.
- **Replay**: any new client can reconstruct history by replaying all events.
- **Time-travel**: easy to add "memory at time T" later.
- **Audit**: every change is logged with who/what/when.

## Storage backend

v0.1 supports SQLite + local disk (dev mode). v0.2+ adds Postgres + pgvector +
S3-compatible storage. Production users should switch via env vars:

```
AGENTCLOUD_DATABASE_URL=postgresql://...
AGENTCLOUD_ASSET_STORAGE_DIR=/var/agentcloud/assets
```

(Use S3 by switching the assets router to boto3; tracked for v0.2.)

## Security

v0.1:
- TLS via reverse proxy (Caddy / nginx / cloud LB).
- JWT with HS256; rotate `AGENTCLOUD_JWT_SECRET` regularly.
- At-rest encryption for asset files (filesystem / disk level).

v0.2+:
- E2E encryption: key derivation so server cannot read plaintext.
- Audit log of all access.

## Open questions

- **Embedding-based semantic search**: requires local embedding model on client
  OR server-side embed (which would preclude E2E).
- **Multi-device conflict**: when two devices write offline, last-write-wins may
  lose data. We may need vector clocks or per-field CRDTs.
- **Sharing sub-keys**: read-only vs. read+memory-write vs. full-control — what
  is the minimum useful set?