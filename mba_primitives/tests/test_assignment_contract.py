"""`assignment_contract` tests (AC #4)."""

from __future__ import annotations

from pathlib import Path

from mba_primitives import assignment_contract
from mba_primitives.constants import NONE_MARKER


def test_render_contract_text_fills_every_field() -> None:
    text = assignment_contract.render_contract_text(
        role="Engineer",
        bead="example-004.1",
        stage="Build",
        session_purpose="main",
        task="Implement primitives",
        read="bd show example-004.1; docs/mba/charter.md",
        produce="working.md and result.md",
        acceptance="every AC row passes",
        authority_and_limits="may edit source; may not commit",
        responsibility="Doer",
    )
    assert "# Engineer Assignment" in text
    assert "- **Bead:** example-004.1" in text
    assert "- **Stage:** Build" in text
    assert "- **Responsibility:** Doer" in text
    assert "- **Organizational role:** Engineer" in text
    assert "- **Session purpose:** main" in text
    assert "- **Task:** Implement primitives" in text
    assert "- **Read:** bd show example-004.1; docs/mba/charter.md" in text
    assert "- **Produce:** working.md and result.md" in text
    assert "- **Acceptance:** every AC row passes" in text
    assert "- **Authority and limits:** may edit source; may not commit" in text


def test_render_contract_text_requires_internal_ai_delegation_disclosure() -> None:
    text = assignment_contract.render_contract_text(
        role="Engineer",
        bead="example-012.1",
        stage="Build",
        session_purpose="main",
        task="Implement worker disclosure",
        read="docs/mba/charter.md",
        produce="working.md and result.md",
        acceptance="delegation is disclosed",
        authority_and_limits="may edit source; may not commit",
        responsibility="Doer",
    )
    assert "- **Worker-internal AI delegation:**" in text
    assert "Record either `none` or every worker-internal AI delegation" in text
    assert "Bead comment or its linked details file" in text
    assert "role, tool/session type, and purpose" in text
    assert "Do not include private transcript contents or inspect personal user sessions" in text


def test_render_contract_text_includes_safe_beads_write_rules() -> None:
    text = assignment_contract.render_contract_text(
        role="Engineer",
        bead="example-028.1",
        stage="Build",
        session_purpose="main",
        task="Implement safe write prompts",
        read="docs/mba/charter.md",
        produce="comment.md",
        acceptance="comments are role-attributed",
        authority_and_limits="may post comment only",
        responsibility="Doer",
    )
    assert 'bd --actor "Engineer" ...' in text
    assert "never rely on the OS/Git/default actor" in text
    assert "Do not use `bd update --claim`" in text
    assert 'bd --actor "Engineer" comments add example-028.1 -f <comment.md>' in text
    assert "`bd comments add -t -`" in text
    assert "`||`, `head`, `wc`, `ls -la`" in text


def test_render_contract_text_requires_real_worker_session_boundary() -> None:
    text = assignment_contract.render_contract_text(
        role="Reviewer",
        bead="example-029.1",
        stage="Verify",
        session_purpose="main",
        task="Audit a result",
        read="bd show example-029.1",
        produce="comment.md and _verdict.txt",
        acceptance="verdict has evidence",
        authority_and_limits="may post comment only",
        responsibility="Auditor",
    )
    assert "## Session boundary" in text
    assert "worker session launched or resumed" in text
    assert "Do not complete both Doer and Auditor work inside one transcript" in text
    assert "do not manufacture convergence" in text


def test_render_contract_text_marks_missing_fields() -> None:
    text = assignment_contract.render_contract_text(
        role="Engineer",
        bead="example-004.1",
        stage=None,
        session_purpose=None,
        task=None,
        read=None,
        produce=None,
        acceptance=None,
        authority_and_limits=None,
    )
    # Every missing field surfaces as the explicit marker.
    for field in ("Stage", "Session purpose", "Task", "Read", "Produce", "Acceptance", "Authority and limits"):
        assert f"- **{field}:** {NONE_MARKER}" in text, field
    # Provided fields still render normally.
    assert "- **Bead:** example-004.1" in text
    assert "- **Organizational role:** Engineer" in text


def test_render_contract_text_renders_lists_as_bullets() -> None:
    text = assignment_contract.render_contract_text(
        role="Engineer",
        bead="example-004.1",
        stage="Build",
        session_purpose="main",
        task=["step 1", "step 2", "step 3"],
        read=["file A", "file B"],
        produce=["working.md", "result.md"],
        acceptance=["AC-1", "AC-2"],
        authority_and_limits=["may edit", "may not commit"],
    )
    assert "- step 1" in text
    assert "- step 2" in text
    assert "- file A" in text
    assert "- AC-1" in text
    assert "- may edit" in text


