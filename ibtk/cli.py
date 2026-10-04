"""
Command-line entry point for the iOS Backup Toolkit.

    ibtk list-backups
    ibtk extract-messages  --backup <id-or-path> --out <dir>
    ibtk extract-contacts  --backup <id-or-path> --out <dir>
    ibtk extract-calendar  --backup <id-or-path> --out <dir>
    ibtk extract-bookmarks --backup <id-or-path> --out <dir>
    ibtk extract-photos    --backup <id-or-path> --out <dir>
    ibtk extract-audio     --backup <id-or-path> --out <dir>
    ibtk extract-all       --backup <id-or-path> --out <dir>

    ibtk extract-macos-messages --messages-dir <~/Library/Messages copy> --out <dir>
                                [--contacts-vcf <file>] [--local-time] [--include-hidden]

    ibtk merge-messages  --primary <dir> --secondary <dir> --out <dir>
    ibtk compare-messages --a <dir> --b <dir> [--report <file>]
    ibtk merge-media  --sources <dir> [<dir> ...] --out <dir>
    ibtk dedup-media  --dir <dir>

`--backup` accepts either a full path to a backup directory, or just the
backup's ID (the 40-char folder name) if it lives under the default
MobileSync location -- run `ibtk list-backups` to see what's available.

The merge/compare/dedup commands work on the OUTPUT of the extract
commands (or anything in the same format) -- they don't touch a backup
directly. See docs/merging.md for when and why you'd use each one.
"""

import argparse
import sys
from pathlib import Path

from ibtk.backup import Backup, DEFAULT_BACKUP_ROOT, list_backups
from ibtk.commands import messages, contacts, calendar, bookmarks, photos, audio
from ibtk.commands import merge_messages, compare_messages, merge_media, dedup_media
from ibtk.commands import macos_messages


COMMANDS = {
    "extract-messages": (messages, "Messages"),
    "extract-contacts": (contacts, "Contacts"),
    "extract-calendar": (calendar, "Calendar"),
    "extract-bookmarks": (bookmarks, "Bookmarks"),
    "extract-photos": (photos, "Photos_Videos"),
    "extract-audio": (audio, "Audio"),
}


def resolve_backup_path(backup_arg: str) -> Path:
    candidate = Path(backup_arg)
    if candidate.exists() and (candidate / "Manifest.db").exists():
        return candidate
    default_candidate = DEFAULT_BACKUP_ROOT / backup_arg
    if (default_candidate / "Manifest.db").exists():
        return default_candidate
    print(f"Could not find a backup at '{backup_arg}'. Run 'ibtk list-backups' to see available backups.")
    sys.exit(1)


def cmd_list_backups(args):
    backups = list_backups()
    if not backups:
        print(f"No backups found under {DEFAULT_BACKUP_ROOT}")
        return
    print(f"Backups found under {DEFAULT_BACKUP_ROOT}:\n")
    for backup_id, path in backups:
        print(f"  {backup_id}")


def cmd_extract(args, module):
    backup_path = resolve_backup_path(args.backup)
    out_dir = Path(args.out)
    with Backup(backup_path) as b:
        module.run(b, out_dir)


def cmd_extract_all(args):
    backup_path = resolve_backup_path(args.backup)
    out_root = Path(args.out)
    with Backup(backup_path) as b:
        for name, (module, subdir) in COMMANDS.items():
            print(f"\n=== {name} ===")
            try:
                module.run(b, out_root / subdir)
            except FileNotFoundError as ex:
                print(f"  Skipped: {ex}")


def cmd_extract_macos_messages(args):
    macos_messages.run(
        Path(args.messages_dir),
        Path(args.out),
        contacts_vcf=args.contacts_vcf,
        local_time=args.local_time,
        include_hidden=args.include_hidden,
    )


def cmd_merge_messages(args):
    merge_messages.run(Path(args.primary), Path(args.secondary), Path(args.out))


def cmd_compare_messages(args):
    report_path = Path(args.report) if args.report else None
    compare_messages.run(Path(args.a), Path(args.b), report_path)


def cmd_merge_media(args):
    merge_media.run(args.sources, Path(args.out))


def cmd_dedup_media(args):
    dedup_media.run(Path(args.dir))


def build_parser():
    parser = argparse.ArgumentParser(prog="ibtk", description="iOS Backup Toolkit")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list-backups", help="List backups found in the default MobileSync location")
    p.set_defaults(func=cmd_list_backups)

    for name, (module, _) in COMMANDS.items():
        p = sub.add_parser(name, help=f"Extract {name.replace('extract-', '')}")
        p.add_argument("--backup", required=True, help="Backup ID or full path to a backup directory")
        p.add_argument("--out", required=True, help="Output directory")
        p.set_defaults(func=lambda args, module=module: cmd_extract(args, module))

    p = sub.add_parser("extract-all", help="Run every extraction command")
    p.add_argument("--backup", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_extract_all)

    # Not part of extract-all: it reads a plain Messages folder, not a backup.
    p = sub.add_parser(
        "extract-macos-messages",
        help="Extract a macOS Messages folder (chat.db + Attachments) into transcripts and dated media",
    )
    macos_messages.add_arguments(p)
    p.set_defaults(func=cmd_extract_macos_messages)

    p = sub.add_parser(
        "merge-messages",
        help="Merge two ibtk-format message-transcript directories (e.g. old backup + new backup), deduplicated by content",
    )
    p.add_argument("--primary", required=True, help="Primary transcript directory (its content takes priority on any ordering)")
    p.add_argument("--secondary", required=True, help="Secondary transcript directory")
    p.add_argument("--out", required=True, help="Output directory for the merged transcripts")
    p.set_defaults(func=cmd_merge_messages)

    p = sub.add_parser(
        "compare-messages",
        help="Report content differences between two ibtk-format message-transcript directories",
    )
    p.add_argument("--a", required=True, help="First transcript directory")
    p.add_argument("--b", required=True, help="Second transcript directory")
    p.add_argument("--report", help="Optional path to write the full report to")
    p.set_defaults(func=cmd_compare_messages)

    p = sub.add_parser(
        "merge-media",
        help="Merge two or more media directories into one deduplicated, categorized output",
    )
    p.add_argument("--sources", required=True, nargs="+", help="One or more source directories")
    p.add_argument("--out", required=True, help="Output directory")
    p.set_defaults(func=cmd_merge_media)

    p = sub.add_parser(
        "dedup-media",
        help="Find content-duplicate files within one directory and quarantine (not delete) the redundant copies",
    )
    p.add_argument("--dir", required=True, help="Directory to de-duplicate")
    p.set_defaults(func=cmd_dedup_media)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
