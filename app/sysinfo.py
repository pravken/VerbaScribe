"""
Processor and memory load of the whole computer, for the meters next to the GPU badge.

No extra package: on Windows the numbers come from GetSystemTimes and GlobalMemoryStatusEx
(kernel32), elsewhere from /proc. The CPU load is measured between two calls, so the first call
after start returns None for it.
"""

import ctypes
import sys
import threading
from typing import Any, Dict, Optional, Tuple

_lock = threading.Lock()
_last_times: Optional[Tuple[int, int]] = None  # (idle, total) at the previous call


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _MemStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def _cpu_times() -> Optional[Tuple[int, int]]:
    """(idle, total) processor time since boot, in arbitrary units."""
    try:
        if sys.platform == "win32":
            idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                return None
            val = lambda ft: (ft.high << 32) | ft.low  # noqa: E731
            # Kernel time includes the idle time
            return val(idle), val(kernel) + val(user)
        with open("/proc/stat", encoding="ascii") as f:
            parts = [int(x) for x in f.readline().split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        return idle, sum(parts)
    except Exception:
        return None


def cpu_percent() -> Optional[float]:
    global _last_times
    now = _cpu_times()
    if now is None:
        return None
    with _lock:
        prev, _last_times = _last_times, now
    if prev is None:
        return None
    d_idle, d_total = now[0] - prev[0], now[1] - prev[1]
    if d_total <= 0:
        return None
    return round(max(0.0, min(100.0, 100.0 * (1 - d_idle / d_total))), 0)


def memory() -> Optional[Dict[str, float]]:
    gb = 2**30
    try:
        if sys.platform == "win32":
            st = _MemStatus()
            st.dwLength = ctypes.sizeof(_MemStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return None
            total, avail = st.ullTotalPhys, st.ullAvailPhys
        else:
            info = {}
            with open("/proc/meminfo", encoding="ascii") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    info[k] = int(v.split()[0]) * 1024
            total, avail = info["MemTotal"], info.get("MemAvailable", info.get("MemFree", 0))
    except Exception:
        return None
    return {"total_gb": round(total / gb, 1), "used_gb": round((total - avail) / gb, 1), "free_gb": round(avail / gb, 1)}


def snapshot() -> Dict[str, Any]:
    return {"cpu_percent": cpu_percent(), "ram": memory()}
