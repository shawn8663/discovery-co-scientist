"""Bridge to the google-deepmind/science-skills repo.

For every subdirectory under `<science_skills.path>/skills/` that contains a
SKILL.md, we expose a `ScienceSkillTool` named `<dirname>` to the agent.

The bridge:
1. Parses SKILL.md front-matter (YAML or simple key:value lines at the top)
   to extract `name`, `description`, optional `inputs:` schema and
   `entrypoint:` script path.
2. If no explicit entrypoint is given, looks for `scripts/run.py`, `scripts/main.py`,
   `scripts/cli.py`, or the only file in `scripts/` — in that order.
3. Invokes the script via subprocess, passing JSON args via stdin and reading
   JSON from stdout. Captures stderr separately. Times out per SKILL.md or 120 s.
4. Persists raw stdout/stderr under `artifacts/tool_runs/<skill>/<run_id>.json`
   for resume and debugging.

This is intentionally permissive: many skills in the upstream repo will not have
formal Inputs schemas, so we expose a generic `{args: object}` shape and the
agent's tool-call args are forwarded verbatim.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..ids import tool_run_id
from .base import ToolCtx, ToolResult

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_KV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.+)$")


@dataclass
class SkillMeta:
    name: str
    description: str
    entrypoint: Path | None
    timeout_seconds: int = 120
    inputs_schema: dict[str, Any] | None = None
    requires_keys: list[str] = field(default_factory=list)
    category: str = "analysis"
    required_files: list[str] = field(default_factory=list)
    network_access: bool = False
    write_scope: str = "run_workspace"
    expected_outputs: list[str] = field(default_factory=list)
    safety_level: str = "trusted_local"
    requires_approval: bool = False
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def is_risky(self) -> bool:
        return self.requires_approval or self.network_access or self.write_scope not in {
            "none",
            "run_workspace",
            "artifacts",
        }


def parse_skill_md(skill_dir: Path) -> SkillMeta | None:
    """Return SkillMeta if this directory looks like a skill, else None."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None
    body = skill_md.read_text(errors="ignore")

    # Try YAML-ish front matter first; fall back to first-paragraph description.
    front: dict[str, Any] = {}
    m = _FRONT_MATTER_RE.match(body)
    if m:
        try:
            import yaml  # type: ignore[import-not-found]

            front = yaml.safe_load(m.group(1)) or {}
        except Exception:
            # Best-effort plain key:value scan
            for line in m.group(1).splitlines():
                mm = _KV_RE.match(line.strip())
                if mm:
                    front[mm.group(1)] = mm.group(2).strip().strip("\"'")
        body_after = body[m.end():]
    else:
        body_after = body

    name = (front.get("name") or skill_dir.name).strip()
    desc = (front.get("description") or "").strip()
    if not desc:
        # Use first non-empty line of the body as description fallback
        for line in body_after.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                desc = line[:400]
                break
    desc = desc or f"science-skill: {name}"

    entry: Path | None = None
    if front.get("entrypoint"):
        candidate = (skill_dir / str(front["entrypoint"])).resolve()
        # Path-traversal guard: entrypoint must stay inside skill_dir.
        try:
            candidate.relative_to(skill_dir.resolve())
            entry = candidate if candidate.exists() else None
        except ValueError:
            entry = None
    if entry is None:
        scripts = skill_dir / "scripts"
        if scripts.is_dir():
            for cand in ("run.py", "main.py", "cli.py", "run.sh"):
                p = scripts / cand
                if p.exists():
                    entry = p
                    break
            if entry is None:
                files = sorted([p for p in scripts.iterdir() if p.is_file()])
                if len(files) == 1:
                    entry = files[0]

    timeout = int(front.get("timeout_seconds") or 120)
    inputs_schema = front.get("inputs_schema") or front.get("inputs")
    if not isinstance(inputs_schema, dict):
        inputs_schema = None
    requires_keys: list[str] = []
    if isinstance(front.get("requires"), list):
        requires_keys = [str(x) for x in front["requires"]]
    if isinstance(front.get("required_secrets"), list):
        requires_keys.extend(str(x) for x in front["required_secrets"])

    required_files = _string_list(front.get("required_files"))
    expected_outputs = _string_list(front.get("expected_outputs"))
    network_access = bool(front.get("network_access") or front.get("requires_network") or False)
    write_scope = str(front.get("write_scope") or "run_workspace")
    safety_level = str(front.get("safety_level") or "trusted_local")
    requires_approval = bool(front.get("requires_approval") or False)
    provenance = front.get("provenance") if isinstance(front.get("provenance"), dict) else {}

    return SkillMeta(
        name=name,
        description=desc,
        entrypoint=entry,
        timeout_seconds=timeout,
        inputs_schema=inputs_schema,
        requires_keys=sorted(set(requires_keys)),
        category=str(front.get("category") or "analysis"),
        required_files=required_files,
        network_access=network_access,
        write_scope=write_scope,
        expected_outputs=expected_outputs,
        safety_level=safety_level,
        requires_approval=requires_approval,
        provenance=provenance,
    )


