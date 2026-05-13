from __future__ import annotations
import re
from typing import Optional
from openpyxl.utils import column_index_from_string, get_column_letter


# ── Reference normalization ──────────────────────────────────────────────────

# Strip $ signs from a cell address like $I$250 -> I250
_DOLLAR_RE = re.compile(r"\$")

# Match a cell address: optional $ + 1-3 uppercase letters + optional $ + 1-7 digits
_CELL_ADDR_RE = re.compile(r"\$?([A-Z]{1,3})\$?([0-9]{1,7})")


def _strip_dollars(addr: str) -> str:
    return _DOLLAR_RE.sub("", addr)


def _parse_cell_addr(addr: str) -> tuple[str, int]:
    """Parse 'A1' or '$A$1' into (col_letter, row_int)."""
    clean = _strip_dollars(addr)
    m = _CELL_ADDR_RE.match(clean)
    if not m:
        raise ValueError(f"Cannot parse cell address: {addr!r}")
    return m.group(1), int(m.group(2))


def expand_range(start_addr: str, end_addr: str) -> list[tuple[str, int]]:
    """Expand 'F5':'BE5' into list of (col, row) tuples.

    Caps at MAX_RANGE_CELLS to avoid memory explosion on huge ranges.
    """
    MAX_RANGE_CELLS = 2000
    start_col, start_row = _parse_cell_addr(start_addr)
    end_col, end_row = _parse_cell_addr(end_addr)

    start_col_idx = column_index_from_string(start_col)
    end_col_idx = column_index_from_string(end_col)

    cells = []
    for row in range(start_row, end_row + 1):
        for col_idx in range(start_col_idx, end_col_idx + 1):
            cells.append((get_column_letter(col_idx), row))
            if len(cells) >= MAX_RANGE_CELLS:
                return cells
    return cells


def normalize_ref(ref_str: str, current_sheet: str) -> list[str]:
    """Normalize an Excel reference token to a list of cell IDs.

    Handles:
      A1, $A$1, $A1, A$1          — local references
      Sheet1!A1, Sheet1!$A$1       — cross-sheet references
      'Sheet Name'!A1              — quoted sheet name
      A1:B10, $A$1:$B$10           — ranges
      Sheet1!A1:B10                — cross-sheet ranges
    """
    ref_str = ref_str.strip()

    # Split sheet name from cell part
    sheet = current_sheet
    cell_part = ref_str

    if "!" in ref_str:
        bang_idx = ref_str.index("!")
        raw_sheet = ref_str[:bang_idx]
        cell_part = ref_str[bang_idx + 1:]
        # Remove surrounding quotes if present
        if raw_sheet.startswith("'") and raw_sheet.endswith("'"):
            sheet = raw_sheet[1:-1]
        else:
            sheet = raw_sheet

    # Check for range
    if ":" in cell_part:
        parts = cell_part.split(":", 1)
        cells = expand_range(parts[0], parts[1])
    else:
        try:
            col, row = _parse_cell_addr(cell_part)
            cells = [(col, row)]
        except ValueError:
            return []

    return [f"{sheet}_{row}_{col}" for col, row in cells]
