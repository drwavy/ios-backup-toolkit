"""
Merge two or more media directories into one organized, deduplicated
output -- useful when you've extracted from multiple backups (old +
new, or two different phones) and want one consolidated library instead
of scattered, partially-overlapping folders.

Deduplicates by actual file content (SHA256), not filename, since
different extraction runs/tools routinely produce the same underlying
photo/video/audio under completely different names. Files are
categorized into Photos_and_Videos / Voice_Messages / Shared_Music_Audio
/ Documents by extension. A duplicate log records every file that was
skipped as a content-duplicate of something already copied, so nothing
is silently dropped without a record.

Sources are read-only; nothing is moved or modified in place.
"""

import os
import sys
import shutil
import hashlib
from pathlib import Path
from collections import defaultdict

from ibtk.creation_time import set_creation_time

VOICE_EXTS = {".caf", ".amr"}
MEDIA_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp", ".mov", ".mp4", ".m4v", ".3gp"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav"}

CATEGORY_DIRS = {
    "voice": "Voice_Messages",
    "media": "Photos_and_Videos",
    "audio": "Shared_Music_Audio",
    "doc": "Documents",
}


def category_for(ext: str) -> str:
    ext = ext.lower()
    if ext in VOICE_EXTS:
        return "voice"
    if ext in MEDIA_EXTS:
        return "media"
    if ext in AUDIO_EXTS:
        return "audio"
    return "doc"


def hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def unique_dest_name(dest_dir: Path, name: str) -> Path:
    candidate = dest_dir / name
    if not candidate.exists():
        return candidate
    stem, ext = os.path.splitext(name)
    n = 2
    while True:
        candidate = dest_dir / f"{stem}_{n}{ext}"
        if not candidate.exists():
            return candidate
        n += 1


def run(source_dirs, out_dir: Path):
    source_dirs = [Path(d) for d in source_dirs]
    for cat_dir in CATEGORY_DIRS.values():
        (out_dir / cat_dir).mkdir(parents=True, exist_ok=True)

    all_files = []
    for src_root in source_dirs:
        all_files.extend(p for p in src_root.rglob("*") if p.is_file() and p.name != ".DS_Store")

    print(f"Found {len(all_files)} files across {len(source_dirs)} source director{'y' if len(source_dirs) == 1 else 'ies'}. Hashing and copying ...\n")

    seen_hashes = {}
    duplicate_log = []
    per_category_count = {k: 0 for k in CATEGORY_DIRS}
    errors = 0
    bytes_saved = 0

    for i, src_path in enumerate(all_files, start=1):
        if i % 500 == 0:
            print(f"  ... processed {i}/{len(all_files)}")
        try:
            file_hash = hash_file(src_path)
        except Exception as ex:
            print(f"  ERROR hashing {src_path}: {ex}")
            errors += 1
            continue

        if file_hash in seen_hashes:
            duplicate_log.append((str(src_path), str(seen_hashes[file_hash])))
            try:
                bytes_saved += src_path.stat().st_size
            except Exception:
                pass
            continue

        seen_hashes[file_hash] = src_path
        cat = category_for(src_path.suffix)
        dest_path = unique_dest_name(out_dir / CATEGORY_DIRS[cat], src_path.name)

        try:
            src_mtime = src_path.stat().st_mtime
            shutil.copy2(src_path, dest_path)
            os.utime(dest_path, (src_mtime, src_mtime))
            set_creation_time(str(dest_path), src_mtime)
            per_category_count[cat] += 1
        except Exception as ex:
            errors += 1
            print(f"  FAILED to copy {src_path.name}: {ex}")

    if duplicate_log:
        log_path = out_dir / "duplicate_files_log.txt"
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("Files skipped as exact-content duplicates of an already-copied file:\n\n")
            for src, canonical in duplicate_log:
                f.write(f"DUPLICATE: {src}\n  (same content as) {canonical}\n\n")
        print(f"\nDuplicate log written to: {log_path}")

    print("\n" + "=" * 60)
    print(f"  Total files scanned  : {len(all_files)}")
    print(f"  Unique files copied  : {sum(per_category_count.values())}")
    for cat_key, cat_dir in CATEGORY_DIRS.items():
        print(f"    {cat_dir:<20}: {per_category_count[cat_key]}")
    print(f"  Duplicates skipped   : {len(duplicate_log)} ({bytes_saved / 1024 / 1024:.1f} MB saved)")
    print(f"  Errors               : {errors}")
    print(f"  Output folder        : {out_dir}")
    print("=" * 60)
