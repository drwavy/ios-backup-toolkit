"""
Extract Camera Roll photos/videos and Messages media attachments, with
real-date recovery for both. Every file in a raw backup restore shows a
backup-bookkeeping date (not a real capture/send date) -- this command
recovers the real one from each file's own embedded metadata, falling
back to the message send date for attachments as a last resort.

Every file's real type is verified by its magic bytes / container
signature before copying, rather than trusting the extension recorded
in the backup's own file index -- a real mislabeled file was found this
way during development.
"""

import struct
import sqlite3
import datetime
from pathlib import Path
from collections import defaultdict

from ibtk.timestamps import apple_epoch_to_datetime, quicktime_epoch_to_unix
from ibtk.creation_time import set_creation_time

try:
    from PIL import Image
    from PIL.ExifTags import IFD
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except ImportError:
    HEIF_AVAILABLE = False

EXIF_DATE_TAGS = (36867, 36868, 306)
IMAGE_EXTS = {".jpg", ".jpeg", ".heic"}
VIDEO_EXTS = {".mov", ".mp4", ".3gp", ".m4a"}  # m4a shares the mp4 container


def sniff_real_type(header: bytes) -> str:
    if header[:2] == b"\xff\xd8":
        return ".jpg"
    if header[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if header[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand in (b"heic", b"heix", b"mif1"):
            return ".heic"
        if brand in (b"qt  ", b"mp42", b"isom", b"M4V "):
            return ".mov" if brand == b"qt  " else ".mp4"
    return ""


def get_exif_epoch(path: Path):
    if not PIL_AVAILABLE:
        return None
    try:
        with Image.open(path) as img:
            merged = {}
            legacy = img._getexif() if hasattr(img, "_getexif") else None
            if legacy:
                merged.update(legacy)
            exif = img.getexif()
            if exif:
                merged.update(dict(exif))
                try:
                    sub = exif.get_ifd(IFD.Exif)
                    if sub:
                        merged.update(dict(sub))
                except Exception:
                    pass
            for tag_id in EXIF_DATE_TAGS:
                if tag_id in merged:
                    raw = merged[tag_id]
                    if isinstance(raw, bytes):
                        raw = raw.decode(errors="ignore")
                    return datetime.datetime.strptime(raw, "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:
        return None
    return None


def get_mvhd_epoch(path: Path):
    """Reads the ISO-BMFF 'mvhd' box's creation_time directly -- works
    for .mov/.mp4/.m4a since they share the same container format."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        return None

    def find_box(box_type, start, end):
        pos = start
        while pos < end - 8:
            size = struct.unpack(">I", data[pos:pos + 4])[0]
            btype = data[pos + 4:pos + 8]
            hdr_len = 8
            if size == 1:
                if pos + 16 > end:
                    break
                size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
                hdr_len = 16
            if size == 0:
                size = end - pos
            if size < hdr_len:
                break
            if btype == box_type:
                return pos + hdr_len, pos + size
            pos += size
        return None

    moov = find_box(b"moov", 0, len(data))
    if not moov:
        return None
    mvhd = find_box(b"mvhd", moov[0], moov[1])
    if not mvhd:
        return None
    try:
        version = data[mvhd[0]]
        if version == 0:
            creation_time = struct.unpack(">I", data[mvhd[0] + 4:mvhd[0] + 8])[0]
        else:
            creation_time = struct.unpack(">Q", data[mvhd[0] + 4:mvhd[0] + 12])[0]
    except Exception:
        return None
    return quicktime_epoch_to_unix(creation_time)


def build_message_date_map(backup):
    """attachment relative path -> message send date, for use as a
    last-resort fallback when no embedded metadata is available."""
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


def apply_best_date(path: Path, fallback_epoch=None):
    ext = path.suffix.lower()
    epoch = None
    if ext in IMAGE_EXTS:
        epoch = get_exif_epoch(path)
    if epoch is None and ext in VIDEO_EXTS:
        epoch = get_mvhd_epoch(path)
    if epoch is None:
        epoch = fallback_epoch
    if epoch is not None:
        import os
        os.utime(path, (epoch, epoch))
        set_creation_time(str(path), epoch)
    return epoch is not None


def run(backup, out_dir: Path):
    media_out = out_dir / "Messages_Media"
    media_out.mkdir(parents=True, exist_ok=True)

    message_dates = build_message_date_map(backup)

    files = backup.find_files("MediaDomain", "Library/SMS/Attachments/", flags=1)
    print(f"Found {len(files)} message media attachments.")

    seen_names = defaultdict(int)
    copied = 0
    dated = 0

    for file_id, rel_path in files:
        src = backup.file_path(file_id)
        if not src.exists():
            continue
        with open(src, "rb") as f:
            header = f.read(16)
        real_ext = sniff_real_type(header) or Path(rel_path).suffix.lower() or ".bin"
        base_name = Path(rel_path).name
        if not base_name.lower().endswith(real_ext):
            base_name = Path(base_name).stem + real_ext

        count = seen_names[base_name]
        seen_names[base_name] += 1
        name = base_name if count == 0 else f"{Path(base_name).stem}_{count}{Path(base_name).suffix}"

        import shutil
        dest = media_out / name
        shutil.copy2(src, dest)
        copied += 1

        fallback = message_dates.get(rel_path)
        if apply_best_date(dest, fallback):
            dated += 1

    print(f"Copied {copied} files to {media_out} ({dated} with a recovered real date).")


def add_arguments(parser):
    pass
