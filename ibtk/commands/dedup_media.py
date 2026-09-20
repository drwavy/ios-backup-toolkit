"""
Find genuine content-duplicate files within a single directory (e.g.
left over after a manual, filename-only copy that didn't account for
the same file existing under two different names) and MOVE the
redundant copies into a "_duplicates_for_review" subfolder -- NOT
deleted outright. Review the quarantine folder yourself and delete it
once you're satisfied nothing important is in it.

Groups files by content hash (SHA256). Within each duplicate group,
keeps the most human-readable filename (shortest, non-UUID-looking
name) and quarantines the rest.
"""

import re
import sys
import shutil
import hashlib
from pathlib import Path
from collections import defaultdict

UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.\w+$', re.IGNORECASE)


def hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def name_tier(name: str) -> int:
    """Lower is more preferred (kept)."""
    if UUID_RE.match(name):
        return 1
    return 0


def choose_keeper(paths):
    return sorted(paths, key=lambda p: (name_tier(p.name), len(p.name), p.name.lower()))[0]


def run(target_dir: Path, quarantine_subdir: str = "_duplicates_for_review"):
    quarantine_dir = target_dir / quarantine_subdir
    report_path = target_dir.parent / f"{target_dir.name}_dedup_report.txt"

    all_files = sorted(p for p in target_dir.iterdir()
                        if p.is_file() and p.name != ".DS_Store" and p.parent == target_dir)
    print(f"Hashing {len(all_files)} files in {target_dir} ...")

    by_hash = defaultdict(list)
    for i, p in enumerate(all_files, start=1):
        if i % 100 == 0:
            print(f"  ... hashed {i}/{len(all_files)}")
        try:
            by_hash[hash_file(p)].append(p)
        except Exception as ex:
            print(f"  ERROR hashing {p.name}: {ex}")

    dup_groups = {h: paths for h, paths in by_hash.items() if len(paths) > 1}
    print(f"\nFound {len(dup_groups)} groups of duplicate content "
          f"({sum(len(v) - 1 for v in dup_groups.values())} redundant files).\n")

    if not dup_groups:
        print("Nothing to do.")
        return

    quarantine_dir.mkdir(exist_ok=True)
    report_lines = []
    moved = 0
    errors = 0

    for file_hash, paths in sorted(dup_groups.items(), key=lambda kv: kv[1][0].name.lower()):
        keeper = choose_keeper(paths)
        losers = [p for p in paths if p != keeper]

        report_lines.append(f"KEEP: {keeper.name}")
        for loser in losers:
            report_lines.append(f"  -> moved to quarantine: {loser.name}")
        report_lines.append("")

        for loser in losers:
            try:
                dest = quarantine_dir / loser.name
                if dest.exists():
                    dest = quarantine_dir / f"{loser.stem}__dup{loser.suffix}"
                shutil.move(str(loser), str(dest))
                moved += 1
            except Exception as ex:
                errors += 1
                print(f"  FAILED to move {loser.name}: {ex}")

    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"  Duplicate groups found    : {len(dup_groups)}")
    print(f"  Files moved to quarantine : {moved}")
    print(f"  Errors                    : {errors}")
    print(f"  Report written to         : {report_path}")
    print(f"  Quarantine folder         : {quarantine_dir}")
    print("  Review it, then delete it yourself once satisfied.")
    print("=" * 60)
