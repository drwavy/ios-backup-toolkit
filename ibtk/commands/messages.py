"""
Extract Messages/iMessage/SMS history from an unencrypted iOS backup
into one readable .txt transcript per conversation.

Sender attribution is done PER-CHAT (via chat_handle_join, with a
message-level fallback through chat_message_join for handles missing
from the join table), not per-message. A per-message approach looks
reasonable but silently breaks: outgoing messages have handle_id = NULL,
so a naive per-message handle lookup falls back to a literal "unknown"
string, which then gets treated as if it were a real resolved contact --
dumping every conversation's outgoing messages into one shared
catch-all file. This was found and root-caused during development;
see the write-up in the repo root for the full story.
"""

import re
import csv
import sqlite3
from pathlib import Path

from ibtk.timestamps import apple_epoch_to_datetime


def normalize_phone(value: str) -> str:
    if not value:
        return ""
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def load_contact_map(backup):
    """Best-effort phone/email -> display name map, from AddressBook,
    if present in this backup. Returns {} if not available."""
    try:
        ab_path = backup.stage_known_db("addressbook.db")
    except FileNotFoundError:
        return {}
    con = sqlite3.connect(str(ab_path))
    cur = con.cursor()
    cur.execute("SELECT ROWID, First, Last, Organization FROM ABPerson")
    names = {}
    for rowid, first, last, org in cur.fetchall():
        name = " ".join(p for p in (first, last) if p) or org
        if name:
            names[rowid] = name
    contact_map = {}
    cur.execute(
        "SELECT record_id, property, value FROM ABMultiValue "
        "WHERE property IN (3, 4) AND value IS NOT NULL"
    )
    for record_id, prop, value in cur.fetchall():
        name = names.get(record_id)
        if not name:
            continue
        key = normalize_phone(value) if prop == 3 else value.strip().lower()
        if key:
            contact_map[key] = name
    con.close()
    return contact_map


def resolve_identifier(contact_map, raw: str) -> str:
    if not raw:
        return raw
    if raw == "unknown":
        return raw
    if "@" in raw:
        return contact_map.get(raw.strip().lower(), raw)
    return contact_map.get(normalize_phone(raw), raw)


def safe_filename(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|]', "_", name).strip() or "unnamed"


def run(backup, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    sms_path = backup.stage_known_db("sms.db")
    contact_map = load_contact_map(backup)

    con = sqlite3.connect(str(sms_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Per-chat participant list, and a name if the chat has one.
    cur.execute("SELECT ROWID, chat_identifier, display_name FROM chat")
    chats = {row["ROWID"]: dict(row) for row in cur.fetchall()}

    cur.execute(
        "SELECT chat_id, handle_id FROM chat_handle_join"
    )
    chat_handles = {}
    for row in cur.fetchall():
        chat_handles.setdefault(row["chat_id"], set()).add(row["handle_id"])

    cur.execute("SELECT ROWID, id FROM handle")
    handle_names = {row["ROWID"]: row["id"] for row in cur.fetchall()}

    # message -> chat, via chat_message_join (authoritative link; some
    # messages are missing from chat_handle_join but still show up here)
    cur.execute("SELECT chat_id, message_id FROM chat_message_join")
    message_chat = {row["message_id"]: row["chat_id"] for row in cur.fetchall()}

    cur.execute(
        "SELECT ROWID, date, is_from_me, handle_id, text FROM message "
        "WHERE text IS NOT NULL ORDER BY date"
    )

    conversations = {}  # chat_id -> list of (dt, sender, text)
    for row in cur.fetchall():
        chat_id = message_chat.get(row["ROWID"])
        if chat_id is None:
            continue
        dt = apple_epoch_to_datetime(row["date"])
        if row["is_from_me"]:
            sender = "Me"
        else:
            handle_id = row["handle_id"]
            raw_sender = handle_names.get(handle_id, "unknown")
            sender = resolve_identifier(contact_map, raw_sender)
        conversations.setdefault(chat_id, []).append((dt, sender, row["text"]))

    written = 0
    for chat_id, events in conversations.items():
        if not events:
            continue
        chat = chats.get(chat_id, {})
        display_name = chat.get("display_name") or chat.get("chat_identifier") or f"chat_{chat_id}"
        # prefer a resolved contact name for 1:1 chats with no set display name
        if not chat.get("display_name"):
            participants = chat_handles.get(chat_id, set())
            if len(participants) == 1:
                raw = handle_names.get(next(iter(participants)), display_name)
                display_name = resolve_identifier(contact_map, raw)

        fname = safe_filename(display_name) + ".txt"
        path = out_dir / fname
        n = 2
        while path.exists() and chat_id not in getattr(run, "_seen_paths", {}):
            path = out_dir / f"{safe_filename(display_name)}_{n}.txt"
            n += 1

        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Conversation: {display_name}\n{'=' * 60}\n\n")
            for dt, sender, text in events:
                ts = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "unknown time"
                f.write(f"[{ts}] {sender}: {text}\n")
        written += 1

    con.close()
    print(f"Wrote {written} conversation files to {out_dir}")


def add_arguments(parser):
    pass  # uses the shared --backup / --out arguments from the top-level CLI
