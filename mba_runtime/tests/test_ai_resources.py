"""Tests for ``mba_runtime.ai_resources``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mba_runtime import ai_resources
from mba_runtime.constants import AI_RESOURCE_RECORD
from mba_runtime.ai_resources import AIResource, AIResourceError, AIResourceRecord


def test_default_record_has_resources_and_default_team() -> None:
    record = ai_resources.default_record()
    assert record.schema == 1
    assert {r.id for r in record.resources} == {"minimax", "claude"}
    minimax = record.resource_by_id("minimax")
    assert minimax.launch is not None
    assert minimax.launch.model == "minimax-coding-plan/MiniMax-M3"
    assert "default" in record.teams
    assert record.teams["default"].pattern == "b"


def test_parse_ai_resource_record_requires_object_payload() -> None:
    with pytest.raises(AIResourceError):
        ai_resources.parse_ai_resource_record("not-a-dict")


def test_parse_ai_resource_record_rejects_unknown_pattern(tmp_path: Path) -> None:
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "resources": [
                    {
                        "id": "minimax",
                        "label": "MiniMax-M3",
                        "capabilities": ["doer", "auditor"],
                    }
                ],
                "teams": {"weird": {"pattern": "z"}},
            }
        )
    )
    with pytest.raises(AIResourceError, match="pattern"):
        ai_resources.load_ai_resource_record(tmp_path, record_path=bad_path)


def test_parse_ai_resource_record_rejects_missing_ai_capability(
    tmp_path: Path,
) -> None:
    # The parser itself only validates structure; the capability
    # check lives in the pattern router (see
    # ``tests/test_pattern_router.py::test_router_refuses_missing_doer_capability``).
    # Here we just confirm the parser tolerates and surfaces capability
    # information without raising.
    payload_path = tmp_path / "ok.json"
    payload_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "resources": [
                    {
                        "id": "minimax",
                        "label": "MiniMax-M3",
                        "capabilities": ["doer"],
                    }
                ],
                "teams": {},
            }
        )
    )
    record = ai_resources.load_ai_resource_record(tmp_path, record_path=payload_path)
    assert record.resources[0].can_doer is True
    assert record.resources[0].can_auditor is False


def test_team_config_default_lookup() -> None:
    record = ai_resources.default_record()
    team = ai_resources.team_config(record)
    assert team.pattern == "b"
    assert team.doer.ai == "minimax"
    assert team.auditor.ai == "claude"


def test_team_config_missing_team_raises(tmp_path: Path) -> None:
    record = ai_resources.default_record()
    record_path = tmp_path / AI_RESOURCE_RECORD
    ai_resources.save_ai_resource_record(tmp_path, record, record_path=record_path)
    with pytest.raises(AIResourceError, match="absent"):
        ai_resources.team_config(record, team_name="missing")


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    record = ai_resources.default_record()
    record_path = tmp_path / AI_RESOURCE_RECORD
    ai_resources.save_ai_resource_record(tmp_path, record, record_path=record_path)
    loaded = ai_resources.load_ai_resource_record(tmp_path, record_path=record_path)
    assert loaded.teams["default"].pattern == "b"
    assert loaded.resources[0].label == "MiniMax-M3"
    assert loaded.resources[0].launch is not None
    assert loaded.resources[0].launch.model == "minimax-coding-plan/MiniMax-M3"


def test_load_ai_resource_record_refuses_when_absent(tmp_path: Path) -> None:
    with pytest.raises(AIResourceError, match="absent"):
        ai_resources.load_ai_resource_record(tmp_path)


def test_resource_preflight_missing_record_returns_questions(tmp_path: Path) -> None:
    state = ai_resources.resource_preflight(tmp_path)

    assert state.ok is False
    assert ".ai-resources.json" in str(state.path)
    assert state.questions
    assert any("available" in q.lower() for q in state.questions)


def test_setup_bead_guidance_lists_allowed_and_blocked_actions(tmp_path: Path) -> None:
    state = ai_resources.resource_preflight(tmp_path)

    guidance = ai_resources.setup_bead_guidance(state)

    assert guidance["title"] == "MBA setup"
    assert guidance["type"] == "task"
    assert guidance["status"] == "blocked"
    assert guidance["assignee"] == "Human"
    assert guidance["labels"] == ["mba", "setup", "human"]
    assert guidance["action"] == "create_or_update"
    assert guidance["allowed_now"] == [
        "create_or_update_setup_bead",
        "post_setup_questions",
    ]
    assert guidance["blocked_until_ready"] == [
        "create_or_drive_executable_beads",
        "launch_workers",
    ]
    comment = guidance["comment"]
    assert isinstance(comment, dict)
    assert comment["format"] == "markdown"
    assert comment["questions"] == list(state.questions)
    assert "## MBA setup" in comment["body"]
    assert "Status:" in comment["body"]


def test_resource_preflight_valid_default_team_is_ready(tmp_path: Path) -> None:
    ai_resources.save_ai_resource_record(tmp_path, ai_resources.default_record())

    state = ai_resources.resource_preflight(tmp_path)

    assert state.ok is True
    assert state.default_team_ready is True
    assert state.questions == ()
    assert state.resources == ("minimax", "claude")


def test_resource_preflight_rejects_ready_team_without_launch_config(
    tmp_path: Path,
) -> None:
    path = tmp_path / AI_RESOURCE_RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "resources": [
                    {
                        "id": "minimax-m3-max",
                        "label": "MiniMax-M3 Max via OpenCode",
                        "capabilities": ["doer", "auditor"],
                        "session_lifetime": "fresh_per_session",
                    }
                ],
                "teams": {
                    "default": {
                        "pattern": "a",
                        "doer": {
                            "ai": "minimax-m3-max",
                            "hat": "Researcher",
                            "session_count": 1,
                        },
                        "auditor": {
                            "ai": "minimax-m3-max",
                            "hat": "Quality Auditor",
                            "session_count": 1,
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    state = ai_resources.resource_preflight(tmp_path)

    assert state.ok is False
    assert "launch.tool and launch.model" in state.reason
    assert "nickname" in state.reason


def test_parse_rejects_opencode_launch_model_that_looks_like_resource_id(
    tmp_path: Path,
) -> None:
    path = tmp_path / AI_RESOURCE_RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "resources": [
                    {
                        "id": "minimax-m3-max",
                        "label": "MiniMax-M3 Max via OpenCode",
                        "capabilities": ["doer", "auditor"],
                        "launch": {
                            "tool": "opencode",
                            "model": "minimax-m3-max",
                            "variant": "max",
                        },
                    }
                ],
                "teams": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AIResourceError, match="provider/model"):
        ai_resources.load_ai_resource_record(tmp_path, record_path=path)


def test_resource_preflight_rejects_default_team_without_auditor_capability(
    tmp_path: Path,
) -> None:
    path = tmp_path / AI_RESOURCE_RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "resources": [
                    {
                        "id": "solo",
                        "label": "Solo",
                        "capabilities": ["doer"],
                    }
                ],
                "teams": {
                    "default": {
                        "pattern": "a",
                        "doer": {"ai": "solo", "hat": "Maker", "session_count": 1},
                        "auditor": {
                            "ai": "solo",
                            "hat": "Reviewer",
                            "session_count": 1,
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    state = ai_resources.resource_preflight(tmp_path)

    assert state.ok is False
    assert "auditor capability" in state.reason


def test_resource_by_id_raises_when_missing() -> None:
    record = ai_resources.default_record()
    with pytest.raises(AIResourceError):
        record.resource_by_id("gpt-9")


def test_session_count_at_least_one() -> None:
    from mba_runtime.ai_resources import ResponsibilityConfig

    with pytest.raises(AIResourceError):
        ResponsibilityConfig(ai="minimax", hat="Engineer", session_count=0)
