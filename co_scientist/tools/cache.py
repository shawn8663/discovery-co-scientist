"""Small on-disk retrieval cache shared by tools.

The cache is session-scoped when a session id is available, which keeps local
research projects reproducible and avoids cross-project leakage. Standalone
tool calls fall back to a global namespace under the same data directory.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..config import Config


class RetrievalCache:
    def __init__(self, cfg: Config, session_id: str | None = None) -> None:
        self.cfg = cfg
        self.session_id = session_id or "_global"
        self.root = cfg.data_dir / "cache" / "retrieval" / self.session_id

    def read(self, tool_name: str, args: dict[str, Any]) -> Any | None:
        path = self._path(tool_name, args)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text()).get("payload")
        except json.JSONDecodeError:
            return None

    def write(self, tool_name: str, args: dict[str, Any], payload: Any) -> str:
        path = self._path(tool_name, args)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps({"tool": tool_name, "args": args, "payload": payload}, default=str))
        tmp.replace(path)
        return str(path)

    def _path(self, tool_name: str, args: dict[str, Any]) -> Path:
        key = _stable_key({"tool": tool_name, "args": args})
        safe_tool = "".join(c if c.isalnum() or c in "-_" else "_" for c in tool_name)
        return self.root / safe_tool / f"{key}.json"


def _stable_key(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()
