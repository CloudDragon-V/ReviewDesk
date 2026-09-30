"""User-visible reading flows and failure boundaries against real SQLite/HTTP."""

from contextlib import closing
from dataclasses import asdict
from pathlib import Path
import json
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from reviewdesk.api import ApiError
from reviewdesk.models import Usage
from reviewdesk.web_api import create_app
from reviewdesk.workbench_store import WorkbenchStore


class WebWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.front = self.root / "web"
        self.front.mkdir()
        (self.front / "index.html").write_text("<!doctype html><html>ReviewDesk</html>", encoding="utf-8")
        self.credentials = patch("reviewdesk.storage.CredentialStore.get_key", return_value="")
        self.credentials.start()
        self.app = create_app(self.root / "data", self.front)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        self.client.__enter__()
        self.headers = {"X-ReviewDesk-Token": self.client.get("/api/bootstrap").json()["token"]}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.credentials.stop()
        self.temp.cleanup()

    def post(self, path, body):
        return self.client.post(path, json=body, headers=self.headers)

    def get(self, path):
        return self.client.get(path, headers=self.headers)

    def finished(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        identifier = response.json()["job_id"]
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            state = self.get("/api/jobs/"+identifier).json()
            if state["status"] not in {"queued", "running"}:
                self.assertEqual(state["status"], "succeeded", state)
                return state["result"]
            time.sleep(.02)
        self.fail("job timed out")

    def demo(self):
        return self.finished(self.post("/api/demo", {}))["revision_id"]

    def test_full_demo_read_search_manual_auto_document_download(self):
        revision = self.demo()
        data = self.get("/api/revisions/"+revision).json()
        self.assertEqual(len(data["sources"]), 3)
        self.assertTrue(any(c["resolved"] and c["callee"].endswith("calculate_order") for c in data["program"]["calls"]))
        results = self.get(f"/api/search?revision_id={revision}&q=金额如何计算").json()["results"]
        self.assertTrue(any("calculate_order" in r["name"] for r in results))
        generated = self.finished(self.post("/api/manual", {"revision_id": revision}))
        manual = self.get("/api/manuals/"+generated["manual_id"]).json()
        self.assertGreater(manual["character_count"], 5000)
        self.assertTrue(manual["auto_exported"])
        self.assertEqual(manual["export_status"], "saved")
        self.assertEqual(manual["export_error"], "")
        self.assertEqual(Path(manual["document_path"]).read_text(encoding="utf-8"), manual["markdown"])
        self.assertIn("subtotal", manual["markdown"])
        self.assertIn("变量", manual["markdown"])
        downloaded = self.client.get(manual["download_url"]+"?token="+self.headers["X-ReviewDesk-Token"])
        self.assertEqual(downloaded.text, manual["markdown"])
        self.assertIn("attachment", downloaded.headers["content-disposition"])
        self.assertFalse(manual["usage"]["actual"])

    def test_loopback_token_and_cross_origin_boundary(self):
        self.assertEqual(self.client.get("/api/projects").status_code, 403)
        self.assertEqual(self.client.get("/api/projects", headers={b"X-ReviewDesk-Token":b"\xe9"}).status_code,403)
        self.assertEqual(self.client.get("/api/bootstrap", headers={"Origin": "https://other.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/bootstrap", headers={"Host": "evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/bootstrap", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(self.client.post("/api/demo", data="{}", headers=self.headers).status_code, 415)
        self.assertIn("frame-ancestors 'none'", self.client.get("/").headers["content-security-policy"])

    def test_path_language_size_and_duplicate_validation(self):
        for files in ([{"path":"../escape.py","content":"x=1"}],
                      [{"path":"x.js","content":"x=1"}],
                      [{"path":"x.py","content":"x=1"},{"path":"X.py","content":"x=2"}]):
            self.assertEqual(self.post("/api/import", {"files":files}).status_code, 400)
        response = self.post("/api/import", {"language":"javascript","files":[{"path":"x.py","content":"x=1"}]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("暂不支持", response.text)
        response = self.post("/api/settings/key", {"key":"PRIVATE"*100})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("PRIVATE", response.text)
        response = self.client.post("/api/demo", content=b"{}", headers={**self.headers,"Content-Type":"application/json","Content-Length":"999999999"})
        self.assertEqual(response.status_code, 413)

    def test_secret_redacted_before_snapshot_and_manual(self):
        secret = "sk-"+"Q"*30
        revision = self.finished(self.post("/api/import", {"files":[{"path":"test.py","content":f'api_key = "{secret}"\nprint(api_key)\n'}]}))["revision_id"]
        data = self.get("/api/revisions/"+revision)
        self.assertNotIn(secret, data.text)
        self.assertIn("REDACTED", data.text)

    def test_ai_send_requires_exact_preview_and_preserves_usage(self):
        revision = self.demo()
        self.app.state.workspace.session_key = "test-key"
        request = {"revision_id":revision,"use_ai":True,"focus":"金额如何变化"}
        self.assertEqual(self.post("/api/manual", request).status_code, 400)
        preview = self.post("/api/manual/preview", request).json()
        self.assertLessEqual(preview["estimated_tokens"], 24000)
        self.assertIn('"source_catalog"',preview["context"])
        self.assertIn('"total_lines"',preview["context"])
        request["preview_id"] = preview["preview_id"]
        changed = {**request,"focus":"另一个问题"}
        self.assertEqual(self.post("/api/manual", changed).status_code, 400)
        usage = Usage(model="deepseek-flash", prompt_tokens=123, completion_tokens=321, actual=True, estimated_cost_cny="0.001")
        payload = {"findings":[],"sections":[{"title":"金额如何产生","body":"根据提供的源码，金额在循环体中逐项累计；实际数值取决于调用时传入的商品列表，不能把静态结构看作实际执行记录。","evidence_ids":["E1"]}]}
        with patch("reviewdesk.manual_ai.DeepSeekClient.complete", return_value=(payload,usage)) as complete:
            result = self.finished(self.post("/api/manual", request))
            self.assertEqual(complete.call_count, 1)
        manual = self.get("/api/manuals/"+result["manual_id"]).json()
        self.assertEqual(manual["mode"], "ai")
        self.assertEqual(manual["usage"]["prompt_tokens"],123)
        self.assertIn("DeepSeek 辅助讲解", manual["markdown"])
        self.assertLess(manual["markdown"].index("## DeepSeek 辅助讲解"),manual["markdown"].index("## 静态结构与逐项源码索引"))
        self.assertEqual(self.post("/api/manual", request).status_code, 400)

    def test_ai_failure_retains_local_manual_and_known_usage(self):
        revision = self.demo()
        self.app.state.workspace.session_key = "test-key"
        request = {"revision_id":revision,"use_ai":True}
        request["preview_id"] = self.post("/api/manual/preview", request).json()["preview_id"]
        usage = Usage(model="deepseek-flash",actual=True,prompt_tokens=300,completion_tokens=500,estimated_cost_cny="0.004")
        with patch("reviewdesk.manual_ai.DeepSeekClient.complete", side_effect=ApiError("format","输出被截断。",usage)) as complete:
            result = self.finished(self.post("/api/manual", request))
            self.assertEqual(complete.call_count,1)
        manual = self.get("/api/manuals/"+result["manual_id"]).json()
        self.assertEqual(manual["mode"],"partial")
        self.assertTrue(manual["usage"]["actual"])
        self.assertGreater(manual["character_count"],5000)
        self.assertIn("输出被截断",manual["markdown"])

    def test_bad_ai_evidence_rejected_not_presented_as_valid(self):
        revision=self.demo()
        self.app.state.workspace.session_key="test-key"
        request={"revision_id":revision,"use_ai":True}
        request["preview_id"]=self.post("/api/manual/preview",request).json()["preview_id"]
        payload={"findings":[],"sections":[{"title":"不存在","body":"胡乱编造的事实"*20,"evidence_ids":["E9999"]}]}
        with patch("reviewdesk.manual_ai.DeepSeekClient.complete",return_value=(payload,Usage(actual=True))):
            result=self.finished(self.post("/api/manual",request))
        manual=self.get("/api/manuals/"+result["manual_id"]).json()
        self.assertEqual(manual["mode"],"partial")
        self.assertNotIn("胡乱编造的事实",manual["markdown"])

    def test_oversized_manual_blocked_before_paid_call(self):
        revision=self.demo()
        self.app.state.workspace.session_key="test-key"
        request={"revision_id":revision,"use_ai":True}
        request["preview_id"]=self.post("/api/manual/preview",request).json()["preview_id"]
        with patch("reviewdesk.manual.build_manual",return_value="字"*1_600_001), patch("reviewdesk.manual_ai.DeepSeekClient.complete") as paid:
            identifier=self.post("/api/manual",request).json()["job_id"]
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                state=self.get("/api/jobs/"+identifier).json()
                if state["status"] not in {"queued","running"}: break
                time.sleep(.02)
            self.assertEqual(state["status"],"failed")
            self.assertIn("尚未产生",state["error"])
            paid.assert_not_called()

    def test_usage_receipt_survives_database_insert_failure(self):
        revision=self.demo()
        self.app.state.workspace.session_key="test-key"
        request={"revision_id":revision,"use_ai":True}
        request["preview_id"]=self.post("/api/manual/preview",request).json()["preview_id"]
        usage=asdict(Usage(actual=True,prompt_tokens=120,completion_tokens=800,estimated_cost_cny="0.01"))
        with self.app.state.workspace.store.connect() as db:
            db.execute("CREATE TRIGGER reject_manual BEFORE INSERT ON manuals "
                       "BEGIN SELECT RAISE(ABORT, 'insert blocked'); END")
        with patch("reviewdesk.manual_ai.expand_manual",return_value=("详细中文讲解",usage,[])), patch("reviewdesk.workbench_store.Path.write_text") as write:
            identifier=self.post("/api/manual",request).json()["job_id"]
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                state=self.get("/api/jobs/"+identifier).json()
                if state["status"] not in {"queued","running"}: break
                time.sleep(.02)
            self.assertEqual(state["status"],"failed")
            self.assertEqual(state["usage"]["completion_tokens"],800)
            self.assertTrue(state["usage"]["actual"])
            self.assertEqual(state["request_status"],"response_received")
            write.assert_not_called()
        self.assertEqual(self.get("/api/manuals?revision_id="+revision).json()["manuals"], [])

    def test_failed_file_export_keeps_paid_body_downloadable_and_retry_never_calls_model(self):
        revision = self.demo()
        self.app.state.workspace.session_key = "test-key"
        usage = Usage(actual=True, prompt_tokens=120, completion_tokens=800, estimated_cost_cny="0.01")
        payload = {"sections": [{"title": "金额说明", "body": "模型返回的已付费正文应完整保留。" * 12,
                                 "evidence_ids": ["E1"]}]}
        for operation in ("write_text", "replace"):
            with self.subTest(operation=operation):
                request = {"revision_id": revision, "use_ai": True}
                request["preview_id"] = self.post("/api/manual/preview", request).json()["preview_id"]
                with patch("reviewdesk.manual_ai.DeepSeekClient.complete", return_value=(payload, usage)) as paid:
                    with patch("reviewdesk.workbench_store.Path." + operation,
                               side_effect=PermissionError("private filesystem detail")):
                        submitted = self.post("/api/manual", request)
                        result = self.finished(submitted)
                        self.assertEqual(result["export_status"], "failed")
                        manual = self.get("/api/manuals/" + result["manual_id"]).json()
                        self.assertFalse(manual["auto_exported"])
                        self.assertIsNone(manual["document_path"])
                        self.assertIn(payload["sections"][0]["body"], manual["markdown"])
                        self.assertNotIn("private", manual["export_error"])
                        self.assertEqual(manual["usage"]["completion_tokens"], 800)
                        state = self.get("/api/jobs/" + submitted.json()["job_id"]).json()
                        self.assertIn("自动导出失败", state["message"])
                        self.assertEqual(state["usage"]["completion_tokens"], 800)
                        downloaded = self.get(manual["download_url"])
                        self.assertEqual(downloaded.text, manual["markdown"])
                        failed_again = self.post("/api/manuals/" + manual["id"] + "/export", {})
                        self.assertEqual(failed_again.status_code, 200)
                        self.assertEqual(failed_again.json()["export_status"], "failed")
                    # Reopening SQLite proves retention is independent of process memory.
                    reopened = WorkbenchStore(self.root / "data").get_manual(manual["id"])
                    self.assertEqual(reopened["markdown"], manual["markdown"])
                    retried = self.post("/api/manuals/" + manual["id"] + "/export", {})
                    self.assertEqual(retried.status_code, 200)
                    exported = retried.json()
                    self.assertEqual(exported["export_status"], "saved")
                    self.assertEqual(exported["export_error"], "")
                    self.assertTrue(exported["auto_exported"])
                    self.assertEqual(Path(exported["document_path"]).read_text(encoding="utf-8"), manual["markdown"])
                    self.assertEqual(exported["usage"], manual["usage"])
                    paid.assert_called_once()
                history = self.get("/api/manuals?revision_id=" + revision).json()["manuals"]
                self.assertEqual(next(row for row in history if row["id"] == manual["id"])["export_status"], "saved")

    def test_export_rejects_invalid_missing_ids_and_caller_supplied_paths(self):
        for identifier in ("not-an-id", "A" * 32, "0" * 32):
            response = self.post("/api/manuals/" + identifier + "/export", {})
            self.assertEqual(response.status_code, 400)
        response = self.post("/api/manuals/" + "0" * 32 + "/export", {"path": "../outside.md"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.post("/api/manuals/" + "0" * 32 + "/export", json={}).status_code, 403)

    def test_job_history_exposes_interruption_and_usage_without_resubmitting(self):
        store = self.app.state.workspace.store
        identifier = store.create_job("manual")
        store.update_job(identifier, "running", 55, "请求模型")
        usage = asdict(Usage(actual=True, prompt_tokens=120, completion_tokens=800))
        store.record_usage(identifier, "response_received", usage)
        store.recover_jobs()
        with patch("reviewdesk.manual_ai.DeepSeekClient.complete") as paid:
            response = self.get("/api/jobs?limit=1")
            self.assertEqual(response.status_code, 200)
            row = response.json()["jobs"][0]
            self.assertEqual(row["id"], identifier)
            self.assertEqual(row["status"], "interrupted")
            self.assertEqual(row["usage"], usage)
            self.assertEqual(row["request_status"], "response_received")
            paid.assert_not_called()
        self.assertEqual(self.get("/api/jobs?limit=101").status_code, 422)
        self.assertEqual(self.get("/api/jobs?limit=0").status_code, 422)

    def test_version_history_delete_cleans_auto_documents(self):
        revision=self.demo()
        original=self.get("/api/revisions/"+revision).json()
        result=self.finished(self.post("/api/manual",{"revision_id":revision}))
        manual=self.get("/api/manuals/"+result["manual_id"]).json()
        deleted=self.client.delete("/api/projects/"+original["project_id"],headers=self.headers)
        self.assertEqual(deleted.status_code,200,deleted.text)
        self.assertFalse(Path(manual["document_path"]).exists())
        self.assertEqual(self.get("/api/revisions/"+revision).status_code,400)
        self.assertEqual(self.get("/api/manuals/"+manual["id"]).status_code,400)

    def test_locked_document_keeps_project_and_manual_retryable(self):
        revision=self.demo()
        original=self.get("/api/revisions/"+revision).json()
        result=self.finished(self.post("/api/manual",{"revision_id":revision}))
        manual=self.get("/api/manuals/"+result["manual_id"]).json()
        with patch("reviewdesk.workbench_store.Path.unlink",side_effect=PermissionError("locked")):
            response=self.client.delete("/api/projects/"+original["project_id"],headers=self.headers)
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.get("/api/revisions/"+revision).status_code,200)
        self.assertEqual(self.get("/api/manuals/"+manual["id"]).status_code,200)
        self.assertTrue(Path(manual["document_path"]).is_file())
        self.assertEqual(self.client.delete("/api/projects/"+original["project_id"],headers=self.headers).status_code,200)
        self.assertFalse(Path(manual["document_path"]).exists())


class ManualPersistenceTests(unittest.TestCase):
    def test_exact_5000_boundary_and_manual_download_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            store=WorkbenchStore(Path(temporary))
            for count,automatic in [(4999,False),(5000,False),(5001,True)]:
                manual=store.save_manual("revision","project","字"*count,"local",asdict(Usage()),[])
                self.assertEqual(manual["character_count"],count)
                self.assertEqual(manual["auto_exported"],automatic)
                self.assertEqual(manual["export_status"], "saved" if automatic else "not_required")
                self.assertEqual(store.document_path(manual["id"]).exists(),automatic)
                retried = store.export_manual(manual["id"])
                self.assertEqual(retried["export_status"], manual["export_status"])
                self.assertEqual(store.document_path(manual["id"]).exists(), automatic)

    def test_old_schema_migration_preserves_reports_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            usage = asdict(Usage(actual=True, prompt_tokens=7, completion_tokens=9))
            with closing(sqlite3.connect(root / "workbench.db")) as db, db:
                db.execute("CREATE TABLE manuals (id TEXT PRIMARY KEY, revision_id TEXT NOT NULL, "
                           "project_name TEXT NOT NULL, markdown TEXT NOT NULL, character_count INTEGER NOT NULL, "
                           "auto_exported INTEGER NOT NULL, mode TEXT NOT NULL, usage TEXT NOT NULL, "
                           "limitations TEXT NOT NULL, created_at TEXT NOT NULL)")
                for identifier, count, saved in (("a" * 32, 5001, 1), ("b" * 32, 5000, 0), ("c" * 32, 5001, 0)):
                    db.execute("INSERT INTO manuals VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (identifier, "revision", "project", "字" * count, count, saved, "ai",
                                json.dumps(usage), "[]", "2026-09-20T00:00:00Z"))
            (root / "manuals").mkdir()
            saved_path = root / "manuals" / ("代码说明书_" + "a" * 32 + ".md")
            saved_path.write_text("字" * 5001, encoding="utf-8")
            for _ in range(2):
                store = WorkbenchStore(root)
                for identifier, status in (("a" * 32, "saved"), ("b" * 32, "not_required"), ("c" * 32, "failed")):
                    manual = store.get_manual(identifier)
                    self.assertEqual(manual["export_status"], status)
                    self.assertEqual(manual["usage"], usage)
                    self.assertEqual(manual["markdown"], "字" * manual["character_count"])
                self.assertIn("重试", store.get_manual("c" * 32)["export_error"])
                self.assertEqual(saved_path.read_text(encoding="utf-8"), "字" * 5001)

    def test_database_insert_failure_does_not_create_an_orphan_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = WorkbenchStore(Path(temporary))
            with store.connect() as db:
                db.execute("CREATE TRIGGER reject_manual BEFORE INSERT ON manuals "
                           "BEGIN SELECT RAISE(ABORT, 'insert blocked'); END")
            with patch("reviewdesk.workbench_store.Path.write_text") as write:
                with self.assertRaises(sqlite3.IntegrityError):
                    store.save_manual("revision", "project", "字" * 5001, "local", asdict(Usage()), [])
                write.assert_not_called()
            self.assertEqual(store.list_manuals("revision"), [])
            self.assertEqual(list(store.documents.glob("*")), [])

    def test_unavailable_export_directory_does_not_block_report_reopening(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "manuals").write_text("existing unrelated file", encoding="utf-8")
            store = WorkbenchStore(root)
            manual = store.save_manual("revision", "project", "字" * 5001, "local", asdict(Usage()), [])
            self.assertEqual(manual["export_status"], "failed")
            self.assertEqual(WorkbenchStore(root).get_manual(manual["id"])["markdown"], "字" * 5001)
            self.assertEqual((root / "manuals").read_text(encoding="utf-8"), "existing unrelated file")

    def test_export_status_database_failure_still_preserves_committed_body(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = WorkbenchStore(Path(temporary))
            with store.connect() as db:
                db.execute("CREATE TRIGGER reject_export_status BEFORE UPDATE ON manuals "
                           "BEGIN SELECT RAISE(ABORT, 'update blocked'); END")
            usage = asdict(Usage(actual=True, prompt_tokens=11, completion_tokens=12))
            with self.assertRaises(sqlite3.IntegrityError):
                store.save_manual("revision", "project", "字" * 5001, "ai", usage, [])
            identifier = store.list_manuals("revision")[0]["id"]
            manual = store.get_manual(identifier)
            self.assertEqual(manual["markdown"], "字" * 5001)
            self.assertEqual(manual["usage"], usage)
            self.assertEqual(manual["export_status"], "failed")
            with store.connect() as db:
                db.execute("DROP TRIGGER reject_export_status")
            self.assertEqual(store.export_manual(identifier)["export_status"], "saved")

    def test_restart_marks_interrupted_not_success_or_automatic_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            store=WorkbenchStore(Path(temporary))
            identifier=store.create_job("manual")
            store.update_job(identifier,"running",50,"请求模型")
            again=WorkbenchStore(Path(temporary))
            again.recover_jobs()
            self.assertEqual(again.get_job(identifier)["status"],"interrupted")
            self.assertIn("核对平台用量",again.get_job(identifier)["error"])


if __name__ == "__main__":
    unittest.main()
