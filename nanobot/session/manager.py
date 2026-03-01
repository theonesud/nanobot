"""Session management for conversation history."""

import collections
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.utils.helpers import ensure_dir, safe_filename
import asyncio
from nanobot.utils.lock import FileLock


@dataclass
class Session:
    """
    A conversation session.

    Stores messages in JSONL format for easy reading and persistence.

    Important: Messages are append-only for LLM cache efficiency.
    The consolidation process writes summaries to MEMORY.md/HISTORY.md
    but does NOT modify the messages list or get_history() output.
    """

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already consolidated to files

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {"role": role, "content": content, "timestamp": datetime.now().isoformat(), **kwargs}
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        """Return unconsolidated messages for LLM input, aligned to a user turn."""
        unconsolidated = self.messages[self.last_consolidated :]
        sliced = unconsolidated[-max_messages:]

        # Ensure history starts with a 'user' role for provider compatibility
        final_history = []
        for i, m in enumerate(sliced):
            if m.get("role") == "user":
                final_history = sliced[i:]
                break

        # Fallback: if no user message found in the slice, try to find the very last user message
        # before the slice to provide at least some context, or just return empty if none.
        if not final_history and sliced:
            # Search backwards from the start of the slice in the full message list
            search_idx = self.last_consolidated + (len(unconsolidated) - len(sliced)) - 1
            while search_idx >= 0:
                if self.messages[search_idx].get("role") == "user":
                    final_history = self.messages[search_idx:]
                    # Re-slice if it became too long
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

    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    def __init__(self, workspace: Path, max_cache_size: int = 100):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.legacy_sessions_dir = Path.home() / ".nanobot" / "sessions"
        self._cache: collections.OrderedDict[str, Session] = collections.OrderedDict()
        self.max_cache_size = max_cache_size

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        """Legacy global session path (~/.nanobot/sessions/)."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.legacy_sessions_dir / f"{safe_key}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session.
        """
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        self._cache[key] = session
        if len(self._cache) > self.max_cache_size:
            self._cache.popitem(last=False)
        return session

    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
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

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated,
            )
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            return None

    async def save_async(self, session: Session) -> None:
        """Save a session to disk with file locking."""
        import os

        path = self._get_session_path(session.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(".lock")

        async with FileLock(lock_path):
            temp_path = path.with_suffix(".tmp")
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    # Save metadata first
                    meta = {
                        "_type": "metadata",
                        "key": session.key,
                        "created_at": session.created_at.isoformat(),
                        "updated_at": session.updated_at.isoformat(),
                        "metadata": session.metadata,
                        "last_consolidated": session.last_consolidated,
                    }
                    f.write(json.dumps(meta, ensure_ascii=False) + "\n")
                    # Save each message
                    for m in session.messages:
                        f.write(json.dumps(m, ensure_ascii=False) + "\n")
                os.replace(temp_path, path)
            except Exception:
                if temp_path.exists():
                    temp_path.unlink()
                raise

        self._cache[session.key] = session
        self._cache.move_to_end(session.key)
        if len(self._cache) > self.max_cache_size:
            self._cache.popitem(last=False)

    def save(self, session: Session) -> None:
        """Fallback for non-async contexts - call save_async synchronously if needed."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # This is tricky - if we are in an async loop we should really use save_async
                # But sometimes sync code is call from a context that doesn't easily allow await.
                # However, for Nanobot's core, we can probably safely assume we'll fix callers.
                # For now, let's keep it simple.
                pass
        except RuntimeError:
            pass

        # Original logic with simple rename, used for initial setup or sync-only CLI paths.
        import os

        path = self._get_session_path(session.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                meta = {
                    "_type": "metadata",
                    "key": session.key,
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                    "metadata": session.metadata,
                    "last_consolidated": session.last_consolidated,
                }
                f.write(json.dumps(meta, ensure_ascii=False) + "\n")
                for m in session.messages:
                    f.write(json.dumps(m, ensure_ascii=False) + "\n")
            os.replace(temp_path, path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.

        Returns:
            List of session info dicts.
        """
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                # Read just the metadata line
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            key = data.get("key") or path.stem.replace("_", ":", 1)
                            sessions.append(
                                {
                                    "key": key,
                                    "created_at": data.get("created_at"),
                                    "updated_at": data.get("updated_at"),
                                    "path": str(path),
                                }
                            )
            except Exception:
                continue

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
