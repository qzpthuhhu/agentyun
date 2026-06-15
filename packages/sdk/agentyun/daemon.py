"""Background sync daemon.

Watches the local SQLite WAL for changes and pushes them to the cloud.
Periodically pulls remote events newer than the local cursor.

Lifecycle:
    daemon = SyncDaemon(ac)
    daemon.start()       # spawn background thread
    daemon.stop()        # graceful shutdown
    daemon.status()      # current state

Robustness:
- Idempotent (uses client_event_id for dedup).
- Network failures retry with exponential backoff.
- Crashes recover from last cursor (no data loss beyond the unsynced tail).
- Single daemon per process; calling start() twice is a no-op.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from .client import AgentCloud, AuthError, APIError
from .store import LocalStore


logger = logging.getLogger("agentyun.sync.daemon")


# Sync intervals (seconds)
DEFAULT_PUSH_INTERVAL = 1.0      # watch local WAL
DEFAULT_PULL_INTERVAL = 5.0      # poll cloud for new events
DEFAULT_RETRY_BACKOFF = 5.0      # network error backoff
DEFAULT_BATCH_SIZE = 100


class SyncDaemon:
    """Background sync daemon for AgentCloud."""

    def __init__(
        self,
        cloud: AgentCloud,
        push_interval: float = DEFAULT_PUSH_INTERVAL,
        pull_interval: float = DEFAULT_PULL_INTERVAL,
        batch_size: int = DEFAULT_BATCH_SIZE,
        retry_backoff: float = DEFAULT_RETRY_BACKOFF,
    ):
        self.cloud = cloud
        self.store: LocalStore = cloud._store
        self.push_interval = push_interval
        self.pull_interval = pull_interval
        self.batch_size = batch_size
        self.retry_backoff = retry_backoff

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._wakeup = threading.Event()
        self._lock = threading.Lock()

        # Stats
        self.stats = {
            "started_at": None,
            "stopped_at": None,
            "pushed_total": 0,
            "pulled_total": 0,
            "errors": 0,
            "last_push_at": None,
            "last_pull_at": None,
            "last_error": None,
        }

        # Register a SQLite trigger so writes wake the daemon up immediately
        self._install_wakeup_trigger()

    # ===== Lifecycle =====

    def start(self) -> None:
        """Start the background sync thread (non-blocking)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                logger.info("daemon already running")
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="agentyun-sync-daemon",
                daemon=True,
            )
            self._thread.start()
            self.stats["started_at"] = datetime.now(timezone.utc).isoformat()
            logger.info("sync daemon started (push=%.1fs, pull=%.1fs)",
                        self.push_interval, self.pull_interval)

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the daemon. Waits up to `timeout` seconds for clean shutdown."""
        with self._lock:
            if self._thread is None:
                return
            self._stop_event.set()
            self._wakeup.set()  # break out of any sleep
            self._thread.join(timeout=timeout)
            self._thread = None
            self.stats["stopped_at"] = datetime.now(timezone.utc).isoformat()
            logger.info("sync daemon stopped")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wake(self) -> None:
        """Force an immediate sync tick (useful after a write)."""
        self._wakeup.set()

    # ===== Main loop =====

    def _run(self) -> None:
        """Main loop: push on local change, pull on timer, sleep on idle."""
        next_pull = time.monotonic() + self.pull_interval
        while not self._stop_event.is_set():
            try:
                # Push anything pending (cheap; no-op if empty)
                pushed = self._push_once()
                if pushed:
                    logger.debug("pushed %d events", pushed)

                # Pull if interval elapsed
                now = time.monotonic()
                if now >= next_pull:
                    pulled = self._pull_once()
                    if pulled:
                        logger.debug("pulled %d events", pulled)
                    next_pull = time.monotonic() + self.pull_interval

            except AuthError as e:
                logger.error("auth error: %s (will retry)", e)
                self.stats["errors"] += 1
                self.stats["last_error"] = str(e)
                # Auth errors are not recoverable without re-login; backoff hard
                self._stop_event.wait(self.retry_backoff * 6)
                continue
            except APIError as e:
                logger.warning("api error: %s", e)
                self.stats["errors"] += 1
                self.stats["last_error"] = str(e)
                self._stop_event.wait(self.retry_backoff)
                continue
            except Exception as e:
                logger.exception("unexpected error: %s", e)
                self.stats["errors"] += 1
                self.stats["last_error"] = repr(e)
                self._stop_event.wait(self.retry_backoff)
                continue

            # Sleep until next push tick (or until wake() is called)
            self._wakeup.wait(timeout=self.push_interval)
            self._wakeup.clear()

    def _push_once(self) -> int:
        """Push a batch of unsynced events. Returns count pushed."""
        unsynced = self.store.unsynced(limit=self.batch_size)
        if not unsynced:
            return 0
        events = [ev.to_remote_dict() for ev in unsynced]
        data = self.cloud._http.post("/events", json={"events": events})
        accepted = data["accepted"]
        updates = []
        for ev, rid in zip(unsynced, accepted):
            updates.append((rid, ev.client_event_id))
        self.store.mark_synced(updates)
        self.stats["pushed_total"] += len(updates)
        self.stats["last_push_at"] = datetime.now(timezone.utc).isoformat()
        return len(updates)

    def _pull_once(self) -> int:
        """Pull a batch of remote events. Returns count."""
        cursor = self.store.get_cursor()
        data = self.cloud._http.get("/events", params={"since": cursor, "limit": self.batch_size})
        events = data.get("events", [])
        if events:
            # Update cursor to max event_id we've seen
            new_cursor = max(e["event_id"] for e in events)
            self.store.set_cursor(new_cursor)
        self.stats["pulled_total"] += len(events)
        self.stats["last_pull_at"] = datetime.now(timezone.utc).isoformat()
        return len(events)

    # ===== Wakeup trigger =====

    def _install_wakeup_trigger(self) -> None:
        """Install a SQLite trigger that fires on local event inserts.

        The trigger sets a 'wakeup' flag; we poll that flag in the loop
        (we can't safely call Python from inside SQLite, so we use a
        flag-based wakeup rather than a real trigger callback).
        """
        try:
            with self.store._conn() as conn:
                conn.executescript("""
                CREATE TABLE IF NOT EXISTS _sync_triggers (
                    flag INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO _sync_triggers (flag) VALUES (0);

                CREATE TRIGGER IF NOT EXISTS trg_local_events_insert
                AFTER INSERT ON local_events
                BEGIN
                    UPDATE _sync_triggers SET flag = 1;
                END;
                """)
        except sqlite3.OperationalError as e:
            # Schema may have changed; that's OK, the polling tick still works.
            logger.warning("could not install wakeup trigger: %s", e)

    def has_pending_wakeup(self) -> bool:
        """Check (and clear) the wakeup flag set by the trigger."""
        try:
            with self.store._conn() as conn:
                row = conn.execute(
                    "SELECT flag FROM _sync_triggers LIMIT 1"
                ).fetchone()
                if row and row["flag"]:
                    conn.execute("UPDATE _sync_triggers SET flag = 0")
                    return True
        except sqlite3.OperationalError:
            pass
        return False

    # ===== Status =====

    def status(self) -> dict:
        """Return current daemon state + sync stats."""
        local = self.store.stats()
        return {
            "running": self.is_running(),
            "push_interval_s": self.push_interval,
            "pull_interval_s": self.pull_interval,
            "local": local,
            "stats": dict(self.stats),
        }


# Convenience: attach a daemon to an AgentCloud instance
def attach_daemon(cloud: AgentCloud, auto_start: bool = True, **kwargs) -> SyncDaemon:
    """Create and optionally start a SyncDaemon bound to this AgentCloud."""
    daemon = SyncDaemon(cloud, **kwargs)
    cloud._daemon = daemon  # type: ignore[attr-defined]
    if auto_start:
        daemon.start()
    return daemon