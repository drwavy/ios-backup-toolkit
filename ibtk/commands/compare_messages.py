"""
Compare two directories of ibtk-format message transcripts (e.g. before
vs. after a merge, or two independent extractions of the same backup)
and report content-level differences -- useful to sanity-check a merge
actually captured everything, or to verify two extraction runs agree.

Does not modify anything; prints a report and optionally writes it to
a file.
"""

from pathlib import Path

from ibtk.text_merge import build_index, match_conversations, parse_transcript, diff_events


def run(a_dir: Path, b_dir: Path, report_path: Path = None):
    a_index = build_index(a_dir)
    b_index = build_index(b_dir)
    print(f"A: {len(a_index)} conversations. B: {len(b_index)} conversations.")

    matched, a_only, b_only = match_conversations(a_index, b_index)
    print(f"Matched {len(matched)} conversations by name.\n")

    report_lines = []
    total_only_a = 0
    total_only_b = 0
    total_matched_messages = 0
    worth_reviewing = []

    for a_stem, b_stem in matched:
        a_events = parse_transcript(a_index[a_stem].read_text(encoding="utf-8", errors="replace"))
        b_events = parse_transcript(b_index[b_stem].read_text(encoding="utf-8", errors="replace"))
        only_a, only_b, matched_count = diff_events(a_events, b_events)

        total_only_a += sum(only_a.values())
        total_only_b += sum(only_b.values())
        total_matched_messages += matched_count

        if only_a or only_b:
            worth_reviewing.append((a_stem, sum(only_a.values()), sum(only_b.values())))
            report_lines.append(f"\n{a_stem}")
            report_lines.append(f"  Matched: {matched_count}  Only in A: {sum(only_a.values())}  Only in B: {sum(only_b.values())}")
            for t in list(only_a)[:10]:
                report_lines.append(f"    ONLY IN A: {t[:150]!r}")
            for t in list(only_b)[:10]:
                report_lines.append(f"    ONLY IN B: {t[:150]!r}")

    print("=" * 60)
    print(f"  Total matched messages : {total_matched_messages}")
    print(f"  Total only-in-A         : {total_only_a}")
    print(f"  Total only-in-B         : {total_only_b}")
    print(f"  Conversations only in A : {len(a_only)}")
    print(f"  Conversations only in B : {len(b_only)}")
    print("=" * 60)

    if a_only:
        print(f"\nConversations only in A: {sorted(a_only)[:10]}{'...' if len(a_only) > 10 else ''}")
    if b_only:
        print(f"Conversations only in B: {sorted(b_only)[:10]}{'...' if len(b_only) > 10 else ''}")
    if worth_reviewing:
        print(f"\nConversations with content differences (top 10 by size):")
        for stem, only_a_n, only_b_n in sorted(worth_reviewing, key=lambda x: x[1] + x[2], reverse=True)[:10]:
            print(f"  {stem}: {only_a_n} only-in-A, {only_b_n} only-in-B")

    if report_path:
        report_path.write_text("\n".join(report_lines), encoding="utf-8")
        print(f"\nFull report written to {report_path}")
