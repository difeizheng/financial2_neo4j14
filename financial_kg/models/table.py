from __future__ import annotations
from dataclasses import dataclass, field, fields
from typing import Optional


@dataclass
class Table:
    """Table node in the knowledge graph (Layer 3).

    Represents a logical table within a sheet (a sheet may contain multiple tables).
    """
    id: str                          # "TBL_{sheet}_{table_name}"
    name: str                        # e.g. "资金筹措及还本付息表"
    sheet: str
    table_type: str                  # "parameter" | "calculation" | "report"
    description: Optional[str] = None
    header_rows: list[int] = field(default_factory=list)
    data_row_range: list[int] = field(default_factory=list)  # [start_row, end_row]
    col_roles: dict = field(default_factory=dict)  # {col_letter: role}
    indicator_ids: list[str] = field(default_factory=list)
    # col_letter -> period label (for time_series columns)
    time_period_labels: dict = field(default_factory=dict)
    time_header_rows: int = 0  # number of header rows containing time period data
    # Populated after relationship inference
    feeds_into: list[str] = field(default_factory=list)    # table ids this feeds into
    fed_by: list[str] = field(default_factory=list)        # table ids that feed this

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "sheet": self.sheet,
            "table_type": self.table_type,
            "description": self.description,
            "header_rows": self.header_rows,
            "data_row_range": self.data_row_range,
            "col_roles": self.col_roles,
            "time_period_labels": self.time_period_labels,
            "time_header_rows": self.time_header_rows,
            "indicator_ids": self.indicator_ids,
            "feeds_into": self.feeds_into,
            "fed_by": self.fed_by,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Table":
        valid = {f.name for f in fields(cls)}
        d = {k: v for k, v in d.items() if k in valid}
        d.setdefault("time_period_labels", {})
        d.setdefault("time_header_rows", 0)
        return cls(**d)
