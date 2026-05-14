"""SQLite-backed task and snapshot registry."""
from __future__ import annotations
import json
import sqlite3
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tasks.db")


@dataclass
class TaskRecord:
    id: str
    filename: str
    status: str          # pending | running | done | error
    created_at: str
    cell_count: int = 0
    indicator_count: int = 0
    table_count: int = 0
    output_dir: str = ""
    error_msg: str = ""


@dataclass
class SnapshotRecord:
    id: str
    task_id: str
    name: str
    description: str
    created_at: str
    filepath: str


class TaskDB:
    def __init__(self, db_path: str = _DEFAULT_DB):
        self.db_path = os.path.abspath(db_path)
        self._init()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    cell_count INTEGER DEFAULT 0,
                    indicator_count INTEGER DEFAULT 0,
                    table_count INTEGER DEFAULT 0,
                    output_dir TEXT DEFAULT '',
                    error_msg TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    filepath TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(id)
                );
                CREATE TABLE IF NOT EXISTS qa_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    messages TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(id)
                );
                CREATE INDEX IF NOT EXISTS idx_qa_history_task ON qa_history(task_id);
                CREATE TABLE IF NOT EXISTS sensitivity_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    run_name TEXT NOT NULL,
                    params TEXT NOT NULL DEFAULT '[]',
                    perturbations TEXT NOT NULL DEFAULT '[]',
                    base_metrics TEXT NOT NULL DEFAULT '{}',
                    scenarios TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(id)
                );
                CREATE INDEX IF NOT EXISTS idx_sensitivity_task ON sensitivity_history(task_id);
            """)

    # ── Tasks ────────────────────────────────────────────────────────────────

    def create_task(self, task_id: str, filename: str, output_dir: str = "") -> TaskRecord:
        rec = TaskRecord(
            id=task_id,
            filename=filename,
            status="pending",
            created_at=datetime.now().isoformat(),
            output_dir=output_dir,
        )
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?,?,?,?)",
                (rec.id, rec.filename, rec.status, rec.created_at,
                 rec.cell_count, rec.indicator_count, rec.table_count,
                 rec.output_dir, rec.error_msg),
            )
        return rec

    def update_task(self, task_id: str, **kwargs) -> None:
        allowed = {"status", "cell_count", "indicator_count", "table_count",
                   "output_dir", "error_msg"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        set_clause = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE tasks SET {set_clause} WHERE id=?",
                (*fields.values(), task_id),
            )

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None

    def list_tasks(self) -> list[TaskRecord]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [_row_to_task(r) for r in rows]

    # ── Snapshots ────────────────────────────────────────────────────────────

    def save_snapshot(
        self,
        snap_id: str,
        task_id: str,
        name: str,
        filepath: str,
        description: str = "",
    ) -> SnapshotRecord:
        rec = SnapshotRecord(
            id=snap_id,
            task_id=task_id,
            name=name,
            description=description,
            created_at=datetime.now().isoformat(),
            filepath=filepath,
        )
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?)",
                (rec.id, rec.task_id, rec.name, rec.description,
                 rec.created_at, rec.filepath),
            )
        return rec

    def list_snapshots(self, task_id: str) -> list[SnapshotRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM snapshots WHERE task_id=? ORDER BY created_at DESC",
                (task_id,),
            ).fetchall()
        return [_row_to_snapshot(r) for r in rows]

    def get_snapshot(self, snap_id: str) -> Optional[SnapshotRecord]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM snapshots WHERE id=?", (snap_id,)).fetchone()
        return _row_to_snapshot(row) if row else None

    # ── QA History ────────────────────────────────────────────────────────────

    def save_qa_history(self, task_id: str, messages: list[dict]) -> None:
        now = datetime.now().isoformat()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM qa_history WHERE task_id=?", (task_id,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE qa_history SET messages=?, updated_at=? WHERE task_id=?",
                    (json.dumps(messages, ensure_ascii=False), now, task_id),
                )
            else:
                conn.execute(
                    "INSERT INTO qa_history (task_id, messages, created_at, updated_at) VALUES (?,?,?,?)",
                    (task_id, json.dumps(messages, ensure_ascii=False), now, now),
                )

    def load_qa_history(self, task_id: str) -> list[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT messages FROM qa_history WHERE task_id=?", (task_id,)
            ).fetchone()
        if row:
            try:
                return json.loads(row["messages"])
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    def clear_qa_history(self, task_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM qa_history WHERE task_id=?", (task_id,))

    # ── Sensitivity History ──────────────────────────────────────────────────

    def save_sensitivity(
        self,
        task_id: str,
        run_name: str,
        params: list[dict],
        perturbations: list[float],
        base_metrics: dict,
        scenarios: list[dict],
    ) -> int:
        now = datetime.now().isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO sensitivity_history (task_id, run_name, params, perturbations, base_metrics, scenarios, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (task_id, run_name, json.dumps(params, ensure_ascii=False),
                 json.dumps(perturbations), json.dumps(base_metrics, ensure_ascii=False),
                 json.dumps(scenarios, ensure_ascii=False), now),
            )
            return cur.lastrowid

    def list_sensitivity(self, task_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, run_name, params, perturbations, base_metrics, scenarios, created_at "
                "FROM sensitivity_history WHERE task_id=? ORDER BY created_at DESC",
                (task_id,),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "run_name": r["run_name"],
                "params": json.loads(r["params"]),
                "perturbations": json.loads(r["perturbations"]),
                "base_metrics": json.loads(r["base_metrics"]),
                "scenarios": json.loads(r["scenarios"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def load_sensitivity(self, record_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sensitivity_history WHERE id=?", (record_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "run_name": row["run_name"],
            "params": json.loads(row["params"]),
            "perturbations": json.loads(row["perturbations"]),
            "base_metrics": json.loads(row["base_metrics"]),
            "scenarios": json.loads(row["scenarios"]),
            "created_at": row["created_at"],
        }

    def delete_sensitivity(self, record_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM sensitivity_history WHERE id=?", (record_id,))

    # ── Delete ─────────────────────────────────────────────────────────────────

    def delete_task(self, task_id: str) -> Optional[TaskRecord]:
        """Delete a task, its snapshots, and return the task record for file cleanup.
        Returns None if the task doesn't exist."""
        task = self.get_task(task_id)
        if task is None:
            return None
        with self._conn() as conn:
            conn.execute("DELETE FROM snapshots WHERE task_id=?", (task_id,))
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        return task

    def list_snapshot_files(self, task_id: str) -> list[str]:
        """Return filepath for all snapshots of a task."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT filepath FROM snapshots WHERE task_id=?", (task_id,)
            ).fetchall()
        return [r[0] for r in rows]


def _row_to_task(row) -> TaskRecord:
    return TaskRecord(
        id=row["id"], filename=row["filename"], status=row["status"],
        created_at=row["created_at"], cell_count=row["cell_count"],
        indicator_count=row["indicator_count"], table_count=row["table_count"],
        output_dir=row["output_dir"], error_msg=row["error_msg"],
    )


def _row_to_snapshot(row) -> SnapshotRecord:
    return SnapshotRecord(
        id=row["id"], task_id=row["task_id"], name=row["name"],
        description=row["description"], created_at=row["created_at"],
        filepath=row["filepath"],
    )
