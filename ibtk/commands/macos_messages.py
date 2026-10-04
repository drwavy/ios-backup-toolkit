"""
Extract a macOS Messages store (a copy of ~/Library/Messages: chat.db plus
its -wal/-shm and the Attachments/ tree) into one transcript per
conversation and a media library with recovered real dates. Output matches
`extract-messages` / `extract-photos`, so merge-messages, compare-messages,
merge-media and dedup-media work on the result.

Unlike the iOS-backup commands, this one reads a plain folder, not a
Manifest.db-indexed backup, so it takes --messages-dir rather than
--backup (and is therefore not part of extract-all).

Things that differ from the iOS sms.db path, each of which is a silent
data-loss bug if handled the naive way:

1. WAL. chat.db is WAL-mode. Opening chat.db alone -- or with
   `immutable=1` -- ignores chat.db-wal and drops every transaction that
   was not yet checkpointed. Opening in place can also checkpoint and
   MUTATE the source. So db + -wal + -shm are copied to a scratch dir and
   the copy is opened.

2. Message body. Since macOS Ventura, `message.text` is frequently NULL and
   the body lives in `message.attributedBody` (a typedstream blob).
   `WHERE text IS NOT NULL` -- what the iOS extractor uses -- would silently
   discard those messages.

3. Units. message.date is nanoseconds since 2001-01-01 UTC;
   attachment.created_date is seconds. Magnitude detection handles both.

4. Timezones. The Cocoa epoch is UTC. Converting to a naive datetime and
   calling .timestamp() reinterprets it as LOCAL time and shifts file dates
   by the UTC offset. Here Cocoa -> Unix epoch is plain arithmetic, and a
   timezone is applied only when formatting text.

5. Chat fragmentation. One person is often several `chat` rows (iMessage
   and SMS rows, rows from old Apple IDs). Transcripts are keyed by
   chat_identifier so a 1:1 history is not split across files.

6. Attachment paths in the DB are absolute/tilde paths from the ORIGINAL
   machine (~/Library/Messages/Attachments/xx/yy/GUID/name). They are
   remapped by the substring after "Attachments/".

7. Embedded dates are sanity-checked. A camera with a dead clock writes
   1970/2001 EXIF dates, and a received file's capture date cannot be later
   than the message that delivered it. Implausible embedded dates fall
   back to the message date.
"""

import csv
import datetime
import os
import re
import shutil
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from ibtk.commands.photos import (
    IMAGE_EXTS,
    VIDEO_EXTS,
    get_exif_epoch,
    get_mvhd_epoch,
    sniff_real_type,
)
from ibtk.creation_time import set_creation_time
from ibtk.timestamps import COCOA_UNIX_OFFSET, cocoa_to_unix

MIN_PLAUSIBLE = COCOA_UNIX_OFFSET  # embedded dates before 2001-01-01 are camera-clock garbage


# --------------------------------------------------------------------------
# time formatting
# --------------------------------------------------------------------------
def _fmt(epoch, local, pattern):
    tz = None if local else datetime.timezone.utc
    return datetime.datetime.fromtimestamp(epoch, tz).strftime(pattern)


def fmt_time(epoch, local):
    return "unknown time" if epoch is None else _fmt(epoch, local, "%Y-%m-%d %H:%M:%S")


def fmt_stamp(epoch, local):
    return "00000000_000000" if epoch is None else _fmt(epoch, local, "%Y%m%d_%H%M%S")


# --------------------------------------------------------------------------
# attributedBody (typedstream) decoding
# --------------------------------------------------------------------------
def decode_attributed_body(blob):
    """Pull the plain string out of an NSAttributedString typedstream.

    Layout around the payload:
        ... 'NSString' 01 94 84 01 '+' <len> <utf8 bytes> 86 84 ...
    <len> is one byte (< 0x80), or 0x81 + uint16 LE, or 0x82 + uint32 LE.
    Returns None if the blob doesn't match that shape.
    """
    if not blob:
        return None
    data = bytes(blob)
    i = data.find(b"NSString")
    if i == -1:
        return None
    j = data.find(b"\x01+", i, i + 24)
    if j == -1:
        return None
    pos = j + 2
    if pos >= len(data):
        return None
    first = data[pos]
    if first == 0x81:
        length = int.from_bytes(data[pos + 1:pos + 3], "little")
        pos += 3
    elif first == 0x82:
        length = int.from_bytes(data[pos + 1:pos + 5], "little")
        pos += 5
    else:
        length = first
        pos += 1
    return data[pos:pos + length].decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# contacts (optional: reuse the vCard written by `ibtk extract-contacts`)
