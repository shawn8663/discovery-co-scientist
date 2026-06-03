"""Shared helpers for Robin-style therapeutic discovery agents."""

from __future__ import annotations

import json
import re
from typing import Any


def final_text(response) -> str:
    parts: list[str] = []
    for block in response.raw.content or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "\n".join(parts).strip()


def parse_json_array(text: str) -> list[dict[str, Any]]:
    data = json.loads(_extract_json(text))
    if not isinstance(data, list):
        raise ValueError("expected JSON array")
    return [item for item in data if isinstance(item, dict)]


def parse_json_object(text: str) -> dict[str, Any]:
    data = json.loads(_extract_json(text))
    if not isinstance(data, dict):
        raise ValueError("expected JSON object")
    return data


def parse_candidate_blocks(text: str) -> list[dict[str, str]]:
    blocks = re.findall(
        r"<CANDIDATE START>(.*?)<CANDIDATE END>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    out: list[dict[str, str]] = []
    for block in blocks:
        record: dict[str, str] = {}
        for key in ("CANDIDATE", "HYPOTHESIS", "REASONING"):
            m = re.search(
                rf"^{key}:\s*(.*?)(?=^\w+:\s*|\Z)",
                block.strip(),
                flags=re.IGNORECASE | re.DOTALL | re.MULTILINE,
            )
            record[key.lower()] = m.group(1).strip() if m else ""
        if record.get("candidate") and record.get("hypothesis"):
            out.append(record)
    if not out:
        raise ValueError("no candidate blocks found")
    return out


def split_evaluation_sections(text: str, headings: tuple[str, ...]) -> dict[str, str]:
    sections: dict[str, str] = {heading: "" for heading in headings}
    for i, heading in enumerate(headings):
        next_headings = "|".join(re.escape(h) for h in headings[i + 1 :])
        if next_headings:
            pattern = rf"{re.escape(heading)}:\s*(.*?)(?={next_headings}:|\Z)"
        else:
            pattern = rf"{re.escape(heading)}:\s*(.*)\Z"
        m = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if m:
            sections[heading] = m.group(1).strip()
    return sections


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return stripped
    m = re.search(r"(\{.*\}|\[.*\])", text, flags=re.DOTALL)
    if not m:
        raise ValueError("no JSON payload found")
    return m.group(1)
