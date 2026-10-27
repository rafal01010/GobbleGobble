#!/usr/bin/env python3
import argparse
import html
import re
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit


def anchors(text: str) -> set[str]:
    found: set[str] = set()
    counts: Counter[str] = Counter()
    fenced = False
    for line in re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        if fenced:
            continue
        rendered = re.sub(r"`+[^`]*`+", "", line)
        found.update(re.findall(r'\bid=["\']([^"\']+)["\']', rendered))
        heading = re.match(r"^#{1,6}\s+(.+?)\s*#*$", line)
        if heading:
            label = html.unescape(re.sub(r"<[^>]*>", "", heading[1]))
            label = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", label)
            slug = re.sub(r"[^\w -]", "", label.lower()).replace(" ", "-")
            duplicate = counts[slug]
            counts[slug] += 1
            found.add(f"{slug}-{duplicate}" if duplicate else slug)
    return found


def audit(root: Path) -> tuple[list[str], int]:
    errors = []
    files = [root / "README.md", *sorted((root / "docs").rglob("*.md"))]
    private = re.compile(
        r"\bGG-\d+(?:\.\d+[A-Z]?)?\b|IMPLEMENTATION_PLAN\.md|PSTACK_PROJECT_PROMPTS(?:\.txt)?",
        re.IGNORECASE,
    )
    for file in files:
        if not file.is_file():
            errors.append(f"{file.relative_to(root)}: missing document")
            continue
        text = file.read_text()
        label = file.relative_to(root)
        for number, line in enumerate(text.splitlines(), 1):
            if private.search(line):
                errors.append(f"{label}:{number}: private planning reference")
        links = re.findall(r"!?\[[^]\n]*\]\(([^)\n]+)\)", text)
        links += re.findall(r"^\s*\[[^]]+\]:\s*(\S+)", text, re.MULTILINE)
        for raw in links:
            target = raw.strip().split(' "', 1)[0].strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme in ("file", "vscode"):
                errors.append(f"{label}: link depends on local machine: {target}")
                continue
            if parsed.scheme or parsed.netloc:
                continue
            destination = (
                (file.parent / unquote(parsed.path)).resolve() if parsed.path else file
            )
            if not destination.is_relative_to(root):
                errors.append(f"{label}: link leaves repository: {target}")
                continue
            relative = destination.relative_to(root)
            if relative.parts[:2] in (
                ("data", "verification"),
                ("data", "runtime"),
            ) or relative.parts[:1] == ("design_temp",):
                errors.append(
                    f"{label}: link depends on private local artifact: {target}"
                )
            elif not destination.exists():
                errors.append(f"{label}: missing local link: {target}")
            elif (
                parsed.fragment
                and destination.suffix == ".md"
                and unquote(parsed.fragment) not in anchors(destination.read_text())
            ):
                errors.append(f"{label}: missing heading anchor: {target}")
    return errors, len(files)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check public documentation for private-plan references and broken local links."
    )
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    args = parser.parse_args()
    errors, count = audit(args.root.resolve())
    for error in errors:
        print(error)
    if errors:
        print(f"FAIL: {len(errors)} documentation problems")
        return 1
    print(
        f"PASS: {count} public Markdown documents, no private-plan references or broken local links"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