# --------------------------------------------------------------------------
def normalize_phone(value):
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def load_vcf(path):
    mapping = {}
    if not path:
        return mapping
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"\r?\n[ \t]", "", text)  # unfold continuation lines
    name, ids = None, []
    for line in text.splitlines():
        up = line.upper()
        if up.startswith("BEGIN:VCARD"):
            name, ids = None, []
        elif up.startswith("END:VCARD"):
            if name:
                for ident in ids:
                    if ident:
                        mapping[ident] = name
        else:
            key, _, val = line.partition(":")
            base = key.split(";")[0].split(".")[-1].upper()
            if base == "FN":
                name = val.strip()
            elif base == "TEL":
                ids.append(normalize_phone(val))
            elif base == "EMAIL":
                ids.append(val.strip().lower())
    return mapping


def resolve(contacts, raw):
    if not raw:
        return raw
    if "@" in raw:
        return contacts.get(raw.strip().lower(), raw)
    return contacts.get(normalize_phone(raw), raw)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def safe_name(name):
    return re.sub(r'[/\\:*?"<>|\x00-\x1f]', "_", name or "").strip(" .") or "unnamed"


def table_columns(cur, table):
    return {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}


def stage_db(messages_dir, work_dir):
    """Copy chat.db and its -wal/-shm siblings into work_dir."""
    if not (messages_dir / "chat.db").exists():
        raise FileNotFoundError(f"No chat.db found in {messages_dir}")
    for suffix in ("", "-wal", "-shm"):
        src = messages_dir / f"chat.db{suffix}"
        if src.exists():
            shutil.copy2(src, work_dir / f"chat.db{suffix}")
    return work_dir / "chat.db"


def resolve_attachment(messages_dir, raw_path):
    if not raw_path:
        return None
    idx = raw_path.find("Attachments/")
    if idx == -1:
        return None
    path = messages_dir / "Attachments" / raw_path[idx + len("Attachments/"):]
    return path if path.is_file() else None


def best_epoch(path, msg_epoch):
    """Embedded EXIF/mvhd date if plausible, else the message date.
    Returns (epoch_or_None, 'embedded' | 'message' | 'none')."""
    ext = path.suffix.lower()
    embedded = None
    if ext in IMAGE_EXTS:
        embedded = get_exif_epoch(path)
    elif ext in VIDEO_EXTS:
        embedded = get_mvhd_epoch(path)
    if (
        embedded is not None
        and embedded >= MIN_PLAUSIBLE
        and (msg_epoch is None or embedded <= msg_epoch + 86400)
    ):
        return embedded, "embedded"
    if msg_epoch is not None:
        return msg_epoch, "message"
    return None, "none"


def apply_date(path, epoch):
    os.utime(path, (epoch, epoch))        # mtime first...
    set_creation_time(str(path), epoch)   # ...then birthtime (macOS lowers birthtime if mtime < it)


