"""Timestamp conversions used throughout the toolkit."""

import datetime

APPLE_EPOCH = datetime.datetime(2001, 1, 1)
QT_EPOCH_OFFSET = 2082844800  # seconds between 1904-01-01 and 1970-01-01


def apple_epoch_to_datetime(raw):
    """Converts an Apple Cocoa-epoch timestamp (seconds or nanoseconds
    since 2001-01-01, as used in sms.db, AddressBook, Calendar, Safari
    Bookmarks) to a Python datetime. iOS 11+ stores message dates in
    nanoseconds; older data and other databases use seconds -- detected
    by magnitude."""
    if raw is None:
        return None
    seconds = raw / 1_000_000_000 if raw > 1_000_000_000_000 else raw
    try:
        return APPLE_EPOCH + datetime.timedelta(seconds=seconds)
    except (OverflowError, OSError):
        return None


def quicktime_epoch_to_unix(creation_time: int):
    """Converts a QuickTime/ISO-BMFF mvhd creation_time (seconds since
    1904-01-01) to a Unix epoch. Used for .mov/.mp4/.m4a files."""
    if not creation_time:
        return None
    return creation_time - QT_EPOCH_OFFSET
