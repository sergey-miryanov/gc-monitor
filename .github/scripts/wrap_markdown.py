"""Rewrap the prose in a Markdown file, leaving everything else byte for byte.

    python .github/scripts/wrap_markdown.py --width 80 docs/formats.md

Verbatim: fenced blocks, tables, headings and HTML. Rewrapped: paragraphs,
block quotes, and list items with their continuation lines.

A link reads badly split over two lines, and stops being greppable, so the
spaces inside ``[text](target)`` and inside a short parenthesised list are held
together while the wrapping happens.

An indented code block and a list continuation line look alike, and rewrapping
one as the other would turn code into prose. A file carrying an indented block
is reported and left alone; put it in a fence instead.

The tool compares the word sequence and the verbatim lines before and after,
and writes nothing when either moved. Pass ``--check`` to report without
writing, which is what CI would call.
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
from pathlib import Path

FENCE = re.compile(r"^\s*(```|~~~)")
LIST_ITEM = re.compile(r"^(\s*(?:[-*+]|\d+\.)\s+)(.*)$")
QUOTE = re.compile(r"^(\s*>\s*)(.*)$")
VERBATIM = re.compile(r"^(\s*\||#{1,6}\s|<)")
INDENTED = re.compile(r"^ {4,}\S")
# Spaces that must not become a line break.
GLUE = re.compile(r"\[[^\]]*\]\([^)]*\)|\((?:[^()\s]+[,;]\s+){1,4}[^()\s]+\)")


def _wrap(lines: list[str], first: str, rest: str, width: int) -> list[str]:
    joined = " ".join(line.strip() for line in lines)
    glued = GLUE.sub(lambda m: m.group(0).replace(" ", "\0"), joined)
    wrapped = textwrap.wrap(
        glued,
        width=width,
        initial_indent=first,
        subsequent_indent=rest,
        break_long_words=False,
        break_on_hyphens=False,
    )
    return [line.replace("\0", " ") for line in wrapped]


def rewrap(text: str, width: int) -> str:
    out: list[str] = []
    para: list[str] = []
    first = rest = ""
    in_fence = False

    def flush() -> None:
        nonlocal para, first, rest
        if para:
            out.extend(_wrap(para, first, rest, width))
        para = []
        first = rest = ""

    for line in text.split("\n"):
        if FENCE.match(line):
            flush()
            in_fence = not in_fence
            out.append(line)
        elif in_fence:
            out.append(line)
        elif not line.strip():
            flush()
            out.append("")
        elif VERBATIM.match(line) and not para:
            flush()
            out.append(line)
        elif quote := QUOTE.match(line):
            if not para:
                first = rest = quote.group(1)
            para.append(quote.group(2))
        elif item := LIST_ITEM.match(line):
            flush()
            first, rest = item.group(1), " " * len(item.group(1))
            para.append(item.group(2))
        else:
            para.append(line)

    flush()
    return "\n".join(out)


def _parts(text: str) -> tuple[list[str], list[str]]:
    """(prose words, verbatim lines), the two things a rewrap must preserve."""
    prose: list[str] = []
    verbatim: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if FENCE.match(line):
            in_fence = not in_fence
            verbatim.append(line)
        elif in_fence or VERBATIM.match(line):
            verbatim.append(line)
        else:
            prose.extend(line.replace(">", " ").split())
    return prose, verbatim


def _indented_code(text: str) -> list[int]:
    """Line numbers of indented blocks that no list item could be continuing."""
    found: list[int] = []
    in_fence = False
    open_list = False
    for number, line in enumerate(text.split("\n"), 1):
        if FENCE.match(line):
            in_fence = not in_fence
        elif in_fence or not line.strip():
            continue
        elif LIST_ITEM.match(line):
            open_list = True
        elif INDENTED.match(line):
            if not open_list:
                found.append(number)
        elif not line.startswith(" "):
            open_list = False
    return found


def process(path: Path, width: int, check: bool) -> bool:
    original = path.read_text(encoding="utf-8", newline="")
    if "\r" in original:
        print(f"{path}: CRLF, skipped")
        return False
    if lines := _indented_code(original):
        print(f"{path}: indented code at line(s) {lines}, skipped")
        return False

    result = rewrap(original, width)
    if _parts(original) != _parts(result):
        print(f"{path}: content moved, not written")
        return False

    over = [line for line in result.split("\n") if len(line) > width and not VERBATIM.match(line)]
    if original == result:
        print(f"{path}: already wrapped at {width}")
        return True
    if check:
        print(f"{path}: would rewrap at {width}")
        return False
    path.write_text(result, encoding="utf-8", newline="")
    print(f"{path}: rewrapped at {width}, {len(over)} line(s) still over")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--width", type=int, default=80)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    ok = [process(path, args.width, args.check) for path in args.paths]
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
