"""Approved runbook parsing for semi-autonomous run execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # pragma: no cover - Python 3.11+ has tomllib.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class RunbookStep:
    name: str
    prompt: str
    workflow: str = "general_hypothesis"
    disease: str | None = None
    preferences_file: Path | None = None
    project_files: list[Path] = field(default_factory=list)
    project_dirs: list[Path] = field(default_factory=list)
    science_skills_path: Path | None = None
    n: int = 3
    wall_clock: int | None = None
    budget_usd: float | None = None
    concurrency: int | None = None


@dataclass(frozen=True)
class Runbook:
    path: Path
    title: str
    approved: bool
    stop_on_failed_step: bool
    summary_file: Path | None
    steps: list[RunbookStep]


def load_runbook(path: Path) -> Runbook:
    """Load and validate an approved TOML runbook."""
    path = path.expanduser().resolve()
    with path.open("rb") as f:
        raw = tomllib.load(f)

    if raw.get("approved") is not True:
        raise ValueError("Runbook must include approved = true before execution.")

    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("Runbook must define at least one [[steps]] entry.")

    base_dir = path.parent
    defaults = _table(raw.get("defaults"), "defaults")
    summary = _table(raw.get("summary"), "summary", allow_missing=True)
    summary_file = _optional_path(summary.get("output"), base_dir)

    steps = [
        _parse_step(item, defaults=defaults, base_dir=base_dir, index=i)
        for i, item in enumerate(raw_steps, start=1)
    ]
    return Runbook(
        path=path,
        title=str(raw.get("title") or path.stem),
        approved=True,
        stop_on_failed_step=bool(raw.get("stop_on_failed_step", True)),
        summary_file=summary_file,
        steps=steps,
    )


def _parse_step(
    item: Any,
    *,
    defaults: dict[str, Any],
    base_dir: Path,
    index: int,
) -> RunbookStep:
    step = _table(item, f"steps[{index}]")
    merged = {**defaults, **step}
    name = str(merged.get("name") or f"step-{index}")

    prompt = str(merged.get("prompt") or "").strip()
    prompt_file = _optional_path(merged.get("prompt_file"), base_dir)
    if prompt_file is not None:
        prompt = prompt_file.read_text().strip()
    if not prompt:
        raise ValueError(f"Runbook step {name!r} must define prompt or prompt_file.")

    return RunbookStep(
        name=name,
        prompt=prompt,
        workflow=str(merged.get("workflow") or "general_hypothesis"),
        disease=_optional_str(merged.get("disease")),
        preferences_file=_optional_path(merged.get("preferences_file"), base_dir),
        project_files=_path_list(merged.get("project_file"), base_dir),
        project_dirs=_path_list(merged.get("project_dir"), base_dir),
        science_skills_path=_optional_path(merged.get("science_skills_path"), base_dir),
        n=int(merged.get("n", 3)),
        wall_clock=_optional_int(merged.get("wall_clock")),
        budget_usd=_optional_float(merged.get("budget_usd")),
        concurrency=_optional_int(merged.get("concurrency")),
    )


def _table(value: Any, name: str, *, allow_missing: bool = False) -> dict[str, Any]:
    if value is None and allow_missing:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Runbook {name} must be a TOML table.")
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_path(value: Any, base_dir: Path) -> Path | None:
    text = _optional_str(value)
    if text is None:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _path_list(value: Any, base_dir: Path) -> list[Path]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raise ValueError("Runbook path lists must be strings or arrays of strings.")
    out: list[Path] = []
    for item in raw_items:
        path = _optional_path(item, base_dir)
        if path is not None:
            out.append(path)
    return out
