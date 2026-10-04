"""Fail the build when prose carries a character the house register bans.

The em dash (U+2014) is banned outright across this portfolio. It was purged
from the sibling `edge-ai-refusal-runtime` by hand, and a branch reintroduced
seven of them two months later in the single file its pull request existed for,
while that commit's own message claimed they were gone. Nothing read prose, so a
green gate and a confident claim were wrong at the same time. This reads it.

What it deliberately does not read matters as much as what it does:

* **Fenced blocks and inline spans in Markdown.** They hold terminal
  transcripts and quote literals a program emits. Rewriting those would
  misquote the artefact being reproduced.
* **Every string literal in Python that is not a docstring.** This package
  emits a string containing the character. A check reaching into literals would
  demand the code lie about its own output, and the decision to change what a
  program emits belongs in a commit of its own.
* **A line a human has marked as quoted material.** Put
  ``<!-- register: quoted -->`` on the line, or on the line before it, and the
  check skips it. That exists for the title of an external document and for a
  passage quoted verbatim: a gate that forces a misquote is worse than the
  convention it enforces. It requires a person to assert the exemption, and it
  shows up in the diff where a reviewer can disagree with it.

So the rule enforced here is narrow and honest: prose that a human wrote as
prose. Program output is a separate argument, and `git grep` finds it in a
second.
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: U+2014, written as an escape so this file does not trip its own check.
EM_DASH = "—"
FENCED = re.compile(r"```.*?```", re.DOTALL)
#: A human's assertion that a line quotes something, and may not be rewritten.
QUOTED = "<!-- register: quoted -->"
INLINE_CODE = re.compile(r"`[^`\n]*`")
#: Generated trees are not prose. A dot directory covers .git, .venv,
#: .pytest_cache and every future cache without another edit here.
SKIP = {"build", "dist", "node_modules", "htmlcov", ".eggs"}


def scanned(pattern: str) -> list[Path]:
    return sorted(
        path for path in ROOT.rglob(pattern)
        if not SKIP & set(path.parts)
        and not any(part.startswith(".") for part in path.parts)
        and not any(part.endswith(".egg-info") for part in path.parts)
    )


def documents() -> list[Path]:
    return scanned("*.md")


def modules() -> list[Path]:
    return scanned("*.py")


def markdown_prose(path: Path) -> list[tuple[int, str]]:
    """Lines of prose, numbered, with code blanked rather than removed.

    Blanked rather than removed so the line numbers still point at the file.
    """
    text = path.read_text(encoding="utf-8")
    blanked = INLINE_CODE.sub(
        lambda m: " " * len(m.group(0)),
        FENCED.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text),
    )
    lines = blanked.splitlines()
    exempt = {
        n for n, line in enumerate(lines, start=1) if QUOTED in line
    }
    exempt |= {n + 1 for n in exempt}          # the marker may sit on the line before
    return [
        (n, line) for n, line in enumerate(lines, start=1)
        if EM_DASH in line and n not in exempt
    ]


def python_prose(path: Path) -> list[tuple[int, str]]:
    """Docstrings and comments only, as (line number, text)."""
    source = path.read_text(encoding="utf-8")
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            doc = ast.get_docstring(node)
            if doc and EM_DASH in doc:
                found.append((getattr(node, "lineno", 1), doc))
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and EM_DASH in token.string:
            found.append((token.start[0], token.string))
    return found


def main() -> int:
    problems: list[str] = []
    for document in documents():
        for lineno, line in markdown_prose(document):
            problems.append(
                f"{document.relative_to(ROOT)}:{lineno}: {line.strip()[:76]}"
            )
    for module in modules():
        for lineno, _ in python_prose(module):
            problems.append(
                f"{module.relative_to(ROOT)}:{lineno}: U+2014 in a docstring or comment"
            )

    for problem in sorted(problems):
        print(problem, file=sys.stderr)
    if problems:
        print(
            f"\n{len(problems)} place(s) use U+2014 in prose. A pair enclosing an "
            "aside becomes parentheses, a label becomes a colon, anything else "
            "becomes a comma.",
            file=sys.stderr,
        )
        return 1

    print(
        f"prose register clean: {len(documents())} document(s), "
        f"{len(modules())} module(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
