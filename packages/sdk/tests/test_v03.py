"""v0.3 tests: share + vector index.

Run with cloud service on http://127.0.0.1:18000:
    pytest packages/sdk/tests/test_v03.py -v
"""
import os
import tempfile

import pytest


SERVER = os.environ.get("AGENTYUN_SERVER", "http://127.0.0.1:18000")


@pytest.fixture
def tmp_data_dir():
    return tempfile.mkdtemp(prefix="agentyun-v03-")


def test_share_create_consume_timeline(tmp_data_dir):
    """Owner creates a share, consumer reads the timeline."""
    from agentyun import AgentCloud, SDKConfig

    cfg = SDKConfig(server_url=SERVER, data_dir=tmp_data_dir + "/owner", timeout_seconds=60)
    ac = AgentCloud.register(SERVER, label="share-owner", config=cfg)
    ac.memory.add("share 测试 1", type="fact")
    ac.memory.add("share 测试 2", type="fact")

    token, info = ac.share.create(permissions="read", expires_in=3600)
    assert token
    assert info.share_id
    assert info.permissions == "read"

    # Consume (no auth needed)
    shared = AgentCloud.connect_share(token, server_url=SERVER)
    items = shared.timeline(limit=10)
    assert len(items) >= 2
    assert any("share 测试 1" in it.content for it in items)


def test_share_search_via_token(tmp_data_dir):
    """Consumer can semantic-search owner's memory via share token."""
    from agentyun import AgentCloud, SDKConfig

    cfg = SDKConfig(server_url=SERVER, data_dir=tmp_data_dir + "/owner", timeout_seconds=120)
    ac = AgentCloud.register(SERVER, label="share-search", config=cfg)
    ac.memory.add("用户喜欢简洁回答", type="preference")
    ac.memory.add("用户偏好技术深度", type="preference")
    ac.memory.add("成都天气热", type="note")

    token, _ = ac.share.create(permissions="read_memory", expires_in=3600)

    shared = AgentCloud.connect_share(token, server_url=SERVER)
    hits = shared.search("用户偏好什么", top_k=2)
    assert len(hits) >= 1
    assert hits[0]["memory_type"] == "preference"


def test_share_revoke_blocks_access(tmp_data_dir):
    """Revoking a share should make subsequent reads fail."""
    from agentyun import AgentCloud, SDKConfig
    from agentyun.client import APIError

    cfg = SDKConfig(server_url=SERVER, data_dir=tmp_data_dir + "/owner", timeout_seconds=60)
    ac = AgentCloud.register(SERVER, label="share-revoke", config=cfg)
    ac.memory.add("revoke 测试", type="fact")

    token, info = ac.share.create(permissions="read", expires_in=3600)
    ac.share.revoke(info.share_id)

    shared = AgentCloud.connect_share(token, server_url=SERVER)
    with pytest.raises(APIError) as ei:
        shared.timeline(limit=10)
    assert ei.value.status_code == 404


def test_share_read_only_blocks_search(tmp_data_dir):
    """'read' permission must NOT allow semantic search."""
    from agentyun import AgentCloud, SDKConfig
    from agentyun.client import APIError

    cfg = SDKConfig(server_url=SERVER, data_dir=tmp_data_dir + "/owner", timeout_seconds=60)
    ac = AgentCloud.register(SERVER, label="share-readonly", config=cfg)
    ac.memory.add("read only 测试", type="fact")

    token, _ = ac.share.create(permissions="read", expires_in=3600)
    shared = AgentCloud.connect_share(token, server_url=SERVER)

    # Search should be forbidden
    with pytest.raises(APIError) as ei:
        shared.search("test", top_k=3)
    assert ei.value.status_code == 403


def test_vector_index_used_for_search(tmp_data_dir):
    """Search should hit the vector index (sqlite-vec or pgvector in dev/prod)."""
    from agentyun import AgentCloud, SDKConfig

    cfg = SDKConfig(server_url=SERVER, data_dir=tmp_data_dir + "/vec", timeout_seconds=120)
    ac = AgentCloud.register(SERVER, label="vec-test", config=cfg)
    for c, t in [
        ("用户喜欢简洁回答", "preference"),
        ("Python 是动态类型语言", "fact"),
        ("FastAPI 是 web 框架", "fact"),
        ("成都的天气闷热", "note"),
    ]:
        ac.memory.add(c, type=t, tags=[t])

    # Should return preference hits
    hits = ac.memory.search("用户偏好什么", top_k=3)
    assert len(hits) >= 1
    assert hits[0].memory_type == "preference"


def test_share_list(tmp_data_dir):
    """Owner can list all active shares."""
    from agentyun import AgentCloud, SDKConfig

    cfg = SDKConfig(server_url=SERVER, data_dir=tmp_data_dir + "/list", timeout_seconds=60)
    ac = AgentCloud.register(SERVER, label="share-list", config=cfg)

    # Initially empty
    assert ac.share.list() == []

    # Create two
    ac.share.create(permissions="read", expires_in=3600, label="one")
    ac.share.create(permissions="read_memory", expires_in=3600, label="two")

    items = ac.share.list()
    assert len(items) == 2
    labels = {it.label for it in items}
    assert labels == {"one", "two"}