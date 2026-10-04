"""Timestamp conversions used throughout the toolkit."""

import datetime

APPLE_EPOCH = datetime.datetime(2001, 1, 1)
QT_EPOCH_OFFSET = 2082844800  # seconds between 1904-01-01 and 1970-01-01
COCOA_UNIX_OFFSET = 978_307_200  # seconds between 1970-01-01 and 2001-01-01 (UTC)


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


def cocoa_to_unix(raw):
    """Converts a Cocoa-epoch timestamp (seconds or nanoseconds since
    2001-01-01 UTC, detected by magnitude) straight to a Unix epoch.

    Prefer this over apple_epoch_to_datetime(...).timestamp() whenever the
    result is used for file times: apple_epoch_to_datetime returns a NAIVE
    datetime holding UTC wall-clock values, and .timestamp() on a naive
    datetime reinterprets it as LOCAL time, shifting the result by the
    machine's UTC offset."""
    if not raw:
        return None
    seconds = raw / 1_000_000_000 if abs(raw) > 1_000_000_000_000 else raw
    return seconds + COCOA_UNIX_OFFSET
