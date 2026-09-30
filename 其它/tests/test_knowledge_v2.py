"""Real SQLite tests for immutable knowledge, lexical evidence and isolation."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from reviewdesk.knowledge import KnowledgeStore
from reviewdesk.models import SourceFile
from reviewdesk.retrieval import query_terms, tokenize


def fixture(marker: str = "alpha", path: str = "main.py") -> tuple[list[SourceFile], dict]:
    source = SourceFile(path, (
        "def calculateTotal(items):\n"
        "    total = sum(items)\n"
        "    return total\n"
        "\n"
        "def save_result(value):\n"
        f'    return "{marker}", value\n'
        "\n"
        "def main(items):\n"
        "    result = calculateTotal(items)\n"
        "    return save_result(result)\n"
    ))
    prefix = path + ":"
    symbols = []
    for name, start, end, purpose in (
        ("calculateTotal", 1, 3, "计算输入数值的总和"),
        ("save_result", 5, 6, "返回带有标签的结果；此示例没有写入文件"),
        ("main", 8, 10, "协调计算和结果整理的入口"),
    ):
        symbols.append({"id": prefix + name, "path": path, "name": name,
                        "qualified_name": name, "kind": "function", "start_line": start,
                        "end_line": end, "signature": name + "(...)", "docstring": "",
                        "purpose": purpose, "parameters": [], "returns": [], "steps": [],
                        "variables": []})
    calls = [{"id": prefix + "call-total", "source": prefix + "main",
              "target": prefix + "calculateTotal", "callee": "calculateTotal", "path": path,
              "line": 9, "resolved": True, "description": "入口调用计算函数"},
             {"id": prefix + "call-save", "source": prefix + "main",
              "target": prefix + "save_result", "callee": "save_result", "path": path,
              "line": 10, "resolved": True, "description": "入口调用结果整理函数"},
             {"id": prefix + "call-sum", "source": prefix + "calculateTotal",
              "target": None, "callee": "sum", "path": path, "line": 2,
              "resolved": False, "description": "内置调用不链接到项目函数"}]
    return [source], {"language": "python", "files": [{"path": path, "purpose": "示例", "line_count": 10}],
                      "symbols": symbols, "calls": calls, "entry_points": [], "limitations": []}


class KnowledgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "knowledge.db"
        self.store = KnowledgeStore(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def save(self, marker: str = "alpha", name: str = "示例项目", path: str = "main.py") -> dict:
        sources, program = fixture(marker, path)
        return self.store.save_revision(name, sources, program)

    def test_sql_tables_hold_real_files_units_calls_and_index(self) -> None:
        revision = self.save()
        with closing(sqlite3.connect(self.database)) as connection:
            for table, count in (("projects", 1), ("revisions", 1), ("source_files", 1),
                                 ("symbols", 3), ("code_units", 3), ("calls", 3), ("index_batches", 1)):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], count)
            self.assertGreater(connection.execute("SELECT COUNT(*) FROM term_postings").fetchone()[0], 10)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        snapshot = self.store.get_revision(revision["revision_id"])
        self.assertEqual(snapshot["project_name"], "示例项目")
        self.assertIn("alpha", snapshot["sources"][0]["content"])
        self.assertEqual(snapshot["program"], fixture()[1])

    def test_same_content_is_idempotent_and_immutable(self) -> None:
        first = self.save()
        sources, program = fixture()
        program["symbols"][0]["purpose"] = "新的分析不能偷偷替换旧版本"
        second = self.store.save_revision(" 示例项目 ", sources, program)
        self.assertEqual(first["revision_id"], second["revision_id"])
        self.assertFalse(second["created"])
        self.assertEqual(len(self.store.list_revisions(first["project_id"])), 1)
        stored = self.store.get_revision(first["revision_id"])
        self.assertEqual(stored["program"]["symbols"][0]["purpose"], "计算输入数值的总和")

    def test_versions_with_same_names_never_mix_sources_or_search(self) -> None:
        first = self.save("uniquebefore")
        second = self.save("uniqueafter")
        self.assertNotEqual(first["revision_id"], second["revision_id"])
        self.assertEqual(self.store.search(first["revision_id"], "uniqueafter", strategy="keyword"), [])
        self.assertEqual(self.store.search(second["revision_id"], "uniquebefore", strategy="keyword"), [])
        hits = self.store.search(first["revision_id"], "uniquebefore")
        self.assertTrue(hits)
        self.assertTrue(all("uniqueafter" not in hit["content"] for hit in hits))
        self.assertEqual(self.store.list_projects()[0]["latest_revision_id"], second["revision_id"])

    def test_analysis_upgrade_creates_snapshot_without_replacing_old_facts(self) -> None:
        sources, old_program = fixture()
        old_program["analysis_version"] = "parser-v1"
        first = self.store.save_revision("版本化分析", sources, old_program)
        new_program = deepcopy(old_program)
        new_program["analysis_version"] = "parser-v2"
        new_program["symbols"][0]["purpose"] = "升级后的分析说明"
        second = self.store.save_revision("版本化分析", sources, new_program)
        repeat = self.store.save_revision("版本化分析", sources, new_program)
        self.assertNotEqual(first["revision_id"], second["revision_id"])
        self.assertEqual(second["revision_id"], repeat["revision_id"])
        self.assertEqual(self.store.get_revision(first["revision_id"])["program"], old_program)
        self.assertEqual(self.store.get_revision(second["revision_id"])["program"], new_program)
        comparison = self.store.compare_revisions(first["revision_id"], second["revision_id"])
        self.assertEqual(comparison["files"], [])
        self.assertEqual(comparison["unchanged_count"], len(sources))

    def test_index_upgrade_creates_new_immutable_index(self) -> None:
        first = self.save()
        with patch("reviewdesk.knowledge.INDEX_VERSION", "lexical-test-v2"):
            second = self.save()
            repeat = self.save()
        self.assertNotEqual(first["revision_id"], second["revision_id"])
        self.assertEqual(second["revision_id"], repeat["revision_id"])
        with closing(sqlite3.connect(self.database)) as db:
            rows = dict(db.execute("SELECT revision_id,version FROM index_batches"))
        self.assertEqual(rows[second["revision_id"]], "lexical-test-v2")
        self.assertNotEqual(rows[first["revision_id"]], rows[second["revision_id"]])

    def test_same_contents_under_different_projects_stay_separate(self) -> None:
        first = self.save(name="项目 A")
        second = self.save(name="项目 B")
        self.assertNotEqual(first["project_id"], second["project_id"])
        self.assertNotEqual(first["revision_id"], second["revision_id"])
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        with self.assertRaisesRegex(ValueError, "同一项目"):
            self.store.compare_revisions(first["revision_id"], second["revision_id"])

    def test_chinese_query_expansion_and_symbol_ranking_are_explained(self) -> None:
        revision = self.save()
        results = self.store.search(revision["revision_id"], "如何保存结果？")
        self.assertEqual(results[0]["name"], "save_result")
        self.assertIn("中文提示", results[0]["reason"])
        exact = self.store.search(revision["revision_id"], "calculateTotal")
        self.assertEqual(exact[0]["name"], "calculateTotal")
        self.assertIn("匹配符号名", exact[0]["reason"])
        self.assertEqual([item["rank"] for item in exact], list(range(1, len(exact) + 1)))
        self.assertTrue(all(item["start_line"] <= item["end_line"] for item in results))
        self.assertEqual(results, self.store.search(revision["revision_id"], "如何保存结果？"))

    def test_call_neighbors_are_grounded_in_revision_and_not_dynamic_guesses(self) -> None:
        revision = self.save()
        keyword = self.store.search(revision["revision_id"], "alpha", strategy="keyword")
        self.assertEqual([item["name"] for item in keyword], ["save_result"])
        dependency = self.store.search(revision["revision_id"], "alpha", strategy="dependency")
        self.assertEqual(set(item["name"] for item in dependency), {"save_result", "main"})
        caller = next(item for item in dependency if item["name"] == "main")
        self.assertIn("调用关系", caller["reason"])
        self.assertIn("main.py:10", caller["reason"])
        self.assertFalse(any(item["name"] == "sum" for item in dependency))

    def test_delete_cascades_only_selected_project_and_rejects_old_revision(self) -> None:
        first = self.save("a", "删除项目")
        self.save("b", "删除项目")
        other = self.save("remain", "保留项目")
        unrelated = self.database.parent / "unrelated.txt"
        unrelated.write_text("do not delete", encoding="utf-8")
        self.store.delete_project(first["project_id"])
        self.store.delete_project(first["project_id"])
        self.assertEqual([item["name"] for item in self.store.list_projects()], ["保留项目"])
        self.assertEqual(self.store.get_revision(other["revision_id"])["project_name"], "保留项目")
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "do not delete")
        with self.assertRaisesRegex(ValueError, "不存在或已删除"):
            self.store.search(first["revision_id"], "main")
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            for table in ("source_files", "symbols", "code_units", "calls", "term_postings", "index_batches"):
                count = connection.execute(f"SELECT COUNT(*) FROM {table} WHERE revision_id=?",
                                           (first["revision_id"],)).fetchone()[0]
                self.assertEqual(count, 0)

    def test_compare_modified_added_deleted_and_exact_rename(self) -> None:
        initial_sources = [SourceFile("keep.py", "same = 1\n"), SourceFile("old.py", "move = 1\n"),
                           SourceFile("gone.py", "gone = 1\n"), SourceFile("edit.py", "value = 1\n")]
        changed_sources = [SourceFile("keep.py", "same = 1\n"), SourceFile("new.py", "move = 1\n"),
                           SourceFile("add.py", "added = 1\n"), SourceFile("edit.py", "value = 2\n")]
        base = self.store.save_revision("比较", initial_sources, {"language": "python"})
        target = self.store.save_revision("比较", changed_sources, {"language": "python"})
        comparison = self.store.compare_revisions(base["revision_id"], target["revision_id"])
        statuses = {item["path"]: item["status"] for item in comparison["files"]}
        self.assertEqual(statuses, {"new.py": "renamed", "gone.py": "deleted", "add.py": "added", "edit.py": "modified"})
        rename = next(item for item in comparison["files"] if item["status"] == "renamed")
        self.assertEqual(rename["old_path"], "old.py")
        changed = next(item for item in comparison["files"] if item["status"] == "modified")
        self.assertIn("-value = 1", changed["diff"])
        self.assertIn("+value = 2", changed["diff"])
        self.assertEqual(comparison["unchanged_count"], 1)

    def test_ambiguous_identical_files_do_not_claim_rename(self) -> None:
        base = self.store.save_revision("比较", [SourceFile("a.py", "x = 1"), SourceFile("b.py", "x = 1")], {})
        target = self.store.save_revision("比较", [SourceFile("c.py", "x = 1")], {})
        changes = self.store.compare_revisions(base["revision_id"], target["revision_id"])["files"]
        self.assertEqual([item["status"] for item in changes], ["deleted", "deleted", "added"])

    def test_final_newline_change_is_visible(self) -> None:
        base = self.store.save_revision("换行", [SourceFile("x.py", "x = 1")], {})
        target = self.store.save_revision("换行", [SourceFile("x.py", "x = 1\n")], {})
        change = self.store.compare_revisions(base["revision_id"], target["revision_id"])["files"][0]
        self.assertEqual(change["status"], "modified")
        self.assertIn("No newline at end of file", change["diff"])

    def test_multi_thread_save_get_and_search_use_independent_connections(self) -> None:
        def task(index: int) -> tuple[str, int]:
            saved = self.save("thread" + str(index % 3))
            snapshot = self.store.get_revision(saved["revision_id"])
            hits = self.store.search(saved["revision_id"], "calculateTotal")
            self.assertTrue(hits)
            return snapshot["revision_id"], len(snapshot["sources"])
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(task, range(24)))
        self.assertEqual(len({revision for revision, _ in results}), 3)
        self.assertTrue(all(count == 1 for _, count in results))
        self.assertEqual(self.store.list_projects()[0]["revision_count"], 3)

    def test_failed_index_write_rolls_back_every_table_and_project(self) -> None:
        existing = self.save(name="原有项目")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("""CREATE TRIGGER fail_new_posting BEFORE INSERT ON term_postings
                BEGIN SELECT RAISE(ABORT, 'simulated index failure'); END""")
            connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.save("broken", name="失败的新项目")
        self.assertEqual([project["name"] for project in self.store.list_projects()], ["原有项目"])
        self.assertTrue(self.store.search(existing["revision_id"], "alpha"))
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM index_batches").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_read_uses_one_snapshot_even_if_project_deleted_mid_request(self) -> None:
        saved = self.save()
        original_lookup = self.store._revision

        def lookup_then_delete(connection: sqlite3.Connection, revision_id: str):
            row = original_lookup(connection, revision_id)
            self.store.delete_project(saved["project_id"])
            return row

        with patch.object(self.store, "_revision", side_effect=lookup_then_delete):
            snapshot = self.store.get_revision(saved["revision_id"])
        self.assertEqual(len(snapshot["sources"]), 1)
        self.assertIn("alpha", snapshot["sources"][0]["content"])
        with self.assertRaises(ValueError):
            self.store.get_revision(saved["revision_id"])

    def test_invalid_paths_duplicates_hashes_and_spans_publish_nothing(self) -> None:
        for invalid in ("../outside.py", "a/../../escape.py", "C:\\secret.py", "/tmp/a.py", "C:drive.py",
                        "a\\..\\b.py", "file.py:stream", "a//b.py", "a/./b.py", "a\x00.py"):
            with self.subTest(path=invalid), self.assertRaises(ValueError):
                self.store.save_revision("非法", [SourceFile(invalid, "x = 1")], {})
        with self.assertRaisesRegex(ValueError, "重复路径"):
            self.store.save_revision("重复", [SourceFile("a.py", "a=1"), SourceFile("A.py", "a=2")], {})
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.store.save_revision("哈希", [SourceFile("a.py", "a=1", "bad")], {})
        sources, program = fixture()
        program["symbols"][0]["end_line"] = 999
        with self.assertRaisesRegex(ValueError, "有效行号"):
            self.store.save_revision("行号", sources, program)
        self.assertEqual(self.store.list_projects(), [])

    def test_invalid_call_references_and_nonfinite_json_are_rejected(self) -> None:
        sources, original = fixture()
        for target in ("missing", ["not a string"]):
            program = deepcopy(original)
            program["calls"][0]["target"] = target
            with self.assertRaises(ValueError):
                self.store.save_revision("错误关系", sources, program)
        program = deepcopy(original)
        program["extra"] = float("nan")
        with self.assertRaisesRegex(ValueError, "JSON"):
            self.store.save_revision("JSON", sources, program)
        self.assertEqual(self.store.list_projects(), [])

    def test_query_bounds_injection_and_empty_queries(self) -> None:
        revision = self.save(name="Robert'); DROP TABLE projects;--")
        for query, limit, strategy in (("x" * 2001, 8, "hybrid"), ("x", 0, "hybrid"),
                                       ("x", True, "hybrid"), ("x", 8, "sql")):
            with self.assertRaises(ValueError):
                self.store.search(revision["revision_id"], query, limit, strategy)
        self.assertEqual(self.store.search(revision["revision_id"], "   "), [])
        self.store.search(revision["revision_id"], "' OR 1=1; DROP TABLE revisions;--")
        self.assertEqual(len(self.store.list_projects()), 1)
        self.assertEqual(len(self.store.list_revisions(revision["project_id"])), 1)

    def test_long_code_units_have_precise_source_ranges(self) -> None:
        text = "\n".join("value_" + str(index) + " = " + str(index) for index in range(230))
        saved = self.store.save_revision("长文件", [SourceFile("long.py", text)], {})
        hits = self.store.search(saved["revision_id"], "value_229", strategy="keyword")
        hit = next(item for item in hits if "value_229 = 229" in item["content"])
        self.assertEqual((hit["start_line"], hit["end_line"]), (201, 230))
        self.assertEqual(hit["content"], "\n".join(text.splitlines()[200:230]))

    def test_windows_separator_normalized_without_writing_source_paths(self) -> None:
        saved = self.store.save_revision("路径", [SourceFile("pkg\\safe.py", "x = 1")], {})
        source = self.store.get_revision(saved["revision_id"])["sources"][0]
        self.assertEqual(source["path"], "pkg/safe.py")
        self.assertEqual(source["sha256"], hashlib.sha256(b"x = 1").hexdigest())
        self.assertFalse((self.database.parent / "pkg").exists())

    def test_database_reopens_without_process_memory_or_open_handles(self) -> None:
        saved = self.save()
        reopened = KnowledgeStore(self.database)
        self.assertEqual(reopened.get_revision(saved["revision_id"])["revision_id"], saved["revision_id"])
        self.assertTrue(reopened.search(saved["revision_id"], "alpha"))
        moved = self.database.with_name("renamed.db")
        self.database.rename(moved)
        self.assertTrue(KnowledgeStore(moved).list_projects())

    def test_python_adapter_output_matches_sql_contract_without_losing_calls(self) -> None:
        from reviewdesk.demo_project import DEMO_FILES
        from reviewdesk.languages import get_adapter

        sources = [SourceFile(path, content) for path, content in DEMO_FILES.items()]
        program = get_adapter("python").analyze(sources)
        saved = self.store.save_revision("购物示例", sources, program)
        snapshot = self.store.get_revision(saved["revision_id"])
        self.assertEqual(snapshot["program"]["calls"], program["calls"])
        hits = self.store.search(saved["revision_id"], "金额如何计算")
        self.assertTrue(hits)
        self.assertIn("calculate_order", hits[0]["name"])
        self.assertIn("subtotal", hits[0]["content"])

    def test_unicode_separators_inside_source_preserve_python_ast_line_numbers(self) -> None:
        from reviewdesk.languages import get_adapter

        for separator in ("\u2028", "\u2029", "\f", "\v"):
            with self.subTest(separator=repr(separator)):
                content = f'label = "a{separator}b"\ndef correct_function():\n    return 123\n'
                sources = [SourceFile("unicode.py", content)]
                program = get_adapter("python").analyze(sources)
                saved = self.store.save_revision("物理行" + repr(separator), sources, program)
                hits = self.store.search(saved["revision_id"], "correct_function", strategy="keyword")
                function = next(item for item in hits if item["kind"] == "function")
                self.assertEqual((function["start_line"], function["end_line"]), (2, 3))
                self.assertEqual(function["content"], "def correct_function():\n    return 123")
                changed_sources = [SourceFile("unicode.py", content.replace("123", "456"))]
                changed = self.store.save_revision("物理行" + repr(separator), changed_sources,
                                                   get_adapter("python").analyze(changed_sources))
                difference = self.store.compare_revisions(saved["revision_id"], changed["revision_id"])
                self.assertIn("@@ -1,3 +1,3 @@", difference["files"][0]["diff"])


class LexicalTokenTests(unittest.TestCase):
    def test_snake_camel_chinese_tokens(self) -> None:
        tokens = set(tokenize("save_result calculateTotal HTTPServer 中文变量变化"))
        self.assertTrue({"save_result", "save", "result", "calculatetotal", "calculate", "total",
                         "http", "server", "中文", "变量", "变化"}.issubset(tokens))
        weights, aliases = query_terms("保存结果")
        self.assertLess(weights["save"], weights["保存"])
        self.assertEqual(aliases["save"], "保存")


if __name__ == "__main__":
    unittest.main()
