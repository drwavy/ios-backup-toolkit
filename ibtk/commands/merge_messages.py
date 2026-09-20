"""
Merge two directories of ibtk-format message transcripts -- e.g. from
an old backup and a newer one, or from two different phones sharing a
family plan -- into one output directory, with content-level
deduplication rather than blind concatenation.

Conversations are matched by normalized filename. Within a matched
pair, messages are compared by NORMALIZED TEXT CONTENT (not by exact
line), since a message existing in both sources is common and should
appear once, not twice, in the merged output. Unmatched conversations
(present in only one source) are copied through unchanged.
"""

from pathlib import Path

from ibtk.text_merge import (
    build_index, match_conversations, parse_transcript,
    normalize_for_diff, write_merged_transcript,
)


def run(primary_dir: Path, secondary_dir: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    primary_index = build_index(primary_dir)
    secondary_index = build_index(secondary_dir)
    print(f"Primary: {len(primary_index)} conversations. Secondary: {len(secondary_index)} conversations.")

    matched, primary_only, secondary_only = match_conversations(primary_index, secondary_index)
    print(f"Matched {len(matched)} conversations by name.")

    written = 0
    total_merged_in = 0

    for primary_stem, secondary_stem in matched:
        primary_text = primary_index[primary_stem].read_text(encoding="utf-8", errors="replace")
        secondary_text = secondary_index[secondary_stem].read_text(encoding="utf-8", errors="replace")
        primary_events = parse_transcript(primary_text)
        secondary_events = parse_transcript(secondary_text)

        seen = set(normalize_for_diff(e["text"]) for e in primary_events)
        merged = [(e["sender"], e["text"]) for e in primary_events]
        added = 0
        for e in secondary_events:
            key = normalize_for_diff(e["text"])
            if key not in seen:
                seen.add(key)
                merged.append((e["sender"], e["text"]))
                added += 1

        write_merged_transcript(out_dir / f"{primary_stem}.txt", primary_stem, merged)
        written += 1
        total_merged_in += added

    for stem in primary_only:
        (out_dir / f"{stem}.txt").write_text(
            primary_index[stem].read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
        )
        written += 1

    for stem in secondary_only:
        (out_dir / f"{stem}.txt").write_text(
            secondary_index[stem].read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
        )
        written += 1

    print(f"\nWrote {written} conversation files to {out_dir}")
    print(f"  Matched conversations merged: {len(matched)} (+{total_merged_in} messages pulled in from secondary)")
    print(f"  Primary-only conversations copied through: {len(primary_only)}")
    print(f"  Secondary-only conversations copied through: {len(secondary_only)}")
