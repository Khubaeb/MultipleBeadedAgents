"""Opt-in real CLI contracts. Never opens a user's board or global config.

MBA_TEST_BD_104 and MBA_TEST_BD_130 must name exact binaries. Missing
configuration skips; a configured missing/wrong binary or failed command fails.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from mba_primitives.bead_read import read_back, assert_field_matches
from mba_primitives.bead_write import safe_write_field
from mba_runtime import graph


TEXT = "## Résultat ✓\n\n- 日本語 → café\n- Literal `code`, $HOME and 'quotes'\n"
ACTOR = "Codex / Python compatibility engineer"


def binary(version):
    value = os.environ.get("MBA_TEST_BD_" + version.replace(".", ""))
    if not value:
        pytest.skip(f"configure exact Beads {version} binary for real contract tests")
    assert Path(value).is_file(), value
    return value


def run(bd, root, *args, ok=True):
    proc = subprocess.run([bd, "--sandbox", *args], cwd=root, capture_output=True,
                          encoding="utf-8", timeout=90)
    if ok:
        assert proc.returncode == 0, (args, proc.stdout, proc.stderr)
    return proc


def rows(bd, root, *args):
    return json.loads(run(bd, root, *args, "--json").stdout)


def init(bd, root, version):
    assert not any((p / ".beads").exists() for p in (root, *root.parents))
    assert version in run(bd, root, "version").stdout
    run(bd, root, "init", "--non-interactive", "--prefix", "compat", "--skip-hooks")
    config = root / ".beads/config.yaml"
    with config.open("a") as stream:
        stream.write("\nsync.remote: \"\"\nexport.auto: false\nbackup.enabled: false\ndolt.auto-push: false\n")
    assert "No remotes" in run(bd, root, "dolt", "remote", "list").stdout
    assert not subprocess.check_output(["git", "remote"], cwd=root, text=True).strip()
    metadata = json.loads((root / ".beads/metadata.json").read_text())
    assert metadata["dolt_mode"] == "embedded"


def create(bd, root, title):
    item = rows(bd, root, "--actor", ACTOR, "create", "--title", title,
                "--assignee", ACTOR, "--type", "task")
    assert item["created_by"] == ACTOR
    assert item["assignee"] == ACTOR
    return item["id"]


@pytest.mark.parametrize("version", ["1.0.4", "1.3.0"])
def test_real_cli_contract(version, authorized_workspace):
    bd = binary(version)
    root = authorized_workspace
    init(bd, root, version)
    prerequisite = create(bd, root, "prerequisite")
    dependent = create(bd, root, "dependent")
    for field in ("description", "design", "notes", "acceptance"):
        proc = safe_write_field(dependent, field, TEXT, cwd=root, bd_binary=bd, actor=ACTOR)
        assert proc.returncode == 0, proc.stderr
        assert_field_matches(dependent, field, TEXT, cwd=root, bd_binary=bd)
    proc = safe_write_field(dependent, "labels", ["compatibility", "unicode"], cwd=root, bd_binary=bd, actor=ACTOR)
    assert proc.returncode == 0, proc.stderr
    assert set(read_back(dependent, cwd=root, bd_binary=bd)["labels"]) == {"compatibility", "unicode"}
    comment = root / "comment.md"
    comment.write_text(TEXT, encoding="utf-8")
    run(bd, root, "--actor", ACTOR, "comments", "add", dependent, "-f", str(comment))
    comments = rows(bd, root, "comments", dependent)
    assert any(c["text"] == TEXT and c["author"] == ACTOR for c in comments)
    run(bd, root, "--actor", ACTOR, "dep", "add", dependent, prerequisite, "--type", "blocks")
    assert dependent not in {x["id"] for x in rows(bd, root, "ready")}
    assert dependent in {x["id"] for x in rows(bd, root, "blocked")}
    graph.assert_wire_clean(cwd=root, bd_binary=bd, for_issue_id=dependent)
    cycle = run(bd, root, "--actor", ACTOR, "dep", "add", prerequisite, dependent, ok=False)
    assert cycle.returncode != 0 and "cycle" in (cycle.stdout + cycle.stderr).lower()
    run(bd, root, "--actor", ACTOR, "close", prerequisite, "--reason", "fixture completed")
    assert dependent in {x["id"] for x in rows(bd, root, "ready")}
    run(bd, root, "--actor", ACTOR, "update", dependent, "--status", "in_progress", "--assignee", ACTOR)
    assert read_back(dependent, cwd=root, bd_binary=bd)["status"] == "in_progress"
    run(bd, root, "--actor", ACTOR, "update", dependent, "--status", "blocked", "--add-label", "human", "--assignee", "Human")
    item = read_back(dependent, cwd=root, bd_binary=bd)
    assert item["status"] == "blocked" and item["assignee"] == "Human" and "human" in item["labels"]
    run(bd, root, "--actor", ACTOR, "update", dependent, "--status", "open", "--assignee", ACTOR)
    run(bd, root, "--actor", ACTOR, "close", dependent, "--reason", "fixture completed")
    assert read_back(dependent, cwd=root, bd_binary=bd)["status"] == "closed"


def export(bd, root):
    output = root / "snapshot.jsonl"
    run(bd, root, "export", "-o", str(output))
    return sorted((json.loads(line) for line in output.read_text().splitlines() if line), key=lambda x: x["id"])


def normalize_export(items):
    """Only allow documented migration timestamp/auxiliary identity changes."""
    result = []
    for item in items:
        item = dict(item)
        item.pop("updated_at", None)
        comments = []
        for comment in item.get("comments", []):
            comment = dict(comment)
            comment.pop("id", None)
            comments.append(comment)
        if "comments" in item:
            item["comments"] = sorted(comments, key=lambda x: json.dumps(x, sort_keys=True))
        result.append(item)
    return result


def test_native_copy_migration_and_complete_rollback(authorized_workspace):
    old, new = binary("1.0.4"), binary("1.3.0")
    root = authorized_workspace
    seed, migrated, restored = (root / name for name in ("seed", "migrated", "restored"))
    seed.mkdir()
    init(old, seed, "1.0.4")
    first = create(old, seed, "native prerequisite")
    second = create(old, seed, "native dependent")
    run(old, seed, "--actor", ACTOR, "dep", "add", second, first, "--type", "blocks")
    run(old, seed, "--actor", ACTOR, "update", second, "--add-label", "compatibility", "--notes", TEXT)
    comment = seed / "comment.md"
    comment.write_text(TEXT, encoding="utf-8")
    for _ in range(2):
        run(old, seed, "--actor", ACTOR, "comments", "add", second, "-f", str(comment))
    before = export(old, seed)
    tables_before = native_tables(seed)
    # Copy the complete old native state; JSONL is evidence, never migration input.
    for target in (migrated, restored):
        target.mkdir()
        shutil.copytree(seed / ".beads", target / ".beads")
        subprocess.run(["git", "init", "--quiet"], cwd=target, check=True)
    migration_start = datetime.now(timezone.utc)
    run(new, migrated, "migrate", "--yes")
    migration_end = datetime.now(timezone.utc)
    after = export(new, migrated)
    tables_after = native_tables(migrated)
    tables_restored = native_tables(restored)
    canonical = lambda rows: sorted(json.dumps(row, sort_keys=True) for row in rows)
    assert {k: canonical(v) for k, v in tables_restored.items()} == {k: canonical(v) for k, v in tables_before.items()}
    # Keep inspectable synthetic evidence only; no production board content.
    evidence = Path(os.environ.get("MBA_TEST_EVIDENCE", str(root)))
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "migration-tables.json").write_text(json.dumps({"before": tables_before, "after": tables_after}, indent=2, sort_keys=True))
    for table in ("comments", "events", "labels", "dependencies"):
        def semantic(rows):
            return canonical([{k: v for k, v in row.items() if k != "id" or table not in {"comments", "events", "dependencies"}} for row in rows])
        if table == "dependencies":
            for row in tables_after[table]:
                row["depends_on_id"] = row.pop("depends_on_issue_id")
        assert semantic(tables_before[table]) == semantic(tables_after[table]), table
    # Every pre-existing table is accounted for, not just export-visible rows.
    special = {"comments", "events", "labels", "dependencies", "issues",
               "ready_issues", "blocked_issues", "local_metadata", "schema_migrations"}
    for table, original in tables_before.items():
        assert table in tables_after
        if table not in special:
            assert canonical(original) == canonical(tables_after[table]), table
    for table in ("issues", "ready_issues", "blocked_issues"):
        for original in tables_before[table]:
            current = next(row for row in tables_after[table] if row["id"] == original["id"])
            assert set(current) - set(original) == {"is_blocked", "row_lock"}
            assert {k: v for k, v in original.items() if k != "updated_at"} == {
                k: current[k] for k in original if k != "updated_at"
            }
            if current["updated_at"] != original["updated_at"]:
                stamp = datetime.fromisoformat(current["updated_at"]).replace(tzinfo=timezone.utc)
                assert migration_start - timedelta(seconds=1) <= stamp <= migration_end
    assert len(tables_before["schema_migrations"]) == 32
    assert len(tables_after["schema_migrations"]) == 66
    assert tables_after["local_metadata"] == [{"key": "bd_version", "value": "1.3.0"}]
    assert normalize_export(after) == normalize_export(before)
    assert export(old, restored) == before  # includes original IDs and timestamps
    assert second not in {x["id"] for x in rows(new, migrated, "ready")}
    run(new, migrated, "--actor", ACTOR, "close", first, "--reason", "migration verified")
    assert second in {x["id"] for x in rows(new, migrated, "ready")}
    graph.assert_wire_clean(cwd=migrated, bd_binary=new, for_issue_id=second)
    # Restored old store remains untouched after writes to the migrated copy.
    assert export(old, restored) == before


def native_tables(root):
    dolt = os.environ.get("MBA_TEST_DOLT")
    if not dolt:
        pytest.skip("configure MBA_TEST_DOLT for auxiliary native-table verification")
    stores = [p for p in (root / ".beads").glob("**/.dolt") if ".dolt" not in p.parent.parts]
    assert len(stores) == 1, stores
    def query(sql):
        proc = subprocess.run([dolt, "sql", "-r", "json", "-q", sql],
                              cwd=stores[0].parent, capture_output=True, encoding="utf-8", timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout).get("rows", [])
    names = [next(iter(x.values())) for x in query("SHOW TABLES")]
    return {name: query(f"SELECT * FROM `{name}`") for name in names}


def test_public_011_upgrade_preserves_app_override(authorized_workspace, monkeypatch):
    import sys
    baseline = os.environ.get("MBA_TEST_PUBLIC_BASELINE")
    if not baseline:
        pytest.skip("configure read-only public v0.1.1 source for upgrade proof")
    old = binary("1.0.4")
    new = binary("1.3.0")
    root = authorized_workspace
    init(old, root, "1.0.4")
    env = dict(os.environ, PYTHONPATH=baseline, PYTHONDONTWRITEBYTECODE="1")
    env["PATH"] = str(Path(old).parent) + os.pathsep + env["PATH"]
    proc = subprocess.run([sys.executable, "-B", "-m", "mba_foundation", "init", "--root", str(root)],
                          cwd=root, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    before = json.loads((root / ".mba/manifest.json").read_text())
    assert before["mba_version"] == "0.1.1"
    override = "# Team Override\nUse Codex and Claude apps; OpenCode provider paths are retired.\n\n"
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = root / name
        path.write_text(override + path.read_text())
    private = root / ".mba-work/.ai-resources.json"
    private.write_text('{"fixture": "private app configuration sentinel"}\n')
    private_before = private.read_bytes()
    run(new, root, "migrate", "--yes")
    env["PATH"] = str(Path(new).parent) + os.pathsep + os.environ["PATH"]
    # Installed candidate package, outside its source tree.
    env.pop("PYTHONPATH", None)
    for action in ("upgrade", "status"):
        proc = subprocess.run([sys.executable, "-B", "-m", "mba_foundation", action, "--root", str(root)],
                              cwd=root, env=env, capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        if action == "status":
            status = json.loads(proc.stdout)
            assert not status["has_drift"] and not status["has_conflicts"]
    for name in ("AGENTS.md", "CLAUDE.md"):
        assert (root / name).read_text().startswith(override)
        assert "1.3.0" in (root / name).read_text()
    assert private.read_bytes() == private_before


@pytest.mark.parametrize("version", ["1.0.4", "1.3.0"])
def test_real_default_cleanup_handoff(version, authorized_workspace, monkeypatch):
    """No launched/killed process: default refusal sink persists on the chosen board."""
    from mba_runtime.external_dispatch import (
        AuthorityContext, ExternalProcessSessionRunner, MBAOwnedWorker,
    )
    bd = binary(version)
    root = authorized_workspace / "board"
    root.mkdir()
    other = authorized_workspace / "unrelated-cwd"
    other.mkdir()
    init(bd, root, version)
    bead = create(bd, root, "cleanup handoff fixture")
    monkeypatch.chdir(other)
    def forbidden(*args):
        pytest.fail("protected session must not reach any process/launch operation")
    runner = ExternalProcessSessionRunner(
        dispatch_argv=("not-launched",),
        authority=AuthorityContext(project_cwd=root, decision_fn=forbidden),
        bd_binary=bd, cleanup_is_alive=forbidden, cleanup_kill=forbidden,
    )
    outcome = runner._cleanup_terminal_state(
        MBAOwnedWorker(bead_id=bead, session_name="doer", owner="user", pid=1234)
    )
    assert outcome.blocked_for_human
    item = read_back(bead, cwd=root, bd_binary=bd)
    assert (item["status"], item["assignee"]) == ("blocked", "Human")
    assert "human" in item["labels"]
    comments = rows(bd, root, "comments", bead)
    assert len(comments) == 1 and comments[0]["author"] == "Orchestrator"
    path = root / ".mba-work" / bead / "orchestrator/cleanup-handoff.md"
    assert comments[0]["text"] == path.read_text(encoding="utf-8")
    assert len(comments[0]["text"].splitlines()) == 5
    assert "not MBA-owned" in comments[0]["text"]
    assert not (other / ".mba-work").exists()
