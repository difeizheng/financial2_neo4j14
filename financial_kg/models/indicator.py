from __future__ import annotations
from dataclasses import dataclass, field, fields
from typing import Any, Optional


@dataclass
class Indicator:
    """Indicator node in the knowledge graph (Layer 2).

    Represents a financial line item — a row in a financial table that has
    business meaning (e.g. '动态总投资（自主投资）').
    """
    id: str                          # "IND_{sheet}_{category}_{name}"
    name: str                        # e.g. "动态总投资（自主投资）"
    sheet: str
    row: int                         # primary data row in Excel
    category: Optional[str] = None   # e.g. "工程计划"
    subcategory: Optional[str] = None
    unit: Optional[str] = None       # e.g. "万元"
    description: Optional[str] = None  # LLM-generated business description
    summary_value: Any = None        # total / summary value (raw)
    display_value: Optional[str] = None  # formatted display string (e.g. "2023年12月")
    formula_readable: Optional[str] = None  # human-readable formula
    time_series: dict = field(default_factory=dict)  # {period_label: value}
    cell_ids: list[str] = field(default_factory=list)
    value_cell_id: Optional[str] = None  # the primary value cell
    table_id: Optional[str] = None
    # Populated after relationship inference
    depends_on_indicators: list[str] = field(default_factory=list)
    depended_by_indicators: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "sheet": self.sheet,
            "row": self.row,
            "category": self.category,
            "subcategory": self.subcategory,
            "unit": self.unit,
            "description": self.description,
            "summary_value": self.summary_value,
            "display_value": self.display_value,
            "formula_readable": self.formula_readable,
            "time_series": self.time_series,
            "cell_ids": self.cell_ids,
            "value_cell_id": self.value_cell_id,
            "table_id": self.table_id,
            "depends_on_indicators": self.depends_on_indicators,
            "depended_by_indicators": self.depended_by_indicators,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Indicator":
        valid = {f.name for f in fields(cls)}
        d = {k: v for k, v in d.items() if k in valid}
        d.setdefault("display_value", None)
        return cls(**d)
