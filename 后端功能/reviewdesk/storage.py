"""Windows Credential Manager and a source-minimizing local report database."""

import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3

from .models import Finding, ReviewResult, Usage


def app_data_dir() -> Path:
    """Return the writable per-user data directory, never the install folder."""
    root = os.environ.get("REVIEWDESK_DATA_DIR")
    path = Path(root) if root else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ReviewDesk"
    path.mkdir(parents=True, exist_ok=True)
    return path


class _Credential(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR)]


class CredentialStore:
    """Store credentials with Windows protection; no plaintext fallback."""

    def __init__(self, target: str = "ReviewDesk/DeepSeek"):
        self.target = target

    def _api(self):
        if os.name != "nt":
            raise RuntimeError("安全保存密钥仅支持 Windows；可仅在本次会话使用密钥。")
        api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        api.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
        api.CredWriteW.restype = wintypes.BOOL
        api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(_Credential))]
        api.CredReadW.restype = wintypes.BOOL
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree.restype = None
        api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        api.CredDeleteW.restype = wintypes.BOOL
        return api

    def get_key(self) -> str:
        """Return an empty string if no credential has been saved."""
        api = self._api()
        pointer = ctypes.POINTER(_Credential)()
        if not api.CredReadW(self.target, 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:
                return ""
            raise RuntimeError("无法读取 Windows 凭据管理器。")
        try:
            return ctypes.string_at(pointer.contents.CredentialBlob,
                                    pointer.contents.CredentialBlobSize).decode("utf-8")
        finally:
            api.CredFree(pointer)

    def set_key(self, key: str) -> None:
        """Save a nonempty credential for the current Windows user."""
        if not isinstance(key, str) or not key.strip() or len(key) > 512:
            raise ValueError("密钥不能为空，且不得超过 512 个字符。")
        api = self._api()
        raw = key.strip().encode("utf-8")
        blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        credential = _Credential(Type=1, TargetName=self.target, UserName="DeepSeek",
                                 CredentialBlobSize=len(raw), CredentialBlob=blob, Persist=2)
        try:
            if not api.CredWriteW(ctypes.byref(credential), 0):
                raise RuntimeError("Windows 凭据保存失败；没有写入明文文件。")
        finally:
            ctypes.memset(blob, 0, len(raw))

    def delete_key(self) -> None:
        """Remove this app's credential without touching other credentials."""
        api = self._api()
        if not api.CredDeleteW(self.target, 1, 0) and ctypes.get_last_error() != 1168:
            raise RuntimeError("无法删除 Windows 凭据。")


def result_from_dict(data: dict) -> ReviewResult:
    """Restore an app-owned report with a known schema version."""
    if data.get("schema_version") != 1:
        raise ValueError("此报告版本不受支持。")
    values = dict(data)
    values["findings"] = [Finding(**item) for item in values.get("findings", [])]
    values["usage"] = Usage(**values.get("usage", {}))
    return ReviewResult(**values)


class ReportStore:
    """Short-lived SQLite connections permit safe use from worker threads."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else app_data_dir() / "reviews.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, project_name TEXT NOT NULL, mode TEXT NOT NULL, report TEXT NOT NULL)")
            db.execute("PRAGMA user_version = 1")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save_report(self, result: ReviewResult) -> int:
        """Persist findings and usage, never a complete source snapshot."""
        with self._connect() as db:
            cursor = db.execute("INSERT INTO reports(created_at,project_name,mode,report) VALUES(?,?,?,?)",
                                (result.created_at, result.project_name, result.mode,
                                 json.dumps(result.to_dict(), ensure_ascii=False)))
            return int(cursor.lastrowid)

    def update_report(self, report_id: int, result: ReviewResult) -> None:
        """Persist user triage status for an existing report."""
        with self._connect() as db:
            cursor = db.execute("UPDATE reports SET report=? WHERE id=?",
                                (json.dumps(result.to_dict(), ensure_ascii=False), report_id))
            if cursor.rowcount != 1:
                raise ValueError("报告不存在。")

    def load_report(self, report_id: int) -> ReviewResult:
        """Load one report by its local ID."""
        with self._connect() as db:
            row = db.execute("SELECT report FROM reports WHERE id=?", (report_id,)).fetchone()
        if row is None:
            raise ValueError("报告不存在。")
        return result_from_dict(json.loads(row[0]))

    def history(self) -> list[dict]:
        """Return up to 200 recent report summaries with token/cost metadata."""
        with self._connect() as db:
            rows = db.execute("SELECT id,created_at,project_name,mode,report FROM reports ORDER BY id DESC LIMIT 200").fetchall()
        summaries = []
        for report_id, created_at, project_name, mode, raw in rows:
            data = json.loads(raw)
            summaries.append({"id": report_id, "created_at": created_at, "project_name": project_name,
                              "mode": mode, "finding_count": len(data.get("findings", [])),
                              "usage": data.get("usage", {}), "file_count": data.get("file_count", 0)})
        return summaries
