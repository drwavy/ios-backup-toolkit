"""
Core access layer for an unencrypted iOS (iTunes-style) local backup.

The single most important thing this module does: it looks up files by
their (domain, relativePath) via Manifest.db at RUNTIME, rather than
hardcoding the resulting fileID hash. Every backup has different hashes
for the same logical file (e.g. Library/SMS/sms.db) -- the hash is a
SHA-1 of the domain+path, and is stable ACROSS backups of the SAME
device/path combination, but is not something you can hardcode once and
reuse for someone else's backup.
"""

import shutil
import sqlite3
from pathlib import Path


DEFAULT_BACKUP_ROOT = Path.home() / "Library" / "Application Support" / "MobileSync" / "Backup"

# Well-known (domain, relativePath) pairs for the files this toolkit reads.
# These are stable across iOS versions for unencrypted local backups.
KNOWN_FILES = {
    "sms.db": ("HomeDomain", "Library/SMS/sms.db"),
    "addressbook.db": ("HomeDomain", "Library/AddressBook/AddressBook.sqlitedb"),
    "calendar.db": ("HomeDomain", "Library/Calendar/Calendar.sqlitedb"),
    "bookmarks.db": ("HomeDomain", "Library/Safari/Bookmarks.db"),
    "photos.db": ("CameraRollDomain", "Media/PhotoData/Photos.sqlite"),
}


def list_backups(root: Path = DEFAULT_BACKUP_ROOT):
    """Returns [(backup_id, path, display_info)] for every backup found
    under the default MobileSync location. Each backup is a directory
    named by a 40-char hex UDID hash, containing its own Manifest.db."""
    if not root.exists():
        return []
    results = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and (entry / "Manifest.db").exists():
            results.append((entry.name, entry))
    return results


class Backup:
    """Represents one unencrypted local iOS backup. Provides file lookup
    by domain+relativePath (the only reliable way to find a file across
    different backups) and staging (copying a file + its -wal/-shm
    siblings to a working directory, since SQLite files in the backup
    are read-only and may have pending WAL data)."""

    def __init__(self, backup_dir: Path, work_dir: Path = None):
        self.backup_dir = Path(backup_dir)
        self.manifest_path = self.backup_dir / "Manifest.db"
        if not self.manifest_path.exists():
            raise FileNotFoundError(
                f"No Manifest.db found at {self.manifest_path}. "
                f"Is this an unencrypted local backup?"
            )
        self.work_dir = Path(work_dir) if work_dir else Path("/tmp/ibtk_work") / self.backup_dir.name
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_con = sqlite3.connect(str(self.manifest_path))

    def find_file_id(self, domain: str, relative_path: str):
        """Looks up a single file's fileID (the hash used as its
        on-disk filename in the backup) by its logical domain+path.
        Returns None if not present in this backup (e.g. the user never
        used that feature, or it's an encrypted backup)."""
        cur = self._manifest_con.cursor()
        cur.execute(
            "SELECT fileID FROM Files WHERE domain = ? AND relativePath = ?",
            (domain, relative_path),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def find_files(self, domain: str, relative_path_prefix: str, flags: int = None):
        """Looks up all files under a domain whose relativePath starts
        with the given prefix (e.g. every attachment under
        'Library/SMS/Attachments/'). Returns [(fileID, relativePath)]."""
        cur = self._manifest_con.cursor()
        if flags is not None:
            cur.execute(
                "SELECT fileID, relativePath FROM Files "
                "WHERE domain = ? AND relativePath LIKE ? AND flags = ?",
                (domain, f"{relative_path_prefix}%", flags),
            )
        else:
            cur.execute(
                "SELECT fileID, relativePath FROM Files "
                "WHERE domain = ? AND relativePath LIKE ?",
                (domain, f"{relative_path_prefix}%"),
            )
        return cur.fetchall()

    def file_path(self, file_id: str) -> Path:
        """The actual on-disk location of a file, given its fileID."""
        return self.backup_dir / file_id[:2] / file_id

    def stage_known_db(self, key: str, dest_name: str = None) -> Path:
        """Looks up one of the well-known databases (see KNOWN_FILES),
        copies it (plus any -wal/-shm siblings) into work_dir, and
        returns the path to the copy. Raises KeyError for an unknown
        key, FileNotFoundError if this backup doesn't have that file."""
        domain, rel_path = KNOWN_FILES[key]
        file_id = self.find_file_id(domain, rel_path)
        if file_id is None:
            raise FileNotFoundError(
                f"{key} ({domain}:{rel_path}) not found in this backup. "
                f"It may be an encrypted backup, or the feature was never used."
            )
        return self.stage_file(file_id, dest_name or key)

    def stage_file(self, file_id: str, dest_name: str) -> Path:
        """Copies one backup-stored file (by its fileID) plus any
        -wal/-shm siblings into work_dir under dest_name."""
        src = self.file_path(file_id)
        if not src.exists():
            raise FileNotFoundError(f"Backup file missing on disk: {src}")
        dest = self.work_dir / dest_name
        shutil.copy2(src, dest)
        for suffix in ("-wal", "-shm"):
            extra_src = self.backup_dir / file_id[:2] / f"{file_id}{suffix}"
            if extra_src.exists():
                shutil.copy2(extra_src, self.work_dir / f"{dest_name}{suffix}")
        return dest

    def close(self):
        self._manifest_con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
