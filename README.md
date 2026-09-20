# iOS Backup Toolkit

Extract and recover data from your own unencrypted local iOS backup —
messages, contacts, calendar, bookmarks, camera roll photos/videos, and
audio shared through Messages — with real dates recovered instead of
the "everything says the same day" problem raw backup restores have.

This grew out of a real recovery project on an old iPhone XS backup.

## What this does and doesn't do

- **Works on unencrypted local backups only** — the kind iTunes/Finder
  creates when you *don't* check "Encrypt local backup." If your backup
  is encrypted, this can't read it (and won't try to help you bypass
  that).
- **Read-only against your backup.** Everything is copied out; nothing
  in the backup itself is ever modified.
- **This is for your own data.** Point it at a backup you have the
  right to access — your own phone, or one you're helping someone with
  their direct permission.
- Date-recovery (setting a file's real "Date Created") only works on
  macOS. Extraction itself works cross-platform.

## Why dates need recovering at all

A raw backup restore gives every single file the date it was *copied
during the restore*, not the date it was actually created, taken, or
sent. This toolkit recovers the real date from whatever source is
actually available for each file type — EXIF/XMP for photos, the `mvhd`
box for videos and `.m4a` audio, ID3 tags for `.mp3`, filename patterns
for Voice-Memos-style recordings, and the original message's send date
as a last resort for anything else.

## Installation

```bash
git clone https://github.com/<you>/ios-backup-toolkit.git
cd ios-backup-toolkit
pip install -e .
# optional, only needed for .heic photo date recovery:
pip install pillow-heif
```

## Finding your backup

```bash
ibtk list-backups
```

Lists every backup under the default location
(`~/Library/Application Support/MobileSync/Backup/`). Pass either the
backup ID it prints, or a full path to any backup directory, to every
other command with `--backup`.

## Usage

```bash
# everything at once
ibtk extract-all --backup <backup-id-or-path> --out ./recovered

# or one at a time
ibtk extract-messages  --backup <id> --out ./recovered/Messages
ibtk extract-contacts  --backup <id> --out ./recovered/Contacts
ibtk extract-calendar  --backup <id> --out ./recovered/Calendar
ibtk extract-bookmarks --backup <id> --out ./recovered/Bookmarks
ibtk extract-photos    --backup <id> --out ./recovered/Photos
ibtk extract-audio     --backup <id> --out ./recovered/Audio
```

- **extract-messages** — one `.txt` transcript per conversation, correct
  sender attribution for both sides of every conversation.
- **extract-contacts** — CSV + vCard.
- **extract-calendar** — CSV + ICS.
- **extract-bookmarks** — Netscape-format bookmarks HTML, importable
  into any browser. The Safari bookmarks schema varies somewhat by iOS
  version; this covers the common case but may need small adjustment
  for very old or very new backups — see the note in
  `ibtk/commands/bookmarks.py`.
- **extract-photos** — Camera Roll + Messages media, real dates
  recovered where possible, file type verified by magic bytes rather
  than trusting the backup's own recorded extension.
- **extract-audio** — splits shared music/audio from actual voice
  messages, with the date-recovery priority chain described above.

## Merging, comparing, and deduplicating

Four more commands for reconciling multiple sources — merging an old
backup's extraction with a newer one, comparing two extractions, or
cleaning up duplicates. See `docs/merging.md` for details and usage.

```bash
ibtk merge-messages  --primary <dir> --secondary <dir> --out <dir>
ibtk compare-messages --a <dir> --b <dir> [--report <file>]
ibtk merge-media  --sources <dir> [<dir> ...] --out <dir>
ibtk dedup-media  --dir <dir>
```

## How file lookup works

Every file in an iOS backup is stored under a hashed filename derived
from its (`domain`, `relativePath`) pair, recorded in the backup's own
`Manifest.db`. This toolkit looks that up **at runtime** for every
backup it's pointed at (`ibtk/backup.py`) — it never hardcodes a
specific hash, since those are unique to each individual backup file.

## Known limitations

- Bookmarks schema handling is less battle-tested than the rest — if it
  comes back empty on your backup, check the actual table schema in
  `Bookmarks.db` and adjust the query in `ibtk/commands/bookmarks.py`.
- Group-chat display names and some edge cases in very old (pre-iOS 11)
  `sms.db` timestamp formats aren't specifically handled.

## License

MIT — see `LICENSE`.
