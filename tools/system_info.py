"""
tools/system_info.py — Cross-platform system time and timezone info.

Tools:
  get_current_time  — returns date/time/weekday/unix timestamp via datetime.datetime.now()
  get_timezone      — returns the local system timezone name

Design intent:
  All time retrieval uses Python's standard library ONLY.
  NO shell commands, NO powershell, NO subprocess calls.
  This was the root cause of the ParameterBindingException / timeout failures
  that occurred when the agent tried to get time via run_cmd / run_powershell.

Both functions are async-wrapped via asyncio.to_thread() for consistency
with the rest of the phantom-mcp tool layer.
"""
from __future__ import annotations

import asyncio
import datetime


async def get_current_time() -> dict:
    """
    Return the current local date and time using Python's datetime module.
    Never calls the OS shell — fully cross-platform (Windows, Linux, macOS).

    Returns a dict with:
      datetime    — ISO 8601 string, e.g. '2026-05-12T19:00:00.123456'
      date        — e.g. '2026-05-12'
      time        — e.g. '19:00:00'
      time_short  — e.g. '7:00 PM'
      weekday     — e.g. 'Tuesday'
      timestamp_unix — float, seconds since epoch
    """
    def _get() -> dict:
        now = datetime.datetime.now()
        return {
            "datetime":      now.isoformat(),
            "date":          now.strftime("%Y-%m-%d"),
            "time":          now.strftime("%H:%M:%S"),
            "time_short":    now.strftime("%I:%M %p").lstrip("0"),
            "weekday":       now.strftime("%A"),
            "timestamp_unix": now.timestamp(),
        }
    return await asyncio.to_thread(_get)


async def get_timezone() -> dict:
    """
    Return the local system timezone name.
    Tries tzlocal (preferred, pip install tzlocal) first for a proper IANA name.
    Falls back to Python's datetime.timezone.utc offset string if unavailable.

    Returns a dict with:
      timezone_name — e.g. 'America/New_York' or 'UTC-05:00'
      utc_offset    — e.g. '-05:00'
      source        — 'tzlocal' or 'datetime_fallback'
    """
    def _get() -> dict:
        # Try tzlocal for a real IANA timezone name
        try:
            from tzlocal import get_localzone
            tz = get_localzone()
            now = datetime.datetime.now(tz=tz)
            utc_offset = now.strftime("%z")  # e.g. '-0500'
            # Format as '-05:00'
            if len(utc_offset) == 5:
                utc_offset = utc_offset[:3] + ":" + utc_offset[3:]
            return {
                "timezone_name": str(tz),
                "utc_offset":    utc_offset,
                "source":        "tzlocal",
            }
        except ImportError:
            pass

        # Fallback: use datetime.timezone aware offset
        now = datetime.datetime.now(datetime.timezone.utc).astimezone()
        utc_offset = now.strftime("%z")
        if len(utc_offset) == 5:
            utc_offset = utc_offset[:3] + ":" + utc_offset[3:]
        tz_name = now.tzname() or "Unknown"
        return {
            "timezone_name": tz_name,
            "utc_offset":    utc_offset,
            "source":        "datetime_fallback",
            "note":          "Install tzlocal for full IANA timezone names: pip install tzlocal",
        }
    return await asyncio.to_thread(_get)
