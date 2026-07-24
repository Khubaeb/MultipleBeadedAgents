"""Tiny CLI dispatch for every Primitives operation.

Subcommands:

* ``safe-write``            — invoke ``safe_write_field``.
* ``read-back``             — invoke ``read_back``.
* ``assert-field-matches``  — invoke ``assert_field_matches``.
* ``assignment-contract``   — invoke ``assignment_contract``.
* ``ensure-layout``         — invoke ``ensure_layout``.

The CLI is intentionally script-friendly (``--json`` machine output) so
tests and ad-hoc audits can reproduce the same operations from a shell.
"""

from __future__ import annotations

import argparse
import json as _json
import sys
from pathlib import Path
from typing import Any

from mba_version import __version__

from . import (
    assignment_contract as ac_mod,
    bead_read as br_mod,
    bead_write as bw_mod,
    records_layout as rl_mod,
)


def _emit(obj: dict[str, Any]) -> None:
    print(_json.dumps(obj, indent=2, sort_keys=True))


def cmd_safe_write(args: argparse.Namespace) -> int:
    proc = bw_mod.safe_write_field(
        args.bead_id,
        args.field,
        # For label fields the content arrives as repeated ``--label``
        # argv; for text fields we read the file at ``--content-file``.
        _resolve_content(args),
        cwd=Path(args.cwd).resolve() if args.cwd else None,
        bd_binary=args.bd,
    )
    payload = {
        "bead_id": args.bead_id,
        "field": args.field,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    _emit(payload)
    return 0 if proc.returncode == 0 else 2


def _resolve_content(args: argparse.Namespace) -> Any:
    if args.field == "labels":
        labels = list(args.label or [])
        if args.content_file:
            labels.extend(
                line.strip()
                for line in Path(args.content_file).read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        return labels
    if args.content_file:
        return Path(args.content_file).read_text(encoding="utf-8")
    if args.content is not None:
        return args.content
    raise SystemExit("safe-write requires --content-file or --content")


def cmd_read_back(args: argparse.Namespace) -> int:
    record = br_mod.read_back(
        args.bead_id,
        cwd=Path(args.cwd).resolve() if args.cwd else None,
        bd_binary=args.bd,
    )
    _emit({"bead_id": args.bead_id, "record": record})
    return 0


def cmd_assert_field_matches(args: argparse.Namespace) -> int:
    content: Any
    if args.field == "labels":
        content = list(args.label or [])
    elif args.content_file:
        content = Path(args.content_file).read_text(encoding="utf-8")
    else:
        content = args.content
    try:
        record = br_mod.assert_field_matches(
            args.bead_id,
            args.field,
            content,
            cwd=Path(args.cwd).resolve() if args.cwd else None,
            bd_binary=args.bd,
        )
    except br_mod.FieldMismatchError as exc:
        _emit(
            {
                "bead_id": args.bead_id,
                "field": args.field,
                "matched": False,
                "reason": str(exc),
            }
        )
        return 3
    _emit({"bead_id": args.bead_id, "field": args.field, "matched": True, "record": record})
    return 0


def cmd_assignment_contract(args: argparse.Namespace) -> int:
    prompt_path = ac_mod.assignment_contract(
        role=args.role,
        bead=args.bead,
        stage=args.stage,
        session_purpose=args.session_purpose,
        task=args.task,
        read=args.read,
        produce=args.produce,
        acceptance=args.acceptance,
        authority_and_limits=args.authority_and_limits,
        session_name=args.session_name,
        base_dir=Path(args.cwd).resolve() if args.cwd else None,
        responsibility=args.responsibility,
        heading=args.heading,
    )
    _emit({"prompt_path": str(prompt_path)})
    return 0


def cmd_ensure_layout(args: argparse.Namespace) -> int:
    layout = rl_mod.ensure_layout(
        args.bead_id,
        list(args.session or []),
        base_dir=Path(args.cwd).resolve() if args.cwd else None,
    )
    _emit(
        {
            "bead_id": args.bead_id,
            "bead_dir": str(layout["bead_dir"]),
            "orchestrator": str(layout["orchestrator"]),
            "final": str(layout["final"]),
            "sessions": {k: str(v) for k, v in layout["sessions"].items()},
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mba-primitives")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("safe-write", help="Multiline-safe Bead-field writer.")
    p.add_argument("--bead-id", required=True)
    p.add_argument(
        "--field",
        required=True,
        choices=["description", "notes", "design", "acceptance", "labels"],
    )
    p.add_argument("--content")
    p.add_argument("--content-file")
    p.add_argument("--label", action="append", default=[])
    p.add_argument("--cwd", default=".")
    p.add_argument("--bd", default="bd")
    p.set_defaults(func=cmd_safe_write)

    p = sub.add_parser("read-back", help="Read a Bead via `bd show --json`.")
    p.add_argument("--bead-id", required=True)
    p.add_argument("--cwd", default=".")
    p.add_argument("--bd", default="bd")
    p.set_defaults(func=cmd_read_back)

    p = sub.add_parser("assert-field-matches", help="Byte-for-byte field check.")
    p.add_argument("--bead-id", required=True)
    p.add_argument(
        "--field",
        required=True,
        choices=["description", "notes", "design", "acceptance", "labels"],
    )
    p.add_argument("--content")
    p.add_argument("--content-file")
    p.add_argument("--label", action="append", default=[])
    p.add_argument("--cwd", default=".")
    p.add_argument("--bd", default="bd")
    p.set_defaults(func=cmd_assert_field_matches)

    p = sub.add_parser("assignment-contract", help="Write the §10 prompt.md contract.")
    p.add_argument("--role", required=True)
    p.add_argument("--bead", required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--session-purpose", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--read", required=True)
    p.add_argument("--produce", required=True)
    p.add_argument("--acceptance", required=True)
    p.add_argument("--authority-and-limits", required=True)
    p.add_argument("--responsibility")
    p.add_argument("--heading")
    p.add_argument("--session-name")
    p.add_argument("--cwd", default=".")
    p.set_defaults(func=cmd_assignment_contract)

    p = sub.add_parser("ensure-layout", help="Create §10 directories for a Bead.")
    p.add_argument("--bead-id", required=True)
    p.add_argument("--session", action="append", default=[])
    p.add_argument("--cwd", default=".")
    p.set_defaults(func=cmd_ensure_layout)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # pragma: no cover - top-level guard
        print(f"mba-primitives: error: {exc}", file=sys.stderr)
        return 99


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
