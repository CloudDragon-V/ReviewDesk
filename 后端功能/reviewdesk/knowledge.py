"""Version-isolated SQLite code knowledge with reproducible lexical retrieval.

Source snapshots, symbols, units, calls and term postings have separate SQL
tables. The program JSON is an immutable display snapshot, not the search index.
Every operation closes its connection; a revision and its index publish in one
transaction so readers never see a partially indexed revision.
"""

from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sqlite3
from typing import Any, Iterator
import unicodedata
import uuid

from .models import SourceFile
from .context import source_lines
from .retrieval import bm25, query_terms, term_counts, tokenize


MAX_FILES = 1000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_PROJECT_BYTES = 24 * 1024 * 1024
MAX_SYMBOLS = 20000
MAX_CALLS = 100000
UNIT_LINES = 100
INDEX_VERSION = "lexical-bm25-call-v1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL,
    language TEXT NOT NULL,
    created_at TEXT NOT NULL,
    program_json TEXT NOT NULL,
    file_count INTEGER NOT NULL,
    symbol_count INTEGER NOT NULL,
    call_count INTEGER NOT NULL,
    UNIQUE(project_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS revisions_by_project ON revisions(project_id, created_at);
CREATE TABLE IF NOT EXISTS source_files (
    revision_id TEXT NOT NULL REFERENCES revisions(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    content TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    line_count INTEGER NOT NULL,
    PRIMARY KEY(revision_id, path)
);
CREATE TABLE IF NOT EXISTS symbols (
    revision_id TEXT NOT NULL,
    id TEXT NOT NULL,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    signature TEXT NOT NULL,
    docstring TEXT NOT NULL,
    purpose TEXT NOT NULL,
    PRIMARY KEY(revision_id, id),
    FOREIGN KEY(revision_id, path) REFERENCES source_files(revision_id, path) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS symbols_by_file ON symbols(revision_id, path);
CREATE TABLE IF NOT EXISTS code_units (
    revision_id TEXT NOT NULL,
    id TEXT NOT NULL,
    symbol_id TEXT NOT NULL,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    content TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    PRIMARY KEY(revision_id, id),
    FOREIGN KEY(revision_id, symbol_id) REFERENCES symbols(revision_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS units_by_symbol ON code_units(revision_id, symbol_id);
CREATE TABLE IF NOT EXISTS term_postings (
    revision_id TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    term TEXT NOT NULL,
    frequency INTEGER NOT NULL,
    PRIMARY KEY(revision_id, term, unit_id),
    FOREIGN KEY(revision_id, unit_id) REFERENCES code_units(revision_id, id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS calls (
    revision_id TEXT NOT NULL,
    id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT,
    callee TEXT NOT NULL,
    path TEXT NOT NULL,
    line INTEGER NOT NULL,
    resolved INTEGER NOT NULL,
    description TEXT NOT NULL,
    PRIMARY KEY(revision_id, id),
    FOREIGN KEY(revision_id, source_id) REFERENCES symbols(revision_id, id) ON DELETE CASCADE,
    FOREIGN KEY(revision_id, target_id) REFERENCES symbols(revision_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS calls_by_source ON calls(revision_id, source_id);
CREATE INDEX IF NOT EXISTS calls_by_target ON calls(revision_id, target_id);
CREATE TABLE IF NOT EXISTS index_batches (
    id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL UNIQUE REFERENCES revisions(id) ON DELETE CASCADE,
    version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status = 'completed'),
    completed_at TEXT NOT NULL,
    unit_count INTEGER NOT NULL,
    posting_count INTEGER NOT NULL
);
PRAGMA user_version = 1;
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative_path(value: Any) -> str:
    """Normalize a source label while rejecting Windows/Posix traversal forms."""
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("源码路径不能为空，且最多为 1024 个字符。")
    normalized = unicodedata.normalize("NFC", value.replace("\\", "/"))
    windows = PureWindowsPath(value)
    parts = normalized.split("/")
    if (windows.drive or windows.root or PurePosixPath(normalized).is_absolute()
            or any(part in ("", ".", "..") for part in parts)
            or any(ord(character) < 32 for character in normalized)
            or any(character in normalized for character in ':*?"<>|')
            or any(part.endswith((" ", ".")) for part in parts)):
        raise ValueError("源码路径必须是项目内的相对路径，不得包含越界或特殊路径。")
    return normalized


def _bounded_text(value: Any, field: str, maximum: int = 20000) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{field}必须为文本，且不得超过 {maximum} 个字符。")
    return value


def _identifier(value: Any, field: str) -> str:
    text = _bounded_text(value, field, 2048)
    if not text or "\x00" in text:
        raise ValueError(f"{field}不能为空或包含空字符。")
    return text


def _integer(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{field}超出源码的有效行号。")
    return value


class KnowledgeStore:
    """Own one local knowledge database and only delete its project snapshots."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection(snapshot=False) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("知识库版本高于当前软件支持范围，请使用较新版本打开。")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)

    @contextmanager
    def _connection(self, write: bool = False, snapshot: bool = True) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=20000")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            elif snapshot:
                connection.execute("BEGIN")
            yield connection
            if write or snapshot:
                connection.commit()
        except BaseException:
            if write or snapshot:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _prepare(self, sources: list[SourceFile], program: dict) -> dict:
        if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_FILES:
            raise ValueError(f"每个版本需要 1 至 {MAX_FILES} 个源码文件。")
        if not isinstance(program, dict):
            raise ValueError("程序分析结果必须是对象。")
        language = _bounded_text(program.get("language", "python"), "语言", 64)
        if not language:
            raise ValueError("必须指定源码语言。")
        by_path: dict[str, dict] = {}
        path_keys: set[str] = set()
        total_size = 0
        for source in sources:
            if not isinstance(source, SourceFile):
                raise ValueError("源码文件必须使用 SourceFile 数据类型。")
            path = _relative_path(source.path)
            if path.casefold() in path_keys:
                raise ValueError(f"同一版本包含重复路径：{path}")
            path_keys.add(path.casefold())
            if not isinstance(source.content, str):
                raise ValueError("源码内容必须为文本。")
            if len(source.content) > MAX_FILE_BYTES:
                raise ValueError("单个源码文件不能超过 2 MB。")
            content_bytes = source.content.encode("utf-8")
            size = len(content_bytes)
            if size > MAX_FILE_BYTES:
                raise ValueError("单个源码文件不能超过 2 MB。")
            total_size += size
            if total_size > MAX_PROJECT_BYTES:
                raise ValueError("单次导入的源码总量不能超过 24 MB。")
            digest = hashlib.sha256(content_bytes).hexdigest()
            if source.sha256 and source.sha256 != digest:
                raise ValueError(f"源码内容与提供的 SHA-256 不一致：{path}")
            by_path[path] = {"path": path, "content": source.content,
                             "sha256": digest, "line_count": max(1, len(source_lines(source.content)))}

        try:
            program_json = json.dumps(program, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":"), allow_nan=False)
        except (ValueError, TypeError, RecursionError) as exc:
            raise ValueError("程序分析结果必须为有限、可序列化的 JSON 数据。") from exc
        if len(program_json.encode("utf-8")) > 64 * 1024 * 1024:
            raise ValueError("程序分析结果超过 64 MB 上限。")
        symbols_input = program.get("symbols", [])
        calls_input = program.get("calls", [])
        if not isinstance(symbols_input, list) or len(symbols_input) > MAX_SYMBOLS:
            raise ValueError(f"符号列表必须为数组，且最多包含 {MAX_SYMBOLS} 项。")
        if not isinstance(calls_input, list) or len(calls_input) > MAX_CALLS:
            raise ValueError(f"调用列表必须为数组，且最多包含 {MAX_CALLS} 项。")

        symbols: list[dict] = []
        symbol_ids: set[str] = set()
        symbol_by_id: dict[str, dict] = {}
        for item in symbols_input:
            if not isinstance(item, dict):
                raise ValueError("每个符号必须是对象。")
            symbol_id = _identifier(item.get("id"), "符号 ID")
            if symbol_id in symbol_ids:
                raise ValueError("分析结果包含重复符号 ID。")
            symbol_ids.add(symbol_id)
            path = _relative_path(item.get("path"))
            if path not in by_path:
                raise ValueError("符号引用了当前版本不存在的文件。")
            start = _integer(item.get("start_line"), "符号开始行", by_path[path]["line_count"])
            end = _integer(item.get("end_line"), "符号结束行", by_path[path]["line_count"])
            if end < start:
                raise ValueError("符号结束行不能早于开始行。")
            symbol = {"id": symbol_id, "path": path, "start_line": start, "end_line": end,
                      "name": _bounded_text(item.get("name", ""), "符号名称", 2048),
                      "qualified_name": _bounded_text(item.get("qualified_name", ""), "完整符号名", 4096),
                      "kind": _bounded_text(item.get("kind", "symbol"), "符号类别", 64),
                      "signature": _bounded_text(item.get("signature", ""), "签名"),
                      "docstring": _bounded_text(item.get("docstring", ""), "文档字符串", MAX_FILE_BYTES),
                      "purpose": _bounded_text(item.get("purpose", ""), "用途")}
            symbols.append(symbol)
            symbol_by_id[symbol_id] = symbol

        # Syntax-invalid or otherwise symbol-free files still remain searchable.
        symbol_paths = {symbol["path"] for symbol in symbols}
        for path, source in by_path.items():
            if path not in symbol_paths:
                symbol_id = f"snapshot:{path}"
                while symbol_id in symbol_ids:
                    symbol_id += ":file"
                symbol = {"id": symbol_id, "path": path, "start_line": 1,
                          "end_line": source["line_count"], "name": path,
                          "qualified_name": path, "kind": "module", "signature": "",
                          "docstring": "", "purpose": "源码快照；无可用结构符号"}
                symbols.append(symbol)
                symbol_ids.add(symbol_id)
                symbol_by_id[symbol_id] = symbol

        calls: list[dict] = []
        call_ids: set[str] = set()
        for item in calls_input:
            if not isinstance(item, dict):
                raise ValueError("每个调用必须是对象。")
            call_id = _identifier(item.get("id"), "调用 ID")
            if call_id in call_ids:
                raise ValueError("分析结果包含重复调用 ID。")
            call_ids.add(call_id)
            source_id = _identifier(item.get("source"), "调用方 ID")
            target_id = item.get("target")
            if target_id is not None:
                target_id = _identifier(target_id, "被调用方 ID")
            if source_id not in symbol_ids or (target_id is not None and target_id not in symbol_ids):
                raise ValueError("调用关系引用了当前版本不存在的符号。")
            path = _relative_path(item.get("path"))
            if path not in by_path or path != symbol_by_id[source_id]["path"]:
                raise ValueError("调用位置必须位于调用方所属文件内。")
            line = _integer(item.get("line"), "调用行", by_path[path]["line_count"])
            if type(item.get("resolved", False)) is not bool:
                raise ValueError("调用解析状态必须为布尔值。")
            resolved = item.get("resolved", False)
            if resolved != (target_id is not None):
                raise ValueError("已解析调用必须指向当前版本中明确的符号。")
            calls.append({"id": call_id, "source": source_id, "target": target_id,
                          "callee": _bounded_text(item.get("callee", ""), "调用名称", 4096),
                          "path": path, "line": line, "resolved": int(resolved),
                          "description": _bounded_text(item.get("description", ""), "调用说明")})

        units: list[dict] = []
        postings: list[tuple] = []
        lines_by_path = {path: source_lines(source["content"]) for path, source in by_path.items()}
        for symbol in symbols:
            for start in range(symbol["start_line"], symbol["end_line"] + 1, UNIT_LINES):
                end = min(start + UNIT_LINES - 1, symbol["end_line"])
                content = "\n".join(lines_by_path[symbol["path"]][start - 1:end])
                # Unit identity never shares a namespace with original symbol IDs.
                unit_id = hashlib.sha256(f'{symbol["id"]}\0{start}\0{end}'.encode()).hexdigest()[:32]
                indexed = "\n".join((symbol["name"], symbol["qualified_name"], symbol["path"],
                                      symbol["signature"], symbol["purpose"], symbol["docstring"], content))
                counts = term_counts(indexed)
                unit = {"id": unit_id, "symbol_id": symbol["id"], "path": symbol["path"],
                        "name": symbol["qualified_name"] or symbol["name"], "kind": symbol["kind"],
                        "start_line": start, "end_line": end, "content": content,
                        "token_count": sum(counts.values())}
                units.append(unit)
                postings.extend((unit_id, term, count) for term, count in counts.items())
                if len(units) > 50000 or len(postings) > 2000000:
                    raise ValueError("代码索引超过本地版容量，请拆分项目后再导入。")

        # Re-import after a parser/index upgrade creates a new immutable analysis
        # instead of silently reusing stale facts or overwriting historical reports.
        analysis_version = _bounded_text(program.get("analysis_version", "unversioned"),
                                         "分析器版本", 128)
        digest_data = {"language": language, "analysis_version": analysis_version,
                       "index_version": INDEX_VERSION, "files": [
            (path, source["sha256"]) for path, source in sorted(by_path.items())]}
        fingerprint = hashlib.sha256(json.dumps(digest_data, ensure_ascii=False,
                                                separators=(",", ":")).encode()).hexdigest()
        return {"language": language, "sources": list(by_path.values()), "symbols": symbols,
                "calls": calls, "units": units, "postings": postings,
                "fingerprint": fingerprint, "program_json": program_json}

    def save_revision(self, project_name: str, sources: list[SourceFile], program: dict) -> dict:
        """Validate and publish a complete immutable revision, idempotent by content."""
        name = unicodedata.normalize("NFC", _bounded_text(project_name, "项目名称", 200)).strip()
        if not name or any(ord(character) < 32 for character in name):
            raise ValueError("项目名称不能为空或包含控制字符。")
        prepared = self._prepare(sources, program)
        name_key = name.casefold()
        with self._connection(write=True) as connection:
            created_at = _utc_now()
            project = connection.execute("SELECT id FROM projects WHERE name_key=?", (name_key,)).fetchone()
            if project is None:
                project_id = uuid.uuid4().hex
                connection.execute("INSERT INTO projects VALUES (?,?,?,?)",
                                   (project_id, name, name_key, created_at))
            else:
                project_id = project["id"]
            existing = connection.execute(
                "SELECT id FROM revisions WHERE project_id=? AND fingerprint=?",
                (project_id, prepared["fingerprint"])).fetchone()
            if existing:
                return {"project_id": project_id, "revision_id": existing["id"],
                        "fingerprint": prepared["fingerprint"], "created": False}
            revision_id = uuid.uuid4().hex
            connection.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?,?)", (
                revision_id, project_id, prepared["fingerprint"], prepared["language"], created_at,
                prepared["program_json"], len(prepared["sources"]), len(prepared["symbols"]),
                len(prepared["calls"])))
            connection.executemany("INSERT INTO source_files VALUES (?,?,?,?,?)", [
                (revision_id, source["path"], source["content"], source["sha256"], source["line_count"])
                for source in prepared["sources"]])
            connection.executemany("INSERT INTO symbols VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
                (revision_id, symbol["id"], symbol["path"], symbol["name"], symbol["qualified_name"],
                 symbol["kind"], symbol["start_line"], symbol["end_line"], symbol["signature"],
                 symbol["docstring"], symbol["purpose"]) for symbol in prepared["symbols"]])
            connection.executemany("INSERT INTO code_units VALUES (?,?,?,?,?,?,?,?,?,?)", [
                (revision_id, unit["id"], unit["symbol_id"], unit["path"], unit["name"], unit["kind"],
                 unit["start_line"], unit["end_line"], unit["content"], unit["token_count"])
                for unit in prepared["units"]])
            connection.executemany("INSERT INTO term_postings VALUES (?,?,?,?)", [
                (revision_id, *posting) for posting in prepared["postings"]])
            connection.executemany("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?)", [
                (revision_id, call["id"], call["source"], call["target"], call["callee"], call["path"],
                 call["line"], call["resolved"], call["description"]) for call in prepared["calls"]])
            connection.execute("INSERT INTO index_batches VALUES (?,?,?,?,?,?,?)", (
                uuid.uuid4().hex, revision_id, INDEX_VERSION, "completed", _utc_now(),
                len(prepared["units"]), len(prepared["postings"])))
            return {"project_id": project_id, "revision_id": revision_id,
                    "fingerprint": prepared["fingerprint"], "created": True}

    def list_projects(self) -> list[dict]:
        """List project summaries; the newest published revision is explicit."""
        with self._connection() as connection:
            rows = connection.execute("""
                SELECT p.id,p.name,p.created_at,COUNT(r.id) AS revision_count,
                  (SELECT id FROM revisions WHERE project_id=p.id
                   ORDER BY created_at DESC,rowid DESC LIMIT 1) AS latest_revision_id,
                  MAX(r.created_at) AS updated_at
                FROM projects p LEFT JOIN revisions r ON r.project_id=p.id
                GROUP BY p.id ORDER BY updated_at DESC,p.name
            """).fetchall()
            return [dict(row) for row in rows]

    def list_revisions(self, project_id: str) -> list[dict]:
        """Return only the requested project's published revisions."""
        project_id = _identifier(project_id, "项目 ID")
        with self._connection() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise ValueError("该项目不存在或已删除。")
            rows = connection.execute("""
                SELECT r.id,r.id AS revision_id,r.project_id,r.fingerprint,r.language,r.created_at,
                  r.file_count,r.symbol_count,r.call_count,b.unit_count,b.version AS index_version
                FROM revisions r JOIN index_batches b ON b.revision_id=r.id
                WHERE r.project_id=? ORDER BY r.created_at DESC,r.rowid DESC
            """, (project_id,)).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _revision(connection: sqlite3.Connection, revision_id: str) -> sqlite3.Row:
        row = connection.execute("""
            SELECT r.*,p.name AS project_name FROM revisions r JOIN projects p ON p.id=r.project_id
            JOIN index_batches b ON b.revision_id=r.id WHERE r.id=?
        """, (revision_id,)).fetchone()
        if row is None:
            raise ValueError("该代码版本不存在或已删除，请重新选择项目版本。")
        return row

    def get_revision(self, revision_id: str) -> dict:
        """Return exact source and analysis snapshots, without consulting newer indexes."""
        revision_id = _identifier(revision_id, "版本 ID")
        with self._connection() as connection:
            row = self._revision(connection, revision_id)
            result = dict(row)
            result["revision_id"] = result["id"]
            result["program"] = json.loads(result.pop("program_json"))
            result["sources"] = [dict(item) for item in connection.execute(
                "SELECT path,content,sha256 FROM source_files WHERE revision_id=? ORDER BY path",
                (revision_id,)).fetchall()]
            return result

    def search(self, revision_id: str, query: str, limit: int = 8,
               strategy: str = "hybrid") -> list[dict]:
        """Rank same-revision evidence by BM25, symbols and optional one-hop calls.

        keyword = lexical BM25; hybrid adds symbol weighting and call neighbors;
        dependency emphasizes symbol matches and call neighbors. All three are
        deterministic lexical/structural baselines, not learned semantic RAG.
        """
        revision_id = _identifier(revision_id, "版本 ID")
        query = _bounded_text(query, "检索问题", 2000).strip()
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("检索结果数量必须是 1 至 50 的整数。")
        if strategy not in ("hybrid", "keyword", "dependency"):
            raise ValueError("检索策略仅支持 hybrid、keyword 和 dependency。")
        weights, aliases = query_terms(query)
        with self._connection() as connection:
            self._revision(connection, revision_id)
            if not weights:
                return []
            placeholders = ",".join("?" for _ in weights)
            rows = connection.execute(f"""
                SELECT p.unit_id,p.term,p.frequency,u.token_count,u.name,u.kind,u.symbol_id
                FROM term_postings p JOIN code_units u
                  ON u.revision_id=p.revision_id AND u.id=p.unit_id
                WHERE p.revision_id=? AND p.term IN ({placeholders})
            """, (revision_id, *weights)).fetchall()
            if not rows:
                return []
            stats = connection.execute(
                "SELECT COUNT(*) AS count,AVG(token_count) AS avg FROM code_units WHERE revision_id=?",
                (revision_id,)).fetchone()
            frequencies = Counter(row["term"] for row in rows)
            scores: defaultdict[str, float] = defaultdict(float)
            reasons: defaultdict[str, list[str]] = defaultdict(list)
            symbols: dict[str, str] = {}
            for row in rows:
                unit_id, term = row["unit_id"], row["term"]
                contribution = bm25(row["frequency"], row["token_count"], frequencies[term],
                                    stats["count"], stats["avg"] or 1.0) * weights[term]
                if row["kind"] == "module":
                    contribution *= 0.55
                name_terms = set(tokenize(row["name"]))
                if strategy != "keyword" and term in name_terms:
                    contribution *= 2.5 if strategy == "hybrid" else 3.5
                scores[unit_id] += contribution
                reason = f'中文提示“{aliases[term]}”→ {term}' if term in aliases else f'关键词“{term}”'
                if strategy != "keyword" and term in name_terms:
                    reason += "匹配符号名"
                if reason not in reasons[unit_id] and len(reasons[unit_id]) < 6:
                    reasons[unit_id].append(reason)
                symbols[unit_id] = row["symbol_id"]
            if strategy != "keyword":
                seeds = sorted(scores, key=lambda unit: (-scores[unit], unit))[:min(8, limit)]
                self._expand_calls(connection, revision_id, seeds, scores, reasons, symbols, strategy)
            ordered = sorted(scores, key=lambda unit: (-scores[unit], unit))[:limit]
            placeholders = ",".join("?" for _ in ordered)
            units = {row["id"]: dict(row) for row in connection.execute(f"""
                SELECT id,symbol_id,path,name,kind,start_line,end_line,content
                FROM code_units WHERE revision_id=? AND id IN ({placeholders})
            """, (revision_id, *ordered)).fetchall()}
            result = []
            for rank, unit_id in enumerate(ordered, 1):
                item = units[unit_id]
                item.update(score=round(scores[unit_id], 6), rank=rank,
                            reason="；".join(reasons[unit_id]), strategy=strategy,
                            retrieval_method="词法 BM25 + 符号/调用关系" if strategy != "keyword" else "词法 BM25")
                result.append(item)
            return result

    @staticmethod
    def _expand_calls(connection: sqlite3.Connection, revision_id: str, seeds: list[str],
                      scores: dict[str, float], reasons: dict[str, list[str]],
                      symbols: dict[str, str], strategy: str) -> None:
        """Add bounded one-hop neighbors; unresolved calls never invent a target."""
        additions: dict[str, float] = {}
        for seed in seeds:
            symbol_id = symbols[seed]
            calls = connection.execute("""
                SELECT source_id,target_id,callee,path,line FROM calls
                WHERE revision_id=? AND target_id IS NOT NULL AND (source_id=? OR target_id=?)
                ORDER BY path,line,id LIMIT 100
            """, (revision_id, symbol_id, symbol_id)).fetchall()
            for call in calls:
                outgoing = call["source_id"] == symbol_id
                neighbor = call["target_id"] if outgoing else call["source_id"]
                neighbor_units = connection.execute("""
                    SELECT id,name,start_line,end_line FROM code_units
                    WHERE revision_id=? AND symbol_id=? ORDER BY start_line LIMIT 3
                """, (revision_id, neighbor)).fetchall()
                for unit in neighbor_units:
                    if unit["id"] == seed:
                        continue
                    multiplier = 0.38 if strategy == "hybrid" else 0.70
                    contribution = scores[seed] * multiplier
                    additions[unit["id"]] = max(additions.get(unit["id"], 0.0), contribution)
                    direction = "命中函数调用此处" if outgoing else "此处调用命中函数"
                    note = f'调用关系：{direction}（{call["path"]}:{call["line"]}，{call["callee"]}）'
                    if note not in reasons[unit["id"]] and len(reasons[unit["id"]]) < 8:
                        reasons[unit["id"]].append(note)
        for unit_id, score in additions.items():
            scores[unit_id] = scores.get(unit_id, 0.0) + score

    def delete_project(self, project_id: str) -> None:
        """Cascade-delete this project's knowledge only; no filesystem paths are used."""
        project_id = _identifier(project_id, "项目 ID")
        with self._connection(write=True) as connection:
            connection.execute("DELETE FROM projects WHERE id=?", (project_id,))

    def compare_revisions(self, base_id: str, target_id: str) -> dict:
        """Compare same-project snapshots; only unambiguous exact-content moves rename."""
        base_id = _identifier(base_id, "基础版本 ID")
        target_id = _identifier(target_id, "目标版本 ID")
        with self._connection() as connection:
            base = self._revision(connection, base_id)
            target = self._revision(connection, target_id)
            if base["project_id"] != target["project_id"]:
                raise ValueError("仅支持比较同一项目的两个版本。")
            base_files = {row["path"]: dict(row) for row in connection.execute(
                "SELECT path,content,sha256 FROM source_files WHERE revision_id=?", (base_id,))}
            target_files = {row["path"]: dict(row) for row in connection.execute(
                "SELECT path,content,sha256 FROM source_files WHERE revision_id=?", (target_id,))}
        removed = set(base_files) - set(target_files)
        added = set(target_files) - set(base_files)
        removed_hashes: defaultdict[str, list[str]] = defaultdict(list)
        added_hashes: defaultdict[str, list[str]] = defaultdict(list)
        for path in removed:
            removed_hashes[base_files[path]["sha256"]].append(path)
        for path in added:
            added_hashes[target_files[path]["sha256"]].append(path)
        files: list[dict] = []
        for digest in sorted(removed_hashes.keys() & added_hashes.keys()):
            old_paths, new_paths = removed_hashes[digest], added_hashes[digest]
            if len(old_paths) == len(new_paths) == 1:
                old_path, new_path = old_paths[0], new_paths[0]
                files.append({"path": new_path, "old_path": old_path, "status": "renamed",
                              "diff": f"rename from {old_path}\nrename to {new_path}\n",
                              "reason": "内容 SHA-256 完全相同且删除/新增路径一一对应"})
                removed.remove(old_path)
                added.remove(new_path)
        for path in sorted(base_files.keys() & target_files.keys()):
            if base_files[path]["sha256"] != target_files[path]["sha256"]:
                files.append(self._file_diff(path, "modified", base_files[path]["content"],
                                             target_files[path]["content"]))
        files.extend(self._file_diff(path, "deleted", base_files[path]["content"], "") for path in sorted(removed))
        files.extend(self._file_diff(path, "added", "", target_files[path]["content"]) for path in sorted(added))
        files.sort(key=lambda item: (item["path"], item["status"]))
        return {"project_id": base["project_id"], "base_id": base_id, "target_id": target_id,
                "files": files, "unchanged_count": sum(
                    base_files[path]["sha256"] == target_files[path]["sha256"]
                    for path in base_files.keys() & target_files.keys()),
                "limitations": ["仅将内容完全相同的一对一文件移动识别为重命名；移动并修改显示为删除和新增。"]}

    @staticmethod
    def _file_diff(path: str, status: str, before: str, after: str) -> dict:
        # Keep line endings so a missing final newline does not disappear silently.
        def physical_lines(text: str) -> list[str]:
            # Unlike str.splitlines(), Python physical lines do not split on
            # U+2028, form feeds or vertical tabs inside a string/comment.
            return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text) if line]

        parts = list(difflib.unified_diff(physical_lines(before), physical_lines(after),
                                          fromfile="/dev/null" if status == "added" else f"a/{path}",
                                          tofile="/dev/null" if status == "deleted" else f"b/{path}", n=3))
        diff = "".join(part if part.endswith(("\n", "\r")) else part + "\n\\ No newline at end of file\n"
                       for part in parts)
        return {"path": path, "status": status, "diff": diff}
