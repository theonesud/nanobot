import asyncio
import collections
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.utils.files import atomic_write
from nanobot.utils.helpers import ensure_dir, safe_filename
from nanobot.utils.lock import FileLock


@dataclass
class Session:
    key: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        msg = {"role": role, "content": content, "timestamp": datetime.now().isoformat(), **kwargs}
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        unconsolidated = self.messages[self.last_consolidated :]
        sliced = unconsolidated[-max_messages:]
        final_history = []
        for i, m in enumerate(sliced):
            if m.get("role") == "user":
                final_history = sliced[i:]
                break
        if not final_history and sliced:
            search_idx = self.last_consolidated + (len(unconsolidated) - len(sliced)) - 1
            while search_idx >= 0:
                if self.messages[search_idx].get("role") == "user":
                    final_history = self.messages[search_idx:]
                    final_history = final_history[-max_messages:]
                    break
                search_idx -= 1
        out: list[dict[str, Any]] = []
        for m in final_history or sliced:
            entry: dict[str, Any] = {"role": m["role"], "content": m.get("content", "")}
            for k in ("tool_calls", "tool_call_id", "name"):
                if k in m:
                    entry[k] = m[k]
            out.append(entry)
        return out


class SessionManager:
    def __init__(self, workspace: Path, max_cache_size: int = 100):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.legacy_sessions_dir = Path.home() / ".nanobot" / "sessions"
        self._cache: collections.OrderedDict[str, Session] = collections.OrderedDict()
        self.max_cache_size = max_cache_size

    def _get_session_path(self, key: str) -> Path:
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        safe_key = safe_filename(key.replace(":", "_"))
        return self.legacy_sessions_dir / f"{safe_key}.jsonl"

    def get_or_create(self, key: str) -> Session:
        if key in self._cache:
            logger.debug("🗂 Session cache hit: {}", key)
            self._cache.move_to_end(key)
            return self._cache[key]
        logger.debug("🗂 Session cache miss: {}", key)

        session = self._load(key)
        if session is None:
            session = Session(key=key)
        self._cache[key] = session
        if len(self._cache) > self.max_cache_size:
            self._cache.popitem(last=False)
        return session

    def _load(self, key: str) -> Session | None:
        path = self._get_session_path(key)
        if not path.exists():
            legacy_path = self._get_legacy_session_path(key)
            if legacy_path.exists():
                try:
                    shutil.move(str(legacy_path), str(path))
                    logger.info("Migrated session {} from legacy path", key)
                except Exception:
                    logger.exception("Failed to migrate session {}", key)
        if not path.exists():
            return None
        try:
            messages = []
            metadata = {}
            created_at = None
            last_consolidated = 0
            updated_at = None
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = (
                            datetime.fromisoformat(data["created_at"])
                            if data.get("created_at")
                            else None
                        )
                        updated_at = (
                            datetime.fromisoformat(data["updated_at"])
                            if data.get("updated_at")
                            else None
                        )
                        last_consolidated = data.get("last_consolidated", 0)
                    else:
                        messages.append(data)
            session = Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated,
            )
            logger.info(
                "💾 Loaded session {} ({} messages, last_consolidated: {})",
                key,
                len(messages),
                last_consolidated,
            )
            return session
        except Exception as e:
            logger.warning("❌ Failed to load session {}: {}", key, e)
            return None

    async def save_async(self, session: Session) -> None:
        path = self._get_session_path(session.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(".lock")
        async with FileLock(lock_path):
            try:
                meta = {
                    "_type": "metadata",
                    "key": session.key,
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                    "metadata": session.metadata,
                    "last_consolidated": session.last_consolidated,
                }
                content = json.dumps(meta, ensure_ascii=False) + "\n"
                for m in session.messages:
                    content += json.dumps(m, ensure_ascii=False) + "\n"
                atomic_write(path, content)
                logger.info("💾 Saved session {} ({} messages)", session.key, len(session.messages))
            except Exception:
                raise
        self._cache[session.key] = session
        self._cache.move_to_end(session.key)
        if len(self._cache) > self.max_cache_size:
            evicted, _ = self._cache.popitem(last=False)
            logger.debug("🗂 Evicted session {} from cache", evicted)

    def save(self, session: Session) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                pass
        except RuntimeError:
            pass
        path = self._get_session_path(session.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "_type": "metadata",
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "last_consolidated": session.last_consolidated,
        }
        content = json.dumps(meta, ensure_ascii=False) + "\n"
        for m in session.messages:
            content += json.dumps(m, ensure_ascii=False) + "\n"
        atomic_write(path, content)
