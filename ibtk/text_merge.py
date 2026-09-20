"""
Shared utilities for merging and comparing message transcripts produced
by `ibtk extract-messages`. Two transcripts of the "same" conversation
(e.g. from an old backup and a newer one, or from two different phones
on a shared family plan) can be merged with content-level deduplication
rather than a blind concatenation, which would double up every message
present in both.

Deliberately scoped to ibtk's OWN output format (see
ibtk/commands/messages.py) rather than attempting to parse arbitrary
third-party export tools -- every export tool has its own idiosyncratic
format, and a generic parser for "any" of them would be unreliable. If
you need to reconcile against a specific third-party tool's format,
write a small adapter that converts its output into ibtk's format first
(one "[timestamp] sender: text" line per message), then use these
commands normally.
"""

import re
from pathlib import Path
from collections import Counter

LINE_RE = re.compile(r'^\[([^\]]+)\]\s+(.*)$')
GROUP_SUFFIX_RE = re.compile(r'\s*_\d+$')

QUOTE_NORMALIZE_TABLE = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
})


def normalize_for_diff(text: str) -> str:
    """Normalizes quote/apostrophe style so text from two different
    sources compares equal when the only difference is e.g. a straight
    vs. typographic apostrophe."""
    return text.translate(QUOTE_NORMALIZE_TABLE)


def parse_transcript(text: str):
    """Parses one ibtk-format transcript file into a list of
    {"sender": str, "text": str} events. Skips the header line(s)
    before the first "[timestamp] sender: text" line."""
    lines = text.split("\n")
    events = []
    for line in lines:
        m = LINE_RE.match(line)
        if not m:
            continue
        rest = m.group(2)
        if ": " not in rest:
            continue
        sender, msg_text = rest.split(": ", 1)
        if msg_text.strip():
            events.append({"sender": sender.strip(), "text": msg_text.strip()})
    return events


def normalize_conversation_key(stem: str) -> str:
    """Normalizes a conversation filename stem for matching across two
    extraction runs -- strips a numeric disambiguation suffix ibtk adds
    on a filename collision (e.g. "_2"), and lowercases."""
    return GROUP_SUFFIX_RE.sub("", stem).strip().lower()


def build_index(directory: Path):
    return {p.stem: p for p in directory.iterdir() if p.is_file() and p.suffix == ".txt"}


def match_conversations(a_index: dict, b_index: dict):
    """Returns (matched, a_only, b_only) where matched is
    [(a_stem, b_stem)], and a_only/b_only are lists of unmatched stems."""
    b_by_key = {normalize_conversation_key(k): k for k in b_index}
    matched = []
    a_only = []
    for a_stem in a_index:
        key = normalize_conversation_key(a_stem)
        if key in b_by_key:
            matched.append((a_stem, b_by_key.pop(key)))
        else:
            a_only.append(a_stem)
    b_only = list(b_by_key.values())
    return matched, a_only, b_only


def diff_events(a_events, b_events):
    """Returns (only_in_a, only_in_b, matched_count) as Counters/int,
    comparing normalized text content (sender is informational, not
    part of the match key, since sender resolution can legitimately
    differ slightly between two extraction runs -- e.g. one run had a
    contact card the other didn't)."""
    a_texts = Counter(normalize_for_diff(e["text"]) for e in a_events)
    b_texts = Counter(normalize_for_diff(e["text"]) for e in b_events)
    only_a = a_texts - b_texts
    only_b = b_texts - a_texts
    matched = sum((a_texts & b_texts).values())
    return only_a, only_b, matched


def write_merged_transcript(path: Path, display_name: str, all_events):
    """Writes a merged transcript in ibtk's own format. all_events
    should already be deduplicated and sorted by whatever order is
    desired (chronological if timestamps are available and reliable;
    otherwise source order is preserved)."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"Conversation: {display_name}\n{'=' * 60}\n\n")
        for sender, text in all_events:
            f.write(f"[merged] {sender}: {text}\n")