def test_assignment_contract_writes_to_session_directory(tmp_path: Path) -> None:
    prompt_path = assignment_contract.assignment_contract(
        role="Engineer",
        bead="example-004.1",
        stage="Build",
        session_purpose="main",
        task="Implement primitives",
        read="docs/mba/charter.md",
        produce="working.md",
        acceptance="AC rows pass",
        authority_and_limits="may edit",
        session_name="engineer",
        base_dir=tmp_path,
    )
    assert prompt_path == tmp_path / ".mba-work" / "example-004.1" / "engineer" / "prompt.md"
    body = prompt_path.read_text(encoding="utf-8")
    assert "# Engineer Assignment" in body
    assert "- **Bead:** example-004.1" in body


def test_assignment_contract_default_session_name(tmp_path: Path) -> None:
    prompt_path = assignment_contract.assignment_contract(
        role="Engineer",
        bead="example-004.1",
        stage="Build",
        session_purpose="main",
        task="x",
        read="x",
        produce="x",
        acceptance="x",
        authority_and_limits="x",
        base_dir=tmp_path,
    )
    # Default derives from `<bead>-<role-slug>`.
    assert prompt_path.parent.name == "example-004.1-engineer"


def test_assignment_contract_existing_prompt_is_replaced(tmp_path: Path) -> None:
    session_dir = tmp_path / ".mba-work" / "example-004.1" / "engineer"
    session_dir.mkdir(parents=True)
    existing = session_dir / "prompt.md"
    existing.write_text("stale content\n", encoding="utf-8")

    prompt_path = assignment_contract.assignment_contract(
        role="Engineer",
        bead="example-004.1",
        stage="Build",
        session_purpose="main",
        task="fresh",
        read="x",
        produce="x",
        acceptance="x",
        authority_and_limits="x",
        base_dir=tmp_path,
        session_name="engineer",
    )
    body = prompt_path.read_text(encoding="utf-8")
    assert "stale content" not in body
    assert "fresh" in body


def test_render_contract_text_empty_list_renders_marker() -> None:
    text = assignment_contract.render_contract_text(
        role="Engineer",
        bead="example-004.1",
        stage="Build",
        session_purpose="main",
        task=[],
        read=[],
        produce=[],
        acceptance=[],
        authority_and_limits=[],
    )
    # All four list args were empty; each surfaces as the marker.
    for field in ("Task", "Read", "Produce", "Acceptance", "Authority and limits"):
        assert f"- **{field}:** {NONE_MARKER}" in text, field


def test_render_contract_text_missing_responsibility_renders_marker() -> None:
    """F2 (turn-2): a missing `responsibility` must surface as the
    explicit ``(None)`` marker instead of defaulting to the
    organisational role. A non-{Doer,Auditor} value (e.g. ``Engineer``)
    on the §10 Responsibility line would mislead the runtime that
    consumes the contract."""

    text = assignment_contract.render_contract_text(
        role="Engineer",
        bead="example-004.1",
        stage="Build",
        session_purpose="main",
        task="x",
        read="x",
        produce="x",
        acceptance="x",
        authority_and_limits="x",
        responsibility=None,
    )
    assert f"- **Responsibility:** {NONE_MARKER}" in text
    # Organisational role line still records the hat.
    assert "- **Organizational role:** Engineer" in text


def test_assignment_contract_missing_responsibility_writes_marker(tmp_path: Path) -> None:
    """F2 (turn-2): file-level write also surfaces the marker."""

    prompt_path = assignment_contract.assignment_contract(
        role="Engineer",
        bead="example-004.1",
        stage="Build",
        session_purpose="main",
        task="x",
        read="x",
        produce="x",
        acceptance="x",
        authority_and_limits="x",
        responsibility=None,
        base_dir=tmp_path,
        session_name="engineer",
    )
    body = prompt_path.read_text(encoding="utf-8")
    assert f"- **Responsibility:** {NONE_MARKER}" in body


def test_render_contract_text_explicit_responsibility_passes_through() -> None:
    text = assignment_contract.render_contract_text(
        role="Engineer",
        bead="example-004.1",
        stage="Build",
        session_purpose="main",
        task="x",
        read="x",
        produce="x",
        acceptance="x",
        authority_and_limits="x",
        responsibility="Doer",
    )
    assert "- **Responsibility:** Doer" in text
