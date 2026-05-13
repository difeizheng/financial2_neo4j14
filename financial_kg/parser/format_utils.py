"""Utilities for interpreting Excel number_format strings and formatting cell values."""
from __future__ import annotations
import re
from datetime import datetime
from typing import Any, Optional

# Patterns that indicate a date/time format (y=year, d=day; m alone is ambiguous but included)
_DATE_PATTERN = re.compile(r'[yYdD]|(?<![hH])[mM](?![sS])')
# Matches quoted literal text in number_format, e.g. "年" or "月"
_QUOTED_LITERAL = re.compile(r'"([^"]*)"')


def is_date_format(number_format: Optional[str]) -> bool:
    """Return True if the number_format string represents a date/time format."""
    if not number_format or number_format in ("General", "@", ""):
        return False
    stripped = _QUOTED_LITERAL.sub("", number_format)
    return bool(_DATE_PATTERN.search(stripped))


def serial_to_datetime(serial: float) -> datetime:
    """Convert Excel date serial number to Python datetime."""
    return datetime.fromordinal(datetime(1899, 12, 30).toordinal() + int(serial))


def serial_to_label(serial: float) -> str:
    """Convert Excel date serial to a YYYY-MM label (no number_format needed)."""
    try:
        dt = serial_to_datetime(serial)
        return dt.strftime("%Y-%m")
    except Exception:
        return str(int(serial))


def _format_date_by_pattern(dt: datetime, number_format: str) -> str:
    """Convert a datetime to a display string based on Excel number_format."""
    result = []
    i = 0
    fmt = number_format
    while i < len(fmt):
        ch = fmt[i]
        if ch == '"':
            j = fmt.find('"', i + 1)
            if j == -1:
                result.append(fmt[i + 1:])
                break
            result.append(fmt[i + 1:j])
            i = j + 1
            continue
        if fmt[i:i+4].lower() == 'yyyy':
            result.append(dt.strftime('%Y'))
            i += 4
            continue
        if fmt[i:i+2].lower() == 'yy':
            result.append(dt.strftime('%y'))
            i += 2
            continue
        if fmt[i:i+2].lower() == 'mm':
            result.append(dt.strftime('%m'))
            i += 2
            continue
        if fmt[i].lower() == 'm':
            result.append(str(dt.month))
            i += 1
            continue
        if fmt[i:i+2].lower() == 'dd':
            result.append(dt.strftime('%d'))
            i += 2
            continue
        if fmt[i].lower() == 'd':
            result.append(str(dt.day))
            i += 1
            continue
        if fmt[i:i+2].lower() == 'hh':
            result.append(dt.strftime('%H'))
            i += 2
            continue
        if fmt[i].lower() == 'h':
            result.append(str(dt.hour))
            i += 1
            continue
        if fmt[i:i+2].lower() == 'ss':
            result.append(dt.strftime('%S'))
            i += 2
            continue
        result.append(ch)
        i += 1
    return ''.join(result)


def format_cell_value(value: Any, number_format: Optional[str]) -> Optional[str]:
    """Format a raw cell value using its Excel number_format.

    Returns a display string, or None if no special formatting applies.
    """
    if not number_format or number_format in ("General", "@", ""):
        return None
    if value is None:
        return None

    if is_date_format(number_format) and isinstance(value, (int, float)):
        try:
            dt = serial_to_datetime(float(value))
            return _format_date_by_pattern(dt, number_format)
        except Exception:
            return None

    return None