def discover_skills(cfg: Config) -> list[SkillMeta]:
    base = Path(cfg.science_skills.path)
    if not base.is_absolute():
        from ..config import PROJECT_ROOT

        base = PROJECT_ROOT / base
    skills_root = base / "skills"
    if not skills_root.exists():
        return []
    out: list[SkillMeta] = []
    for sub in sorted(skills_root.iterdir()):
        if not sub.is_dir():
            continue
        meta = parse_skill_md(sub)
        if meta is not None:
            out.append(meta)
    return out


class ScienceSkillTool:
    """One Anthropic tool per discovered skill."""

    def __init__(self, cfg: Config, meta: SkillMeta) -> None:
        self._cfg = cfg
        self.meta = meta
        self.name = _sanitize_name(meta.name)
        meta_bits = [
            f"category={meta.category}",
            f"safety_level={meta.safety_level}",
            f"write_scope={meta.write_scope}",
        ]
        if meta.network_access:
            meta_bits.append("network_access=true")
        if meta.expected_outputs:
            meta_bits.append("expected_outputs=" + ",".join(meta.expected_outputs[:5]))
        self.description = (meta.description + "\n\nMetadata: " + "; ".join(meta_bits))[:1024]
        self.input_schema = meta.inputs_schema or {
            "type": "object",
            "properties": {
                "args": {
                    "type": "object",
                    "description": "Free-form arguments forwarded to the skill's script.",
                }
            },
            "required": [],
        }

    async def call(self, args: dict[str, Any], ctx: ToolCtx) -> ToolResult:
        t0 = time.monotonic()
        if self.meta.entrypoint is None:
            return ToolResult(
                is_error=True,
                error_message=f"skill {self.meta.name!r} has no entrypoint",
            )
        entry = self.meta.entrypoint
        run_id = ctx.run_id or tool_run_id()

        if _approval_required(self._cfg, self.meta, ctx, run_id):
            return ToolResult(
                is_error=True,
                error_message=(
                    f"skill {self.meta.name!r} requires approval before execution "
                    f"(network={self.meta.network_access}, write_scope={self.meta.write_scope})"
                ),
                content={"approval_required": _approval_manifest(self.meta, run_id)},
            )

        missing_secrets = _missing_required_secrets(self._cfg, self.meta.requires_keys)
        if missing_secrets:
            return ToolResult(
                is_error=True,
                error_message="missing required secrets: " + ", ".join(missing_secrets),
                content={"missing_required_secrets": missing_secrets},
            )

        env = _sanitized_env(self._cfg, self.meta.requires_keys or [])
        input_hash = _input_hash(args)
        cached = _read_skill_cache(self._cfg, self.meta.name, input_hash)
        if cached is not None:
            return ToolResult(
                content=cached["content"],
                duration_ms=int((time.monotonic() - t0) * 1000),
                result_bytes=len(json.dumps(cached["content"], default=str)),
                metadata={
                    "cached": True,
                    "resumed_from_run_id": cached.get("run_id"),
                    "input_hash": input_hash,
                },
            )
        # cwd: per-call tmp under data/tool_runs/<skill>/<run_id>
        cwd = self._cfg.data_dir / "tool_runs" / self.meta.name / run_id
        cwd.mkdir(parents=True, exist_ok=True)

        cmd: list[str]
        if entry.suffix == ".py":
            cmd = ["python", str(entry)]
        elif entry.suffix in (".sh", ""):
            cmd = ["bash", str(entry)]
        else:
            cmd = [str(entry)]

        payload_stdin = json.dumps(args.get("args", args)).encode("utf-8")
        started_at = time.time()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=payload_stdin),
                    timeout=self.meta.timeout_seconds,
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return ToolResult(
                    is_error=True,
                    error_message=f"timeout after {self.meta.timeout_seconds}s",
                )
        except FileNotFoundError as e:
            return ToolResult(
                is_error=True, error_message=f"could not exec {cmd[0]}: {e}"
            )

        rc = proc.returncode or 0
        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        manifest = {
            **_approval_manifest(self.meta, run_id),
            "cmd": cmd,
            "cwd": str(cwd),
            "secrets_available": sorted(k for k in self.meta.requires_keys if k in env),
            "started_at": started_at,
            "finished_at": time.time(),
            "returncode": rc,
            "stdout_bytes": len(stdout_text),
            "stderr_bytes": len(stderr_text),
        }
        (cwd / "provenance.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

        # Persist raw artifact
        if ctx.session_id is not None:
            from ..storage.artifacts import write_json

            await write_json(
                self._cfg,
                ctx.session_id,
                f"tool_runs/{self.meta.name}",
                run_id,
                {
                    "skill": self.meta.name,
                    "args": args,
                    "cmd": cmd,
                    "provenance": manifest,
                    "returncode": rc,
                    "stdout": stdout_text[:200_000],
                    "stderr": stderr_text[:50_000],
                },
            )

        # Try to parse stdout as JSON; if that fails, return a raw envelope.
        parsed: Any
        parse_error: str | None = None
        try:
            parsed = json.loads(stdout_text) if stdout_text.strip() else {}
        except json.JSONDecodeError as e:
            parsed = {"raw": stdout_text[:8000]}
            parse_error = str(e)

        if rc != 0:
            return ToolResult(
                is_error=True,
                error_message=f"skill {self.meta.name} exit {rc}: {stderr_text[:600]}",
                content=parsed,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

        out_content: dict[str, Any] = {"result": parsed}
        if parse_error:
            out_content["parse_error"] = parse_error
        out_content["provenance"] = {
            "run_id": run_id,
            "category": self.meta.category,
            "safety_level": self.meta.safety_level,
            "cwd": str(cwd),
        }
        if ctx.session_id is not None:
            from ..workspace import ScientistWorkspace

            if self.meta.category == "analysis":
                ScientistWorkspace(self._cfg, ctx.session_id).add_artifact(
                    kind="analysis",
                    path=cwd,
                    title=f"{self.meta.name} analysis",
                    provenance=manifest,
                    metadata={
                        "expected_outputs": self.meta.expected_outputs,
                        "stdout_bytes": len(stdout_text),
                        "stderr_bytes": len(stderr_text),
                        "returncode": rc,
                    },
                )
            elif self.meta.category == "drafting":
                ScientistWorkspace(self._cfg, ctx.session_id).add_artifact(
                    kind="draft",
                    path=cwd,
                    title=f"{self.meta.name} draft",
                    provenance=manifest,
                    metadata={
                        "expected_outputs": self.meta.expected_outputs,
                        "returncode": rc,
                    },
                )
            ScientistWorkspace(self._cfg, ctx.session_id).add_artifact(
                kind="tool_run",
                path=cwd,
                title=f"{self.meta.name} run",
                provenance=manifest,
                metadata={
                    "category": self.meta.category,
                    "expected_outputs": self.meta.expected_outputs,
                    "returncode": rc,
                },
            )
        _write_skill_cache(
            self._cfg,
            self.meta.name,
            input_hash,
            {"run_id": run_id, "content": out_content},
        )
        return ToolResult(
            content=out_content,
            duration_ms=int((time.monotonic() - t0) * 1000),
            result_bytes=len(stdout_text),
        )


# --------------------------------------------------------------------------- #
# helpers

_ALLOWED_ENV_KEYS = {
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "PYTHONPATH",
}


def _sanitized_env(cfg: Config, extra_required: list[str]) -> dict[str, str]:
    import os as _os

    env: dict[str, str] = {}
    allowed = _ALLOWED_ENV_KEYS | set(extra_required)
    for k, v in _os.environ.items():
        if k in allowed:
            env[k] = v
    # Also export declared secrets present on the cfg.secrets object. Undeclared
    # API keys are intentionally withheld from skill subprocesses.
    for sk in set(extra_required):
        val = getattr(cfg.secrets, sk, "")
        if val and sk not in env:
            env[sk] = val
    return env


def _missing_required_secrets(cfg: Config, required: list[str]) -> list[str]:
    import os as _os

    missing: list[str] = []
    for name in sorted(set(required)):
        if _os.environ.get(name) or getattr(cfg.secrets, name, ""):
            continue
        missing.append(name)
    return missing


def _input_hash(args: dict[str, Any]) -> str:
    payload = json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _skill_cache_path(cfg: Config, skill_name: str, input_hash: str) -> Path:
    return cfg.data_dir / "tool_runs" / skill_name / "cache" / f"{input_hash}.json"


def _read_skill_cache(cfg: Config, skill_name: str, input_hash: str) -> dict[str, Any] | None:
    path = _skill_cache_path(cfg, skill_name, input_hash)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _write_skill_cache(cfg: Config, skill_name: str, input_hash: str, payload: dict[str, Any]) -> None:
    path = _skill_cache_path(cfg, skill_name, input_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    tmp.replace(path)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _approval_required(cfg: Config, meta: SkillMeta, ctx: ToolCtx, run_id: str) -> bool:
    approved = ctx.extra.get("approved_science_skill_runs", [])
    if (
        ctx.extra.get("approve_all_science_skills")
        or meta.name in approved
        or run_id in approved
    ):
        return False
    if meta.requires_approval:
        return True
    if cfg.science_skills.require_approval_for_risky_tools and meta.is_risky:
        return True
    if cfg.science_skills.execution_policy == "approval_required":
        return not cfg.science_skills.require_approval_for_risky_tools
    return False


def _approval_manifest(meta: SkillMeta, run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "skill": meta.name,
        "category": meta.category,
        "required_files": meta.required_files,
        "required_secrets": meta.requires_keys,
        "network_access": meta.network_access,
        "write_scope": meta.write_scope,
        "expected_outputs": meta.expected_outputs,
        "safety_level": meta.safety_level,
        "requires_approval": meta.requires_approval,
        "provenance": meta.provenance,
    }


_BAD_NAME_RE = re.compile(r"[^a-z0-9_]+")


def _sanitize_name(name: str) -> str:
    """Anthropic tool names must match ^[a-zA-Z0-9_-]{1,64}$."""
    n = _BAD_NAME_RE.sub("_", name.lower()).strip("_")
    return (n or "skill")[:64]
