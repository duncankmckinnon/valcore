#!/usr/bin/env python3
"""Sync README.md's command-summary table from the one in the use-valcore skill's
reference.md.

reference.md is the source of truth for which CLI commands exist and what each one
does; README.md carries a copy for readers who never install the skill. Edit the table
in reference.md, then run this script with no arguments to update README.md, or with
``--check`` (as tests/test_docs.py does) to verify the two already match without
writing anything.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "valcore" / "skills" / "use-valcore" / "reference.md"
TARGET = ROOT / "README.md"
START = "<!-- COMMANDS:START -->"
END = "<!-- COMMANDS:END -->"
_BLOCK_RE = re.compile(re.escape(START) + r"\n(.*?)\n" + re.escape(END), re.DOTALL)


def _extract_block(text: str, path: Path) -> str:
    match = _BLOCK_RE.search(text)
    if not match:
        raise SystemExit(f"{path}: missing {START} / {END} markers")
    return match.group(1)


def _replace_block(text: str, block: str) -> str:
    return _BLOCK_RE.sub(f"{START}\n{block}\n{END}", text, count=1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Verify README.md is in sync; write nothing."
    )
    args = parser.parse_args()

    source_block = _extract_block(SOURCE.read_text(), SOURCE)
    target_text = TARGET.read_text()
    target_block = _extract_block(target_text, TARGET)

    if source_block == target_block:
        return 0

    if args.check:
        print(
            f"{TARGET} is out of sync with {SOURCE}. "
            "Run `python scripts/sync_command_table.py` to update it."
        )
        return 1

    TARGET.write_text(_replace_block(target_text, source_block))
    print(f"Updated {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
