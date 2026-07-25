#!/usr/bin/env python3
"""Fail when built release archives contain generated or private-only files."""
from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

FORBIDDEN_NAMES = {".env", ".ds_store"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".sqlite", ".sqlite3", ".db", ".csv"}
FORBIDDEN_PARTS = {"__pycache__", ".git", ".pytest_cache"}


def archive_names(path: Path) -> list[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            return archive.getnames()
    raise ValueError(f"unsupported release archive: {path.name}")


def forbidden_reason(name: str) -> str | None:
    candidate = PurePosixPath(name)
    folded_parts = {part.casefold() for part in candidate.parts}
    if candidate.is_absolute() or ".." in candidate.parts:
        return "unsafe_archive_path"
    if folded_parts & FORBIDDEN_PARTS:
        return "generated_or_vcs_path"
    if candidate.name.casefold() in FORBIDDEN_NAMES:
        return "private_filename"
    if candidate.suffix.casefold() in FORBIDDEN_SUFFIXES:
        return "private_or_generated_suffix"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist", nargs="?", type=Path, default=Path("dist"))
    args = parser.parse_args()
    archives = sorted([*args.dist.glob("*.whl"), *args.dist.glob("*.tar.gz")])
    if not archives:
        print("release_artifact_scan archives=0 findings=1")
        return 2
    findings: list[tuple[str, str, str]] = []
    for archive in archives:
        for name in archive_names(archive):
            reason = forbidden_reason(name)
            if reason:
                findings.append((archive.name, name, reason))
    for archive, name, reason in findings:
        print(f"{archive}:{name}: {reason}")
    print(f"release_artifact_scan archives={len(archives)} findings={len(findings)}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
