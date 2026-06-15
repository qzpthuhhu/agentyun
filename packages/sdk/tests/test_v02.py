"""v0.2 tests: daemon + semantic search + web UI.

Run with cloud service on http://127.0.0.1:18000:
    pytest packages/sdk/tests/test_v02.py -v
"""
import os
import tempfile
import time

import pytest
import requests


SERVER = os.environ.get("AGENTCLOUD_SERVER", "http://127.0.0.1:18000")


@pytest.fixture
def tmp_data_dir():
    return tempfile.mkdtemp(prefix="agentcloud-v02-")


def test_daemon_pushes_locally_on_add(tmp_data_dir):
    """After ac.memory.add(), the daemon should auto-push within a few seconds."""
    from agentcloud import AgentCloud, SDKConfig

    cfg = SDKConfig(server_url=SERVER, data_dir=tmp_data_dir + "/daemon", timeout_seconds=60)
    ac = AgentCloud.register(SERVER, label="daemon-push", config=cfg)

    daemon = ac.sync.daemon_start(push_interval=0.5, pull_interval=2.0)
    try:
        time.sleep(0.5)
        ac.memory.add("daemon 应该自动 push", type="fact", tags=["daemon-test"])
        # Wait up to 3s for daemon to push
        for _ in range(30):
            s = daemon.status()
            if s["stats"]["pushed_total"] >= 1 and s["local"]["unsynced"] == 0:
                break
            time.sleep(0.1)
        s = daemon.status()
        assert s["stats"]["pushed_total"] >= 1
        assert s["local"]["unsynced"] == 0
    finally:
        daemon.stop()


def test_daemon_pulls_remote_updates(tmp_data_dir):
    """Daemon should auto-pull remote events created by other clients."""
    from agentcloud import AgentCloud, SDKConfig
    from agentcloud.client import Credentials
    import json

    cfg_a = SDKConfig(server_url=SERVER, data_dir=tmp_data_dir + "/A", timeout_seconds=60)
    ac_a = AgentCloud.register(SERVER, label="daemon-A", config=cfg_a)
    ac_a.save()

    cfg_b = SDKConfig(server_url=SERVER, data_dir=tmp_data_dir + "/B", timeout_seconds=60)
    creds = Credentials.from_dict(json.loads(cfg_a.credentials_path.read_text()))
    ac_b = AgentCloud.from_credentials(creds, config=cfg_b)

    daemon_b = ac_b.sync.daemon_start(push_interval=0.5, pull_interval=0.5)
    try:
        time.sleep(0.5)
        # A writes something
        ac_a.memory.add("A 写一条 - B 应该自动拉到", type="fact")
        # Wait for B to pull
        for _ in range(40):
            s = daemon_b.status()
            if s["local"]["last_remote_event_id"] > 0:
                break
            time.sleep(0.1)
        assert daemon_b.status()["local"]["last_remote_event_id"] > 0
    finally:
        daemon_b.stop()


def test_semantic_search_returns_relevant_hits(tmp_data_dir):
    """Semantic search should return semantically related memories."""
    from agentcloud import AgentCloud, SDKConfig

    cfg = SDKConfig(server_url=SERVER, data_dir=tmp_data_dir + "/search", timeout_seconds=120)
    ac = AgentCloud.register(SERVER, label="semantic", config=cfg)

    ac.memory.add("用户喜欢简洁、直接的回答", type="preference")
    ac.memory.add("用户偏好技术性深度解释", type="preference")
    ac.memory.add("成都的天气最近很闷热", type="note")
    ac.memory.add("Python 是动态类型语言", type="fact")

    # Search for user preferences
    hits = ac.memory.search("用户喜欢什么风格", top_k=2)
    assert len(hits) >= 1
    # Top hit should be a preference about user style
    assert hits[0].memory_type == "preference"
    assert any(word in hits[0].content for word in ["简洁", "技术", "偏好"])


def test_web_home_renders():
    """Web UI home page should return 200 with HTML."""
    r = requests.get(f"{SERVER}/web/home", timeout=10)
    assert r.status_code == 200
    assert "Agent Cloud Drive" in r.text


def test_web_timeline_with_key(tmp_data_dir):
    """Web UI timeline should render with valid key."""
    from agentcloud import AgentCloud, SDKConfig

    cfg = SDKConfig(server_url=SERVER, data_dir=tmp_data_dir + "/web", timeout_seconds=60)
    ac = AgentCloud.register(SERVER, label="web-test", config=cfg)
    ac.memory.add("web 测试记忆", type="fact")

    r = requests.get(
        f"{SERVER}/web/timeline",
        params={"key": ac.credentials().key},
        timeout=10,
    )
    assert r.status_code == 200
    assert "web 测试记忆" in r.text


def test_web_search_renders_hits(tmp_data_dir):
    """Web UI search should render scored hits."""
    from agentcloud import AgentCloud, SDKConfig

    cfg = SDKConfig(server_url=SERVER, data_dir=tmp_data_dir + "/websearch", timeout_seconds=120)
    ac = AgentCloud.register(SERVER, label="web-search", config=cfg)
    ac.memory.add("Python 是动态类型语言", type="fact")
    ac.memory.add("FastAPI 是一个 web 框架", type="fact")
    ac.memory.add("成都天气很热", type="note")

    r = requests.get(
        f"{SERVER}/web/timeline",
        params={"key": ac.credentials().key, "q": "Python 编程"},
        timeout=30,
    )
    assert r.status_code == 200
    assert "score" in r.text.lower()  # search result fragment renders score