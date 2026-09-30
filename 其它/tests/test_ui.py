"""Offline Qt integration checks for user actions, persistence and worker lifecycle."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import threading
import time
import unittest
import sqlite3
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from reviewdesk.models import Context, ReviewResult, Usage
from reviewdesk.service import ReviewService
from reviewdesk.storage import ReportStore
from reviewdesk_ui.ui import MainWindow


class MemoryCredentials:
    """No system credential access or real credentials in UI tests."""

    def __init__(self):
        self.key = ""

    def get_key(self):
        return self.key

    def set_key(self, key):
        self.key = key

    def delete_key(self):
        self.key = ""


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.service = ReviewService(ReportStore(Path(self.directory.name) / "reviews.db"))
        self.credentials = MemoryCredentials()
        self.window = MainWindow(self.service, self.credentials)
        self.window.show()
        QTest.qWait(40)

    def tearDown(self):
        self.wait_idle()
        self.window.close()
        QTest.qWait(15)
        self.directory.cleanup()

    def wait_idle(self, timeout=7000):
        deadline = time.monotonic() + timeout / 1000
        while self.window._thread is not None or self.window._operations:
            QTest.qWait(15)
            if time.monotonic() > deadline:
                self.fail("UI worker did not become idle")
        QTest.qWait(25)

    def load_demo(self):
        QTest.mouseClick(self.window.demo_button, Qt.MouseButton.LeftButton)
        self.wait_idle()
        self.assertIsNotNone(self.window.result, self.window.notice.text())

    def test_demo_result_selection_filter_and_persistent_status(self):
        self.load_demo()
        result = self.window.result
        self.assertGreaterEqual(len(result.findings), 2)
        self.assertEqual(len(self.service.store.history()), 1)
        self.assertFalse(result.usage.actual)
        self.assertTrue(self.window.code_editor.isReadOnly())
        self.assertEqual(self.window.findings_table.rowCount(), len(result.findings))
        finding = self.window._selected_finding
        self.assertIn(finding.evidence, self.window.detail_text.toPlainText())
        self.assertEqual(self.window.code_editor.textCursor().blockNumber() + 1, finding.start_line)
        original_code = self.window.code_editor.toPlainText()
        QTest.mouseClick(self.window.accept_button, Qt.MouseButton.LeftButton)
        self.wait_idle()
        self.assertEqual(finding.status, "accepted")
        loaded = self.service.store.load_report(self.window.report_id)
        self.assertTrue(any(item.status == "accepted" for item in loaded.findings))
        self.assertEqual(self.window.code_editor.toPlainText(), original_code)
        self.window.search_input.setText("THIS_SEARCH_HAS_NO_RESULTS")
        self.assertEqual(self.window.findings_table.rowCount(), 0)
        self.assertFalse(self.window.accept_button.isEnabled())
        self.window.search_input.clear()
        self.assertEqual(self.window.findings_table.rowCount(), len(result.findings))
        self.assertEqual(len(self.service.store.history()), 1)

    def test_pasted_code_is_analyzed_after_edits(self):
        QTest.mouseClick(self.window.paste_button, Qt.MouseButton.LeftButton)
        self.assertFalse(self.window.code_editor.isReadOnly())
        self.window.code_editor.setPlainText("def collect(item, values=[]):\n    values.append(item)\n    return values\n")
        QTest.mouseClick(self.window.local_button, Qt.MouseButton.LeftButton)
        self.wait_idle()
        self.assertGreaterEqual(len(self.window.result.findings), 1)
        self.window.code_editor.setPlainText("def collect(item, values=None):\n    if values is None:\n        values = []\n    values.append(item)\n    return values\n")
        self.assertTrue(self.window._dirty)
        self.window.run_local_review()
        self.wait_idle()
        self.assertIn("values=None", self.window.analysis.files[0].content)
        self.assertFalse(any("mutable" in item.rule_id for item in self.window.result.findings))

    def test_graph_nodes_are_clickable_and_zoomable(self):
        self.load_demo()
        self.window.show_page(1)
        QTest.qWait(30)
        graph = self.window.graph_view
        self.assertEqual(len(graph.node_items), len(self.window.analysis.nodes))
        self.assertGreater(graph.shown_edges, 0)
        before = graph.transform().m11()
        graph.zoom(1.2)
        self.assertGreater(graph.transform().m11(), before)
        node = next(iter(graph.node_items.values()))
        point = graph.mapFromScene(node.sceneBoundingRect().center())
        QTest.mouseClick(graph.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(self.window.pages.currentIndex(), 0)
        self.assertEqual(self.window.current_path, node.node.path)
        self.assertEqual(self.window.code_editor.textCursor().blockNumber() + 1, node.node.line)

    def test_preview_cancel_never_calls_model(self):
        self.load_demo()
        self.credentials.key = "test-only-not-a-real-key"
        with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Rejected), patch.object(self.service, "review") as review:
            self.window.run_api_review()
            self.wait_idle()
            review.assert_not_called()
        self.assertIn("没有调用", self.window.notice.text())
        self.assertEqual(self.window._temporary_key, "")

    def test_preview_send_uses_selected_options(self):
        self.load_demo()
        self.credentials.key = "test-only-not-a-real-key"
        result = ReviewResult("mock API report", file_count=1, mode="hybrid", usage=Usage(model="deepseek-flash", prompt_tokens=300, completion_tokens=80, estimated_cost_cny="0.001", actual=True))
        with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Accepted), patch.object(self.service, "review", return_value=result) as review:
            self.window.run_api_review()
            self.wait_idle()
            self.assertTrue(review.call_args.kwargs["use_api"])
            self.assertEqual(review.call_args.kwargs["api_key"], self.credentials.key)
            self.assertEqual(review.call_args.kwargs["model"], "deepseek-flash")
        self.assertIn("380", self.window.cost_hint.text())
        self.assertEqual(self.window.result.mode, "hybrid")

    def test_worker_runs_outside_gui_and_callbacks_return_to_gui(self):
        gui_thread = threading.get_ident()
        observed = []
        self.window._run("thread check", threading.get_ident, lambda worker_id: observed.append((worker_id, threading.get_ident())))
        self.wait_idle()
        self.assertNotEqual(observed[0][0], gui_thread)
        self.assertEqual(observed[0][1], gui_thread)

    def test_failed_worker_redacts_secret_and_can_recover(self):
        def fail():
            raise RuntimeError("Invalid sk-testingSecretOnly123456789")
        self.window._run("failure path", fail)
        self.wait_idle()
        self.assertNotIn("testingSecretOnly", self.window.notice.text())
        self.assertIn("已隐藏密钥", self.window.notice.text())
        self.assertTrue(self.window.local_button.isEnabled())
        self.load_demo()

    def test_credentials_clear_field_and_delete_saved_value(self):
        self.window.show_page(3)
        self.window.api_key_input.setText("test-only-not-a-real-key")
        QTest.mouseClick(self.window.save_key_button, Qt.MouseButton.LeftButton)
        self.wait_idle()
        self.assertEqual(self.credentials.key, "test-only-not-a-real-key")
        self.assertEqual(self.window.api_key_input.text(), "")
        QTest.mouseClick(self.window.delete_key_button, Qt.MouseButton.LeftButton)
        self.wait_idle()
        self.assertEqual(self.credentials.key, "")

    def test_history_open_and_export(self):
        self.load_demo()
        self.window.show_page(2)
        self.wait_idle()
        self.assertEqual(self.window.history_table.rowCount(), 1)
        self.window.history_table.selectRow(0)
        self.window.open_history_report()
        self.wait_idle()
        self.assertEqual(self.window.pages.currentIndex(), 0)
        report = Path(self.directory.name) / "report.json"
        with patch("reviewdesk_ui.ui.QFileDialog.getSaveFileName", return_value=(str(report), "JSON 数据 (*.json)")):
            self.window.export_current()
            self.wait_idle()
        self.assertTrue(report.exists())
        self.assertIn('"schema_version": 1', report.read_text(encoding="utf-8"))

    def test_close_waits_for_worker_without_destroying_thread(self):
        self.window._run("slow work", lambda: time.sleep(0.12))
        self.window.close()
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window._closing)
        self.wait_idle()
        self.assertFalse(self.window.isVisible())

    def test_unknown_api_usage_never_looks_free(self):
        self.window._set_result(ReviewResult("failed API", mode="partial", usage=Usage(model="deepseek-flash", note="请求失败，实际用量未知")))
        self.wait_idle()
        self.assertEqual(self.window.metric_values["cost"].text(), "未知")
        self.assertIn("未知", self.window.cost_hint.text())
        self.assertIn("估算费用：未知", self.window.usage_details.toPlainText())
        self.assertNotIn("输入 0 tokens", self.window.usage_details.toPlainText())
        self.window.refresh_history()
        self.wait_idle()
        self.assertEqual(self.window.history_table.item(0, 5).text(), "未知")
        self.assertIn("1 次费用未知", self.window.history_summary.text())

    def test_edits_during_analysis_are_locked_and_programmatic_revision_survives(self):
        self.window.new_snippet()
        before = "def first():\n    return 1\n"
        after = "def changed():\n    return 2\n"
        self.window.code_editor.setPlainText(before)
        analyze = self.service.analyze_code

        def delayed(*args, **kwargs):
            time.sleep(0.10)
            return analyze(*args, **kwargs)
        with patch.object(self.service, "analyze_code", side_effect=delayed):
            self.window.run_local_review()
            self.assertTrue(self.window.code_editor.isReadOnly())
            QTest.keyClicks(self.window.code_editor, "SHOULD_NOT_APPEAR")
            self.assertEqual(self.window.code_editor.toPlainText(), before)
            # Even a programmatic replacement must not be overwritten by an old snapshot.
            self.window.code_editor.setPlainText(after)
            self.wait_idle()
        self.assertEqual(self.window.code_editor.toPlainText(), after)
        self.assertTrue(self.window._dirty)
        self.assertIsNone(self.window.result)
        self.assertFalse(self.window.code_editor.isReadOnly())

    def test_saved_id_never_attaches_to_another_report(self):
        self.load_demo()
        old = self.window.result
        old_id = self.window.report_id
        self.window.refresh_history()
        self.wait_idle()
        self.window.history_table.selectRow(0)
        new = ReviewResult("new immutable snapshot", file_count=1)
        self.window._set_result(new)
        self.assertIsNotNone(self.window._thread)
        self.window.open_history_report()
        self.assertIs(self.window.result, new)
        self.assertFalse(self.window.open_history_button.isEnabled())
        # Identity guard also protects the callback when a caller replaces the view.
        self.window.result = old
        self.window.report_id = old_id
        self.wait_idle()
        self.assertEqual(self.window.report_id, old_id)
        self.assertEqual(self.service.store.load_report(old_id).project_name, old.project_name)
        records = self.service.store.history()
        self.assertEqual(len(records), 2)
        new_record = next(record for record in records if record["project_name"] == new.project_name)
        self.assertNotEqual(new_record["id"], old_id)
        self.assertEqual(self.service.store.load_report(new_record["id"]).project_name, new.project_name)

    def test_closing_during_paid_review_preserves_returned_usage(self):
        self.window.analysis = self.service.analyze_code("value = 1\n")
        self.window.project_name = "paid review closing regression"
        result = ReviewResult(self.window.project_name, mode="hybrid", file_count=1,
                              usage=Usage(model="deepseek-flash", actual=True, prompt_tokens=123, completion_tokens=45))

        def paid_result(*args, **kwargs):
            time.sleep(0.12)
            return result
        with patch.object(self.service, "review", side_effect=paid_result):
            self.window._perform_review(True, "test-only-not-a-real-key")
            self.window.close()
            self.wait_idle()
        records = self.service.store.history()
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["usage"]["actual"])
        self.assertEqual(records[0]["usage"]["prompt_tokens"], 123)
        self.assertEqual(records[0]["usage"]["completion_tokens"], 45)
        self.assertFalse(self.window.isVisible())

    def test_failed_history_save_keeps_paid_report_exportable_and_retry_avoids_api(self):
        self.window.analysis = self.service.analyze_code("value = 1\n")
        result = ReviewResult("paid save failure", mode="hybrid", file_count=1,
                              usage=Usage(model="deepseek-flash", actual=True, prompt_tokens=234, completion_tokens=56))
        with patch.object(self.service, "review", return_value=result) as review:
            with patch.object(self.service.store, "save_report", side_effect=sqlite3.OperationalError("database is locked")) as save:
                self.window._perform_review(True, "test-only-not-a-real-key")
                self.wait_idle()
                self.assertIs(self.window.result, result)
                self.assertIsNone(self.window.report_id)
                self.assertEqual(self.window.result.usage.prompt_tokens, 234)
                self.assertIn("保存失败", self.window.notice.text())
                self.assertTrue(self.window.retry_save_button.isVisible())
                self.assertEqual(save.call_count, 1)
                exported = Path(self.directory.name) / "unsaved-paid-report.json"
                with patch("reviewdesk_ui.ui.QFileDialog.getSaveFileName", return_value=(str(exported), "JSON 数据 (*.json)")):
                    self.window.export_current()
                    self.wait_idle()
                self.assertIn('"prompt_tokens": 234', exported.read_text(encoding="utf-8"))
                self.assertEqual(save.call_count, 1)
            self.window.retry_save_report()
            self.wait_idle()
            self.assertEqual(review.call_count, 1)
        self.assertIsNotNone(self.window.report_id)
        self.assertFalse(self.window.retry_save_button.isVisible())
        self.assertEqual(len(self.service.store.history()), 1)

    def test_close_is_cancelled_if_paid_report_cannot_be_saved(self):
        self.window.analysis = self.service.analyze_code("value = 1\n")
        result = ReviewResult("paid close save failure", mode="hybrid", file_count=1,
                              usage=Usage(model="deepseek-flash", actual=True, prompt_tokens=345, completion_tokens=67))

        def paid_result(*args, **kwargs):
            time.sleep(0.12)
            return result
        with patch.object(self.service, "review", side_effect=paid_result), patch.object(self.service.store, "save_report", side_effect=OSError("disk full")) as save:
            self.window._perform_review(True, "test-only-not-a-real-key")
            self.window.close()
            self.wait_idle()
            self.assertEqual(save.call_count, 1)
        self.assertFalse(self.window._closing)
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.isEnabled())
        self.assertIs(self.window.result, result)
        self.assertIsNone(self.window.report_id)
        self.assertEqual(self.window.result.usage.prompt_tokens, 345)
        self.assertIn("窗口会保持打开", self.window.notice.text())


if __name__ == "__main__":
    unittest.main()
