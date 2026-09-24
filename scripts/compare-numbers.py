#!/usr/bin/env python3
"""Compare the numbers in an original document and its translation.

Usage: python scripts/compare-numbers.py ORIGINAL TRANSLATION

A faithful translation keeps every number. This counts the numbers in both files,
then lists the lines where they differ. A number written out as a word ("three
kinds") shows up here too; explain every difference before committing. Decimal
commas are read as decimal points, and digits inside URLs are ignored.
"""

from __future__ import annotations

import difflib
import re
import sys
from collections import Counter
from pathlib import Path

NUMBER = re.compile(r"[−-]?\d+(?:[.,]\d+)?")
URL = re.compile(r"\(https?://[^)]*\)")


def numbered_lines(path: Path) -> list[tuple[int, str, str]]:
    """Return (line number, the line's numbers, the line) for every line holding a number."""
    lines = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        found = [n.replace("−", "-").replace(",", ".") for n in NUMBER.findall(URL.sub("", line))]
        if found:
            lines.append((index, " ".join(found), line))
    return lines


def tally(lines: list[tuple[int, str, str]]) -> Counter:
    return Counter(n for _, numbers, _ in lines for n in numbers.split())


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python scripts/compare-numbers.py ORIGINAL TRANSLATION", file=sys.stderr)
        return 2
    sys.stdout.reconfigure(encoding="utf-8")

    original, translation = (numbered_lines(Path(p)) for p in sys.argv[1:])
    missing = tally(original) - tally(translation)
    added = tally(translation) - tally(original)
    print(f"{sum(tally(original).values())} numbers in the original, {sum(tally(translation).values())} in the translation")
    if not missing and not added:
        print("Every number matches.")
        return 0

    print("Only in the original:   ", dict(sorted(missing.items())) or "none")
    print("Only in the translation:", dict(sorted(added.items())) or "none")
    matcher = difflib.SequenceMatcher(None, [x[1] for x in original], [x[1] for x in translation], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        for index, numbers, line in original[i1:i2]:
            print(f"  original    {index:4d} [{numbers}] {line[:100]}")
        for index, numbers, line in translation[j1:j2]:
            print(f"  translation {index:4d} [{numbers}] {line[:100]}")
        print("  --")
    return 1


if __name__ == "__main__":
    sys.exit(main())
