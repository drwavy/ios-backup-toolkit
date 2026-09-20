"""
Extract audio shared via Messages (music, beat demos, etc.) and actual
voice messages (.caf/.amr tap-to-talk recordings), with real-date
recovery for the shared-audio files using a priority chain:

  1. Filename, if it matches Voice Memos/GarageBand's own
     "YYYY-MM-DD--HH.MM.SS" auto-naming -- the real recording timestamp.
  2. ID3 recording-date tags for .mp3, but only with day-level precision
     -- a bare year tag is kept separate and used only as an absolute
     last resort, since defaulting it to Jan 1 00:00:00 would otherwise
     look like a real timestamp when it isn't one.
  3. The 'mvhd' box's creation_time for .m4a (see photos.py -- same
     container format as .mov/.mp4).
  4. The message's own send date from sms.db, as an approximate fallback.
"""

import re
import shutil
import sqlite3
import datetime
from pathlib import Path
from collections import defaultdict

from ibtk.timestamps import apple_epoch_to_datetime
from ibtk.creation_time import set_creation_time
from ibtk.commands.photos import get_mvhd_epoch, sniff_real_type

try:
    from mutagen.id3 import ID3, ID3NoHeaderError
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

MUSIC_EXTS = {".m4a", ".mp3"}
VOICE_EXTS = {".caf", ".amr"}

FILENAME_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})--(\d{2})\.(\d{2})\.(\d{2})")


def get_filename_epoch(path: Path):
    match = FILENAME_DATE_RE.search(path.name)
    if not match:
        return None
    year, month, day, hour, minute, second = (int(g) for g in match.groups())
    try:
        return datetime.datetime(year, month, day, hour, minute, second).timestamp()
    except ValueError:
        return None


def get_id3_epoch(path: Path, precise_only: bool):
    if not MUTAGEN_AVAILABLE:
        return None
    try:
        tags = ID3(str(path))
    except Exception:
        return None
    try:
        if "TDRC" in tags:
            raw = str(tags["TDRC"].text[0])
            if precise_only and len(raw) <= 4:
                return None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y-%m", "%Y"):
                try:
                    return datetime.datetime.strptime(raw, fmt).timestamp()
                except ValueError:
                    continue
        if "TYER" in tags:
            if precise_only and "TDAT" not in tags:
                return None
            year = str(tags["TYER"].text[0])
            month, day = "01", "01"
            if "TDAT" in tags:
                ddmm = str(tags["TDAT"].text[0])
                if len(ddmm) == 4:
                    day, month = ddmm[:2], ddmm[2:]
            return datetime.datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d").timestamp()
    except Exception:
        return None
    return None


def build_message_date_map(backup):
    try:
        sms_path = backup.stage_known_db("sms.db")
    except FileNotFoundError:
        return {}
    con = sqlite3.connect(str(sms_path))
    cur = con.cursor()
    cur.execute(
        "SELECT attachment.filename, message.date FROM attachment "
        "JOIN message_attachment_join ON attachment.ROWID = message_attachment_join.attachment_id "
        "JOIN message ON message.ROWID = message_attachment_join.message_id"
    )
    date_map = {}
    for raw_path, msg_date in cur.fetchall():
        idx = (raw_path or "").find("Library/SMS/Attachments/")
        if idx == -1:
            continue
        dt = apple_epoch_to_datetime(msg_date)
        if dt:
            date_map[raw_path[idx:]] = dt.timestamp()
    con.close()
    return date_map


def best_date_for_music_file(path: Path, fallback_epoch=None):
    epoch = get_filename_epoch(path)
    if epoch is None and path.suffix.lower() == ".mp3":
        epoch = get_id3_epoch(path, precise_only=True)
    if epoch is None and path.suffix.lower() == ".m4a":
        epoch = get_mvhd_epoch(path)
    if epoch is None:
        epoch = fallback_epoch
    if epoch is None and path.suffix.lower() == ".mp3":
        epoch = get_id3_epoch(path, precise_only=False)  # bare-year, absolute last resort
    return epoch


def run(backup, out_dir: Path):
    music_out = out_dir / "Shared_Music_Audio"
    voice_out = out_dir / "Voice_Messages"

    files = backup.find_files("MediaDomain", "Library/SMS/Attachments/", flags=1)
    music_files = [(fid, p) for fid, p in files if Path(p).suffix.lower() in MUSIC_EXTS]
    voice_files = [(fid, p) for fid, p in files if Path(p).suffix.lower() in VOICE_EXTS]
    print(f"Shared music/audio files: {len(music_files)}")
    print(f"Voice messages: {len(voice_files)}")

    message_dates = build_message_date_map(backup)

    music_out.mkdir(parents=True, exist_ok=True)
    seen = defaultdict(int)
    dated = 0
    for file_id, rel_path in music_files:
        src = backup.file_path(file_id)
        if not src.exists():
            continue
        name = Path(rel_path).name
        count = seen[name]
        seen[name] += 1
        if count:
            name = f"{Path(name).stem}_{count}{Path(name).suffix}"
        dest = music_out / name
        shutil.copy2(src, dest)
        epoch = best_date_for_music_file(dest, message_dates.get(rel_path))
        if epoch is not None:
            import os
            os.utime(dest, (epoch, epoch))
            set_creation_time(str(dest), epoch)
            dated += 1
    print(f"Copied {len(music_files)} shared audio files to {music_out} ({dated} with a recovered date).")

    voice_out.mkdir(parents=True, exist_ok=True)
    seen_voice = defaultdict(int)
    voice_events = []  # (dest_path, epoch, sender)
    for i, (file_id, rel_path) in enumerate(sorted(voice_files, key=lambda x: x[1]), start=1):
        src = backup.file_path(file_id)
        if not src.exists():
            continue
        ext = Path(rel_path).suffix.lower()
        name = f"{i:03d}_Voice_Message{ext}"
        dest = voice_out / name
        shutil.copy2(src, dest)
        epoch = message_dates.get(rel_path)
        if epoch is not None:
            import os
            os.utime(dest, (epoch, epoch))
            set_creation_time(str(dest), epoch)
    print(f"Copied {len(voice_files)} voice messages to {voice_out}.")


def add_arguments(parser):
    pass
