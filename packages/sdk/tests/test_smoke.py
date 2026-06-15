"""Smoke tests for SDK against a running cloud server.

Assumes:
  - Cloud service running on http://127.0.0.1:18000
  - Uses a temp data dir

Run:
  pytest packages/sdk/tests/test_smoke.py -v
"""
import os
import tempfile

import pytest


@pytest.fixture
def tmp_data_dir():
    return tempfile.mkdtemp(prefix="agentcloud-test-")


def test_register_login_memory_sync(tmp_data_dir):
    """Full E2E: register, add memory, simulate second device, sync."""
    from agentcloud import AgentCloud, SDKConfig
    from agentcloud.client import Credentials
    import json

    server = os.environ.get("AGENTCLOUD_SERVER", "http://127.0.0.1:18000")

    # Device A
    cfg_a = SDKConfig(server_url=server, data_dir=tmp_data_dir + "/A")
    ac_a = AgentCloud.register(server, label="device-A", config=cfg_a)
    assert ac_a.credentials().key_id

    # Add memories
    eid_1 = ac_a.memory.add("用户喜欢简洁回答", type="preference", tags=["user:zhang"])
    eid_2 = ac_a.memory.add("今天讨论了产品设计", type="fact", tags=["project:agentcloud"])
    assert eid_1 > 0
    assert eid_2 > eid_1

    # Save and reload from disk
    ac_a.save()
    creds_data = json.loads(cfg_a.credentials_path.read_text())
    assert creds_data["key"] == ac_a.credentials().key

    # Device B: load same key, sync
    cfg_b = SDKConfig(server_url=server, data_dir=tmp_data_dir + "/B")
    creds = Credentials.from_dict(creds_data)
    ac_b = AgentCloud.from_credentials(creds, config=cfg_b)

    result = ac_b.sync.once()
    assert result["pulled"] >= 2

    items = ac_b.memory.list()
    assert len(items) >= 2
    contents = [it.content for it in items]
    assert any("简洁回答" in c for c in contents)


def test_idempotent_memory_add(tmp_data_dir):
    """Same client_event_id should not create duplicate events."""
    from agentcloud import AgentCloud, SDKConfig
    import uuid

    server = os.environ.get("AGENTCLOUD_SERVER", "http://127.0.0.1:18000")
    cfg = SDKConfig(server_url=server, data_dir=tmp_data_dir + "/idem")
    ac = AgentCloud.register(server, label="idem", config=cfg)

    eid1 = ac.memory.add("重复测试", client_event_id="evt-xyz")
    eid2 = ac.memory.add("重复测试", client_event_id="evt-xyz")
    assert eid1 == eid2, "Same client_event_id should return same remote event_id"


def test_recovery_preserves_identity(tmp_data_dir):
    """Recover should issue a new key but keep the same key_id."""
    from agentcloud import AgentCloud, SDKConfig
    import httpx

    server = os.environ.get("AGENTCLOUD_SERVER", "http://127.0.0.1:18000")

    cfg = SDKConfig(server_url=server, data_dir=tmp_data_dir + "/recover")
    ac = AgentCloud.register(server, label="recover", config=cfg)
    old_key_id = ac.credentials().key_id

    # Find recovery code by re-registering another agent - we need it from server
    # Actually, register() returns it but we don't expose it via SDK yet.
    # For this test, we'll call the API directly.
    resp = httpx.post(
        server + "/v1/auth/register",
        json={"label": "recover-target"},
        timeout=10,
    )
    data = resp.json()
    recovery = data["recovery_code"]
    target_key_id = data["key_id"]

    # Now recover
    resp = httpx.post(
        server + "/v1/auth/recover",
        json={"recovery_code": recovery},
        timeout=10,
    )
    assert resp.status_code == 200
    new_data = resp.json()
    assert new_data["key_id"] == target_key_id, "key_id should be preserved"
    assert new_data["key"] != data["key"], "a new key should be issued"