#!/usr/bin/env python3
"""Scan public release text for generic privacy and secret classes.

Project-specific names belong in an external policy file passed with
``--extra-policy``; they are intentionally not embedded in this repository.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

SKIP_PARTS = {".git", ".venv", "build", "dist", "__pycache__", ".pytest_cache"}
TEXT_SUFFIXES = {"", ".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".json", ".jsonl", ".ini", ".cfg"}
RULES = {
    "absolute_home": re.compile(r"(?<![A-Za-z0-9.:])/(?:Users|home)/[A-Za-z0-9._-]+/"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "secret_assignment": re.compile(r"(?i)(?:api[_-]?key|token|secret|password)\s*[:=]\s*['\"][^'\"\s]{8,}"),
    "telegram_identifier": re.compile(r"(?i)(?:chat|thread|topic|telegram)[_-]?id\s*[:=]\s*['\"]?\d{6,}"),
}


def iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
            continue
        if path.resolve() == Path(__file__).resolve():
            continue
        if path.suffix.casefold() in TEXT_SUFFIXES:
            yield path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    parser.add_argument("--extra-policy", type=Path, help="external newline-delimited literal denylist")
    args = parser.parse_args()
    literals = []
    if args.extra_policy:
        literals = [line.strip().casefold() for line in args.extra_policy.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    findings = []
    for path in iter_files(args.root.resolve()):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        label = path.relative_to(args.root.resolve()).as_posix()
        for name, rule in RULES.items():
            for match in rule.finditer(text):
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end = text.find("\n", match.end())
                line = text[line_start:None if line_end < 0 else line_end]
                if name == "absolute_home" and "_hp(" in line:
                    continue
                findings.append((label, name, text.count("\n", 0, match.start()) + 1))
        folded = text.casefold()
        for literal in literals:
            if literal in folded:
                findings.append((label, "external_policy", 0))
    for label, name, line in findings:
        print(f"{label}:{line}: {name}")
    print(f"privacy_scan files={sum(1 for _ in iter_files(args.root.resolve()))} findings={len(findings)}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
