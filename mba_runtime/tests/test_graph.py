"""Tests for ``mba_runtime.graph``."""

from __future__ import annotations

from pathlib import Path

import pytest

from mba_runtime import graph


def test_verify_graph_with_clean_stub(fake_bd_dir: Path, monkeypatch) -> None:
    monkeypatch.setenv("BD_DEP_LISTING", "")
    monkeypatch.setenv("BD_DEP_CYCLES", "")
    state = graph.verify_graph(cwd=fake_bd_dir)
    assert state.cycles_present is False
    assert state.list_returncode == 0
    assert state.cycles_returncode == 0


def test_assert_graph_clean_raises_on_cycles(
    fake_bd_dir: Path, monkeypatch
) -> None:
    monkeypatch.setenv("BD_DEP_CYCLES", "cycle: a -> b -> a\n")
    state = graph.verify_graph(cwd=fake_bd_dir)
    with pytest.raises(graph.GraphVerificationError, match="cycle"):
        graph.assert_graph_clean(state)


def test_assert_wire_clean_uses_both_commands(
    fake_bd_dir: Path, monkeypatch
) -> None:
    monkeypatch.setenv("BD_DEP_LISTING", "")
    monkeypatch.setenv("BD_DEP_CYCLES", "")
    state = graph.assert_wire_clean(cwd=fake_bd_dir)
    assert state.list_returncode == 0
    assert state.cycles_returncode == 0
