"""
Sets a file's macOS "Date Created" (birth time), which is not exposed
by Python's standard os.utime (that only sets mtime/atime). Uses the
native setattrlist() syscall via ctypes; falls back to an AppleScript
call through Finder if that's unavailable for some reason. No-ops
(returns False) on non-macOS systems.
"""

import sys
import ctypes
import subprocess
import datetime


def _build_native_setter():
    if sys.platform != "darwin":
        return None
    try:
        libc = ctypes.CDLL("libSystem.B.dylib")

        class _attrlist(ctypes.Structure):
            _fields_ = [
                ("bitmapcount", ctypes.c_uint16), ("reserved", ctypes.c_uint16),
                ("commonattr", ctypes.c_uint32), ("volattr", ctypes.c_uint32),
                ("dirattr", ctypes.c_uint32), ("fileattr", ctypes.c_uint32),
                ("forkattr", ctypes.c_uint32),
            ]

        class _timespec(ctypes.Structure):
            _fields_ = [("tv_sec", ctypes.c_int64), ("tv_nsec", ctypes.c_int64)]

        def setter(path: str, epoch: float) -> bool:
            al = _attrlist()
            al.bitmapcount = 5
            al.commonattr = 0x00000200  # ATTR_CMN_CRTIME
            ts = _timespec()
            ts.tv_sec = int(epoch)
            ts.tv_nsec = 0
            return libc.setattrlist(
                path.encode("utf-8"), ctypes.byref(al), ctypes.byref(ts), ctypes.sizeof(ts), 0
            ) == 0

        return setter
    except Exception:
        return None


_native_setter = _build_native_setter()


def set_creation_time(path: str, epoch: float) -> bool:
    """Sets the file's creation date to the given Unix epoch. Returns
    True on success, False if it couldn't be set (including on
    non-macOS platforms, where this is a no-op)."""
    if _native_setter is not None:
        if _native_setter(path, epoch):
            return True
    if sys.platform != "darwin":
        return False
    dt = datetime.datetime.fromtimestamp(epoch)
    script = (
        f'set d to current date\nset year of d to {dt.year}\n'
        f'set month of d to {dt.month}\nset day of d to {dt.day}\n'
        f'set time of d to {dt.hour * 3600 + dt.minute * 60 + dt.second}\n'
        f'tell application "Finder" to set creation date of (POSIX file "{path}") to d'
    )
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, check=True)
        return True
    except Exception:
        return False
