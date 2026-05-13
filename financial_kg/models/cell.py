from __future__ import annotations
from dataclasses import dataclass, field, fields
from typing import Any, Optional


@dataclass
class CellData:
    """Raw cell data extracted from Excel before graph construction."""
    sheet: str
    row: int
    col: str          # Excel column letter, e.g. "A", "BE"
    value: Any        # Computed value (data_only=True read)
    formula_raw: Optional[str]  # Raw formula string, e.g. "=SUM(F5:BE5)"
    data_type: str    # "number" | "string" | "date" | "bool" | "formula" | "empty"
    is_merged: bool = False
    merge_parent_id: Optional[str] = None  # id of the top-left cell in merge group
    merge_end_row: Optional[int] = None    # for top-left of merge: end row of merged range
    merge_end_col: Optional[str] = None    # for top-left of merge: end col of merged range
    number_format: Optional[str] = None    # Excel number format string, e.g. 'yyyy"年"m"月"'

    @property
    def id(self) -> str:
        return f"{self.sheet}_{self.row}_{self.col}"


@dataclass
class Cell:
    """Cell node in the knowledge graph (Layer 1)."""
    id: str                        # "{sheet}_{row}_{col}"
    sheet: str
    row: int
    col: str
    value: Any
    formula_raw: Optional[str]
    data_type: str
    is_header: bool = False
    is_merged: bool = False
    merge_parent_id: Optional[str] = None
    number_format: Optional[str] = None    # Excel number format string
    # Populated after graph construction
    dependencies: list[str] = field(default_factory=list)   # cell ids this cell depends on
    dependents: list[str] = field(default_factory=list)     # cell ids that depend on this cell
    # Populated after indicator/table detection
    indicator_id: Optional[str] = None
    table_id: Optional[str] = None
    time_period: Optional[str] = None  # e.g. "2024", "2024-01", "2024-01-15"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "sheet": self.sheet,
            "row": self.row,
            "col": self.col,
            "value": self.value,
            "formula_raw": self.formula_raw,
            "data_type": self.data_type,
            "is_header": self.is_header,
            "is_merged": self.is_merged,
            "merge_parent_id": self.merge_parent_id,
            "number_format": self.number_format,
            "dependencies": self.dependencies,
            "dependents": self.dependents,
            "indicator_id": self.indicator_id,
            "table_id": self.table_id,
            "time_period": self.time_period,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Cell":
        valid = {f.name for f in fields(cls)}
        d = {k: v for k, v in d.items() if k in valid}
        d.setdefault("number_format", None)
        d.setdefault("time_period", None)
        return cls(**d)
