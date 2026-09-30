"""Durable work states and beginner manuals, separate from legacy reports."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterator
from uuid import uuid4

from .models import utc_now


class WorkbenchStore:
    """Use short transactions so background work never holds a database lock."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "workbench.db"
        self.documents = self.directory / "manuals"
        self._export_lock = threading.RLock()
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                    progress INTEGER NOT NULL, message TEXT NOT NULL,
                    result TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS manuals (
                    id TEXT PRIMARY KEY, revision_id TEXT NOT NULL, project_name TEXT NOT NULL,
                    markdown TEXT NOT NULL, character_count INTEGER NOT NULL,
                    auto_exported INTEGER NOT NULL, mode TEXT NOT NULL,
                    usage TEXT NOT NULL, limitations TEXT NOT NULL, created_at TEXT NOT NULL,
                    export_status TEXT NOT NULL DEFAULT 'not_required',
                    export_error TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS manuals_revision ON manuals(revision_id, created_at);
                CREATE TABLE IF NOT EXISTS usage_receipts (
                    job_id TEXT PRIMARY KEY, status TEXT NOT NULL, usage TEXT NOT NULL, updated_at TEXT NOT NULL);
            """)
            # Additive migration preserves existing manuals and their immutable text.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(manuals)")}
            if not {"export_status", "export_error"}.issubset(columns):
                db.execute("BEGIN")
            if "export_status" not in columns:
                db.execute("ALTER TABLE manuals ADD COLUMN export_status TEXT NOT NULL DEFAULT 'not_required'")
                db.execute("UPDATE manuals SET export_status=CASE WHEN auto_exported=1 THEN 'saved' "
                           "WHEN character_count>5000 THEN 'failed' ELSE 'not_required' END")
            if "export_error" not in columns:
                db.execute("ALTER TABLE manuals ADD COLUMN export_error TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE manuals SET export_error=? WHERE export_status='failed'",
                           ("正文已保存，自动导出尚未完成；可下载正文或重试导出。",))

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Always close SQLite connections, including failed transactions."""
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover_jobs(self) -> None:
        """Mark interrupted work honestly; never silently repeat charged calls."""
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='interrupted',message=?,error=?,updated_at=? "
                       "WHERE status IN ('queued','running')",
                       ("上次运行已中断，请检查结果后手动重新开始。",
                        "如果中断发生在模型请求期间，请先核对平台用量。", utc_now()))

    def create_job(self, kind: str) -> str:
        """Queue one durable task and return an opaque ID."""
        identifier, now = uuid4().hex, utc_now()
        with self.connect() as db:
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
                       (identifier, kind, "queued", 0, "等待处理", None, None, now, now))
        return identifier

    def update_job(self, identifier: str, status: str, progress: int, message: str,
                   result: dict | None = None, error: str | None = None) -> None:
        """Persist a bounded, safe state update without source or credentials."""
        if status not in {"queued", "running", "succeeded", "failed", "interrupted"}:
            raise ValueError("未知任务状态。")
        with self.connect() as db:
            db.execute("UPDATE jobs SET status=?,progress=?,message=?,result=?,error=?,updated_at=? WHERE id=?",
                       (status, min(100, max(0, progress)), message,
                        json.dumps(result, ensure_ascii=False) if result is not None else None,
                        error, utc_now(), identifier))

    def get_job(self, identifier: str) -> dict:
        """Load one task for UI polling."""
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
            receipt = db.execute("SELECT status,usage FROM usage_receipts WHERE job_id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("任务不存在。")
        result = dict(row)
        result["result"] = json.loads(result["result"]) if result["result"] else None
        result["usage"] = json.loads(receipt["usage"]) if receipt else None
        result["request_status"] = receipt["status"] if receipt else "not_sent"
        return result

    def record_usage(self, identifier: str, status: str, usage: dict) -> None:
        """Preserve billing facts independently of Markdown rendering or export."""
        with self.connect() as db:
            db.execute("INSERT INTO usage_receipts VALUES(?,?,?,?) ON CONFLICT(job_id) DO UPDATE "
                       "SET status=excluded.status,usage=excluded.usage,updated_at=excluded.updated_at",
                       (identifier, status, json.dumps(usage, ensure_ascii=False), utc_now()))

    def list_jobs(self, limit: int = 50) -> list[dict]:
        """Expose bounded durable task history, including interrupted call receipts."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("任务查询数量必须为 1 到 100。")
        with self.connect() as db:
            rows = db.execute(
                "SELECT j.*,r.usage AS receipt_usage,r.status AS request_status FROM jobs j "
                "LEFT JOIN usage_receipts r ON r.job_id=j.id ORDER BY j.created_at DESC,j.id DESC LIMIT ?",
                (limit,)).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["result"] = json.loads(item["result"]) if item["result"] else None
            usage = item.pop("receipt_usage")
            item["usage"] = json.loads(usage) if usage else None
            item["request_status"] = item["request_status"] or "not_sent"
            results.append(item)
        return results

    def save_manual(self, revision_id: str, project_name: str, markdown: str,
                    mode: str, usage: dict, limitations: list[str]) -> dict:
        """Commit the complete report before attempting an optional file export."""
        from .manual import count_characters
        if not markdown.strip() or len(markdown) > 2_000_000:
            raise ValueError("说明书为空或超过 200 万字符限制，请分模块生成。")
        identifier = uuid4().hex
        count = count_characters(markdown)
        automatic = count > 5000
        with self.connect() as db:
            db.execute("INSERT INTO manuals (id,revision_id,project_name,markdown,character_count,"
                       "auto_exported,mode,usage,limitations,created_at,export_status,export_error) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (identifier, revision_id, project_name, markdown, count, 0,
                        mode, json.dumps(usage, ensure_ascii=False),
                        json.dumps(limitations, ensure_ascii=False), utc_now(),
                        "failed" if automatic else "not_required",
                        "正文已保存，自动导出尚未完成；可下载正文或重试导出。" if automatic else ""))
        if automatic:
            return self.export_manual(identifier)
        return self.get_manual(identifier)

    def export_manual(self, identifier: str) -> dict:
        """Retry only the Markdown write from SQLite; never call a model or rerender."""
        with self._export_lock:
            return self._export_manual(identifier)

    def _export_manual(self, identifier: str) -> dict:
        """Serialize file writes so an older failed attempt cannot hide a newer success."""
        manual = self.get_manual(identifier)
        if manual["character_count"] <= 5000:
            return manual
        target = self.document_path(identifier)
        temporary = target.with_suffix(f".{uuid4().hex}.tmp")
        status, error = "saved", ""
        try:
            self.documents.mkdir(parents=True, exist_ok=True)
            temporary.write_text(manual["markdown"], encoding="utf-8")
            temporary.replace(target)
        except (OSError, UnicodeError):
            # File-system error messages may contain private paths; store a safe explanation.
            status = "failed"
            error = "自动导出失败，正文和用量已保存在数据库；请检查文件占用、目录权限或磁盘空间后重试，也可直接下载。"
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Cleanup failure must not discard an already committed report.
        with self.connect() as db:
            db.execute("UPDATE manuals SET auto_exported=?,export_status=?,export_error=? WHERE id=?",
                       (int(status == "saved"), status, error, identifier))
        return self.get_manual(identifier)

    def document_path(self, identifier: str) -> Path:
        """Only generated hex identifiers may become filenames."""
        if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise ValueError("无效的说明书编号。")
        return self.documents / f"代码说明书_{identifier}.md"

    def get_manual(self, identifier: str) -> dict[str, Any]:
        """Return stored Markdown together with its real generation metadata."""
        self.document_path(identifier)
        with self.connect() as db:
            row = db.execute("SELECT * FROM manuals WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("说明书不存在。")
        result = dict(row)
        result["usage"] = json.loads(result["usage"])
        result["limitations"] = json.loads(result["limitations"])
        result["auto_exported"] = bool(result["auto_exported"])
        result["download_url"] = f"/api/manuals/{identifier}/download"
        result["document_path"] = str(self.document_path(identifier)) if result["auto_exported"] else None
        return result

    def list_manuals(self, revision_id: str) -> list[dict]:
        """List recent manuals without sending the full prose repeatedly."""
        with self.connect() as db:
            rows = db.execute("SELECT id,revision_id,project_name,character_count,auto_exported,mode,created_at,"
                              "export_status,export_error "
                              "FROM manuals WHERE revision_id=? ORDER BY created_at DESC LIMIT 100",
                              (revision_id,)).fetchall()
        return [dict(row) for row in rows]

    def delete_revision_manuals(self, revision_ids: list[str]) -> None:
        """Remove only files belonging to explicitly deleted project revisions."""
        with self.connect() as db:
            ids = [row[0] for revision in revision_ids
                   for row in db.execute("SELECT id FROM manuals WHERE revision_id=?", (revision,))]
        try:
            for identifier in ids:
                self.document_path(identifier).unlink(missing_ok=True)
        except OSError:
            # Keep database identities so a locked Windows file can be retried.
            raise ValueError("说明书文件正在被其他程序占用，项目尚未删除；请关闭文档后重试。") from None
        with self.connect() as db:
            db.executemany("DELETE FROM manuals WHERE revision_id=?", [(revision,) for revision in revision_ids])
