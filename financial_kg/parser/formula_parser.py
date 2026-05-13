from __future__ import annotations
from typing import Optional

from openpyxl.formula import Tokenizer
from openpyxl.formula.tokenizer import Token

from .reference_resolver import normalize_ref


def extract_dependencies(formula: str, current_sheet: str) -> list[str]:
    """Extract all cell IDs that a formula depends on.

    Uses openpyxl's Tokenizer to identify RANGE operand tokens, then
    normalizes each reference to a canonical cell ID.

    Returns a deduplicated list of cell IDs.
    """
    if not formula or not formula.startswith("="):
        return []

    try:
        tok = Tokenizer(formula)
    except Exception:
        return []

    seen: set[str] = set()
    result: list[str] = []

    for token in tok.items:
        if token.type != Token.OPERAND or token.subtype != Token.RANGE:
            continue
        for cell_id in normalize_ref(token.value, current_sheet):
            if cell_id not in seen:
                seen.add(cell_id)
                result.append(cell_id)

    return result