# --------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------
def run(messages_dir, out_dir, contacts_vcf=None, local_time=False, include_hidden=False):
    messages_dir = Path(messages_dir).expanduser()
    out_dir = Path(out_dir).expanduser()
    work = Path(tempfile.mkdtemp(prefix="ibtk_macos_messages_"))
    try:
        db_path = stage_db(messages_dir, work)
        _extract(db_path, messages_dir, out_dir, contacts_vcf, local_time, include_hidden)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _extract(db_path, messages_dir, out_dir, contacts_vcf, local_time, include_hidden):
    tx_dir = out_dir / "Messages"
    media_dir = out_dir / "Messages_Media"
    tx_dir.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # fold the copied WAL into the copied main file

    contacts = load_vcf(contacts_vcf)
    msg_cols = table_columns(cur, "message")
    att_cols = table_columns(cur, "attachment")

    handles = {r["ROWID"]: r["id"] for r in cur.execute("SELECT ROWID, id FROM handle")}
    chats = {
        r["ROWID"]: dict(r)
        for r in cur.execute("SELECT ROWID, chat_identifier, display_name, style FROM chat")
    }
    chat_key = {
        cid: (c["chat_identifier"] or f"chat_rowid_{cid}") for cid, c in chats.items()
    }

    key_people = defaultdict(set)
    for r in cur.execute("SELECT chat_id, handle_id FROM chat_handle_join"):
        if r["chat_id"] in chat_key:
            key_people[chat_key[r["chat_id"]]].add(r["handle_id"])
    key_group_name, key_style = {}, {}
    for cid, c in chats.items():
        k = chat_key[cid]
        key_style.setdefault(k, c.get("style"))
        if c.get("display_name"):
            key_group_name[k] = c["display_name"]

    # chat_message_join is the authoritative message -> chat link
    msg_chat = {}
    for r in cur.execute("SELECT chat_id, message_id FROM chat_message_join"):
        msg_chat.setdefault(r["message_id"], r["chat_id"])

    # display name + unique folder/file label per conversation
    display, label, used = {}, {}, Counter()
    for k in sorted(set(chat_key.values())):
        if k in key_group_name:
            name = key_group_name[k]
        else:
            people = [
                resolve(contacts, handles[h]) for h in key_people.get(k, ()) if handles.get(h)
            ]
            if key_style.get(k) == 45 or len(people) <= 1:
                name = (people[0] if people else k) if k.startswith("chat") else resolve(contacts, k)
            else:
                people.sort()
                name = ", ".join(people[:4]) + (f" +{len(people) - 4}" if len(people) > 4 else "")
        display[k] = name
        base = safe_name(name)
        used[base] += 1
        label[k] = base if used[base] == 1 else f"{base}_{used[base]}"

    # attachments per message (optional columns vary by macOS version)
    acols = ["a.ROWID AS att_id", "a.filename", "a.mime_type", "a.transfer_name"]
    for opt in ("is_sticker", "hide_attachment"):
        if opt in att_cols:
            acols.append(f"a.{opt}")
    atts = defaultdict(list)
    for r in cur.execute(
        f"SELECT maj.message_id AS mid, {', '.join(acols)} FROM message_attachment_join maj "
        "JOIN attachment a ON a.ROWID = maj.attachment_id ORDER BY maj.message_id, a.ROWID"
    ):
        atts[r["mid"]].append(dict(r))

    mcols = ["ROWID", "date", "is_from_me", "handle_id", "text"]
    if "attributedBody" in msg_cols:
        mcols.append("attributedBody")
    rows = cur.execute(f"SELECT {', '.join(mcols)} FROM message ORDER BY date, ROWID").fetchall()

    stats = Counter()
    events = defaultdict(list)
    index_rows = []

    for row in rows:
        stats["messages_total"] += 1
        chat_id = msg_chat.get(row["ROWID"])
        if chat_id is None or chat_id not in chat_key:
            stats["messages_no_chat"] += 1
            continue
        k = chat_key[chat_id]
        epoch = cocoa_to_unix(row["date"])
        outgoing = bool(row["is_from_me"])

        # Sender attribution is per-chat with a fallback, never a shared
        # literal "unknown" (see the note in commands/messages.py).
        if outgoing:
            sender = "Me"
        else:
            raw = handles.get(row["handle_id"])
            if raw:
                sender = resolve(contacts, raw)
            else:
                people = key_people.get(k, set())
                sole = handles.get(next(iter(people))) if len(people) == 1 else None
                sender = resolve(contacts, sole) if sole else "Unknown participant"

        text = row["text"]
        if not text and "attributedBody" in row.keys() and row["attributedBody"]:
            text = decode_attributed_body(row["attributedBody"])
            if text:
                stats["text_from_attributedBody"] += 1
        text = (text or "").replace("\ufffc", "").strip()  # U+FFFC marks inline attachments

        lines = [text] if text else []

        for att in atts.get(row["ROWID"], []):
            src = resolve_attachment(messages_dir, att["filename"])
            name = att["transfer_name"] or Path(att["filename"] or "").name or "attachment"
            if src is None:
                stats["attachments_missing"] += 1
                lines.append(f"[attachment missing on disk: {name}]")
                index_rows.append(
                    [display[k], row["ROWID"], att["att_id"], att["filename"], "",
                     att["mime_type"], "missing", ""]
                )
                continue
            hidden = (
                att.get("hide_attachment") == 1
                or att.get("is_sticker") == 1
                or src.name.endswith(".pluginPayloadAttachment")
            )
            if hidden and not include_hidden:
                stats["attachments_skipped_hidden"] += 1
                continue

            with open(src, "rb") as f:
                header = f.read(16)
            real_ext = sniff_real_type(header) or src.suffix.lower() or ".bin"
            direction = "sent" if outgoing else "recv"
            out_name = (
                f"{fmt_stamp(epoch, local_time)}_{direction}_{safe_name(src.stem)[:60]}"
                f"_{att['att_id']}{real_ext}"
            )
            chat_media = media_dir / label[k]
            chat_media.mkdir(parents=True, exist_ok=True)
            dest = chat_media / out_name
            shutil.copy2(src, dest)
            stats["attachments_exported"] += 1

            date_epoch, date_src = best_epoch(dest, epoch)
            if date_epoch is not None:
                apply_date(dest, date_epoch)
            stats[f"date_source_{date_src}"] += 1

            rel = dest.relative_to(out_dir)
            lines.append(f"[attachment] {rel}")
            index_rows.append(
                [display[k], row["ROWID"], att["att_id"], att["filename"], str(rel),
                 att["mime_type"], date_src,
                 fmt_time(date_epoch, False) if date_epoch is not None else ""]
            )

        if not lines:
            stats["messages_empty_skipped"] += 1  # system rows, group renames, etc.
            continue
        stats["messages_written"] += 1
        stamp = fmt_time(epoch, local_time)
        for line in lines:
            events[k].append(f"[{stamp}] {sender}: {line}")

    for k, lines in events.items():
        with open(tx_dir / f"{label[k]}.txt", "w", encoding="utf-8") as f:
            f.write(f"Conversation: {display[k]}\n{'=' * 60}\n\n")
            f.write("\n".join(lines) + "\n")

    with open(out_dir / "attachments_index.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["conversation", "message_rowid", "attachment_rowid", "original_db_path",
                    "output_path", "mime_type", "date_source", "date_utc"])
        w.writerows(index_rows)

    con.close()

    print(f"Conversations written : {len(events)}  -> {tx_dir}")
    print(
        f"Messages              : {stats['messages_total']} total, {stats['messages_written']} written, "
        f"{stats['messages_empty_skipped']} empty/system skipped, {stats['messages_no_chat']} with no chat"
    )
    print(f"  recovered from attributedBody (text was NULL): {stats['text_from_attributedBody']}")
    print(
        f"Attachments           : {stats['attachments_exported']} exported -> {media_dir}, "
        f"{stats['attachments_missing']} missing on disk, "
        f"{stats['attachments_skipped_hidden']} hidden/sticker/payload skipped"
    )
    print(
        f"  date source: embedded={stats['date_source_embedded']} "
        f"message={stats['date_source_message']} none={stats['date_source_none']}"
    )
    print(f"Timestamps in transcripts: {'local time' if local_time else 'UTC (matches ibtk extract-messages)'}")
    print(f"Audit trail           : {out_dir / 'attachments_index.csv'}")


def add_arguments(parser):
    parser.add_argument("--messages-dir", required=True,
                        help="Folder containing chat.db (+ -wal/-shm) and Attachments/")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--contacts-vcf",
                        help="vCard from `ibtk extract-contacts`, used to resolve names")
    parser.add_argument("--local-time", action="store_true",
                        help="Print transcript times in this machine's timezone "
                             "(default: UTC, to match ibtk transcripts)")
    parser.add_argument("--include-hidden", action="store_true",
                        help="Also export stickers, hidden attachments and link-preview payloads")
