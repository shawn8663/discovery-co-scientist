"""Task model support for therapeutic discovery agents and actions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from co_scientist import ids
from co_scientist.models import Task


@pytest.mark.parametrize(
    ("agent", "action"),
    [
        ("assay", "GenerateAssays"),
        ("assay", "EvaluateAssay"),
        ("assay", "RankAssays"),
        ("candidate", "GenerateCandidates"),
        ("candidate", "EvaluateCandidate"),
        ("candidate", "RankCandidates"),
        ("candidate", "RegenerateCandidatesFromResults"),
        ("analysis", "AnalyzeExperimentalData"),
        ("result_interpreter", "InterpretResults"),
    ],
)
def test_robin_task_agents_and_actions_validate(agent: str, action: str) -> None:
    task = Task(
        id=ids.task_id(),
        session_id="ses_test",
        created_at=datetime.now(UTC),
        agent=agent,
        action=action,
        payload={},
        status="pending",
    )
    assert task.agent == agent
    assert task.action == action


def test_unknown_robin_action_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Task(
            id=ids.task_id(),
            session_id="ses_test",
            created_at=datetime.now(UTC),
            agent="candidate",
            action="InventUnplannedWorkflow",
            payload={},
            status="pending",
        )
