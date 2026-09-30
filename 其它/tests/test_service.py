import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from reviewdesk.api import ApiError
from reviewdesk.models import Analysis, Context, Finding, ReviewResult, SourceFile, Usage
from reviewdesk.service import ReviewService, export_report, merge_findings, scrub, validate_findings
from reviewdesk.storage import ReportStore


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.analysis = Analysis(files=[SourceFile("demo.py", "def go(x):\n    return eval(x)\n")])
        self.context = Context("demo.py\n1 | def go(x):\n2 |     return eval(x)", 40, {"demo.py": [(1, 2)]})
        self.valid = {"path": "demo.py", "start_line": 2, "end_line": 2,
                      "category": "correctness", "severity": "high", "confidence": 0.9,
                      "title": "动态执行输入", "evidence": "eval(x)", "recommendation": "校验数据并避免执行。"}

    def test_grounded_candidate(self):
        accepted, warnings = validate_findings({"findings": [self.valid]}, self.analysis, self.context)
        self.assertEqual(len(accepted), 1)
        self.assertFalse(warnings)

    def test_untrusted_positions_and_content(self):
        changes = [{"path": "../demo.py"}, {"path": "C:/demo.py"}, {"path": "ghost.py"},
                   {"start_line": True}, {"end_line": 99}, {"severity": "urgent"},
                   {"confidence": float("nan")}, {"confidence": 10**400}, {"evidence": "fabricated()"}, {"evidence": "x"},
                   {"recommendation": ["not string"]}, {"category": "unknown"}]
        for change in changes:
            with self.subTest(change=change):
                accepted, warnings = validate_findings({"findings": [self.valid | change]}, self.analysis, self.context)
                self.assertFalse(accepted)
                self.assertTrue(warnings)

    def test_existing_but_unsent_lines_rejected(self):
        self.context.included_spans = {"demo.py": [(1, 1)]}
        accepted, _ = validate_findings({"findings": [self.valid]}, self.analysis, self.context)
        self.assertFalse(accepted)

    def test_secret_redaction(self):
        text = 'api_key = "' + 'sk-' + 'x' * 32 + '"\npassword = "something-private"'
        self.assertNotIn("something-private", scrub(text))
        self.assertNotIn("x" * 32, scrub(text))

    def test_duplicate_defect_with_different_span_is_merged(self):
        local = Finding("a.py", 4, 5, "correctness", "medium", "裸 except 捕获所有异常",
                        "    except:\n        return -1", "fix", rule_id="PY003")
        model = Finding("a.py", 2, 4, "correctness", "medium", "裸 except 掩盖异常",
                        "    except:", "fix", source="llm")
        findings, count = merge_findings([local], [model])
        self.assertEqual((len(findings), count), (1, 1))

    def test_overlap_does_not_merge_different_defects(self):
        local = Finding("a.py", 1, 2, "correctness", "high", "非空元组使断言恒为真",
                        "assert (eval(text), 'message')", "fix", rule_id="PY010")
        model = Finding("a.py", 1, 1, "correctness", "high", "动态执行不可信输入",
                        "eval(text)", "fix", source="llm")
        findings, count = merge_findings([local], [model])
        self.assertEqual((len(findings), count), (2, 0))

    def test_identical_excerpt_does_not_prove_same_defect(self):
        excerpt = "def go(items=[]): return eval(text)"
        local = Finding("a.py", 1, 1, "correctness", "medium", "可变对象作为默认参数", excerpt, "fix", rule_id="PY002")
        other = Finding("a.py", 1, 1, "correctness", "high", "动态执行不可信代码", excerpt, "fix", source="llm")
        findings, count = merge_findings([local], [other])
        self.assertEqual((len(findings), count), (2, 0))

    def test_default_expression_execution_is_not_mutable_default(self):
        excerpt = "def go(items=[], value=eval(text)):"
        local = Finding("a.py", 1, 1, "correctness", "medium", "可变对象作为默认参数", excerpt, "fix", rule_id="PY002")
        other = Finding("a.py", 1, 1, "correctness", "high", "default argument evaluates untrusted input", "eval(text)", "fix", source="llm")
        findings, count = merge_findings([local], [other])
        self.assertEqual((len(findings), count), (2, 0))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ReportStore(Path(self.tmp.name) / "history.db")
        self.service = ReviewService(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_offline_never_calls_api(self):
        with patch("reviewdesk.service.DeepSeekClient") as client:
            result = self.service.review(self.service.analyze_code("value = 1"), "test")
        client.assert_not_called()
        self.assertFalse(result.usage.actual)

    def test_budget_blocks_before_network(self):
        with patch("reviewdesk.service.DeepSeekClient") as client:
            with self.assertRaises(ValueError):
                self.service.review(self.service.analyze_code("value = 1"), "test", True,
                                    "test-only", max_cost_cny="0.000001")
        client.assert_not_called()

    def test_failure_returns_local_and_usage(self):
        usage = Usage(prompt_tokens=71, completion_tokens=18, actual=True)
        with patch("reviewdesk.service.DeepSeekClient.complete", side_effect=ApiError("format", "截断", usage)):
            result = self.service.review(self.service.analyze_code("value = 1"), "test", True, "test-only")
        self.assertEqual(result.mode, "partial")
        self.assertEqual(result.usage.prompt_tokens, 71)
        self.assertTrue(any("截断" in x for x in result.limitations))

    def test_persist_export_triage(self):
        result = ReviewResult("测试项目", findings=[Finding("a.py", 1, 1, "correctness", "high", "title", "code", "fix")])
        report_id = self.store.save_report(result)
        result.findings[0].status = "dismissed"
        self.store.update_report(report_id, result)
        loaded = self.store.load_report(report_id)
        self.assertEqual(loaded.findings[0].status, "dismissed")
        self.assertEqual(self.store.history()[0]["finding_count"], 1)
        for suffix in (".json", ".md"):
            path = Path(self.tmp.name) / ("report" + suffix)
            export_report(loaded, path)
            self.assertIn("测试项目", path.read_text(encoding="utf-8"))
        self.assertNotIn("content", str(asdict(loaded)))

    def test_unknown_usage_export_is_not_zero(self):
        result = ReviewResult("test", mode="partial", usage=Usage(note="未知"))
        path = Path(self.tmp.name) / "unknown.md"
        export_report(result, path)
        text = path.read_text(encoding="utf-8")
        self.assertIn("估算费用：未知", text)
        self.assertNotIn("¥0", text)
        self.assertNotIn("输入 0", text)


if __name__ == "__main__":
    unittest.main()
