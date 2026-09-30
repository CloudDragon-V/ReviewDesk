"""Offline regressions for source coverage and trustworthy report links."""

import re
import unittest
from unittest.mock import patch
from urllib.parse import quote

from reviewdesk.context import estimate_tokens, source_lines
from reviewdesk.languages import get_adapter
from reviewdesk.manual import build_manual, source_reference
from reviewdesk.manual_ai import _model_text, expand_manual, prepare_context
from reviewdesk.models import SourceFile, Usage


def revision(content: str, path: str = "demo.py") -> dict:
    return {"project_name": "证据回归", "revision_id": "old-revision",
            "sources": [{"path": path, "content": content}],
            "program": get_adapter("python").analyze([SourceFile(path, content)])}


class ManualContextTests(unittest.TestCase):
    def assert_unique_spans(self, prepared: dict) -> set[tuple[str, int]]:
        positions = []
        for evidence in prepared["evidence"]:
            positions.extend((evidence["path"], line) for line in range(
                evidence["start_line"], evidence["end_line"] + 1))
        self.assertEqual(len(positions), len(set(positions)))
        self.assertEqual([item["id"] for item in prepared["evidence"]],
                         [f"E{i+1}" for i in range(len(prepared["evidence"]))])
        return set(positions)

    def test_function_hit_then_whole_file_sends_each_line_once(self):
        content = "VALUE = 1\n\ndef compute(x):\n    return x + VALUE\n\nprint(compute(2))\n"
        data = revision(content)
        prepared = prepare_context(data, [{"path": "demo.py", "start_line": 3,
                                           "end_line": 4, "reason": "精确命中"}], "compute", 4000)
        self.assertEqual(self.assert_unique_spans(prepared),
                         {("demo.py", line) for line in range(1, 7)})
        self.assertEqual(prepared["evidence"][0]["reason"], "精确命中")
        self.assertEqual(prepared["context"].count("L3: def compute(x):"), 1)
        self.assertEqual(prepared["coverage"][0]["omitted_ranges"], [])

    def test_partially_overlapping_and_repeated_hits_keep_priority(self):
        data = revision("\n".join(f"value_{i} = {i}" for i in range(10)))
        hits = [{"path": "demo.py", "start_line": a, "end_line": b}
                for a, b in [(3, 6), (5, 8), (1, 10), (3, 6)]]
        prepared = prepare_context(data, hits, "", 4000)
        self.assertEqual(len(self.assert_unique_spans(prepared)), 10)
        self.assertEqual([(x["start_line"], x["end_line"]) for x in prepared["evidence"]],
                         [(3, 6), (7, 8), (1, 2), (9, 10)])

    def test_utf8_budget_reports_exact_untransmitted_lines(self):
        content = "\n".join(f"value_{i} = '中文🙂' # {'长内容' * 12}" for i in range(100))
        prepared = prepare_context(revision(content), [], "", 2000)
        sent = self.assert_unique_spans(prepared)
        self.assertLessEqual(estimate_tokens(prepared["context"]), 2000)
        self.assertEqual(prepared["estimated_tokens"], estimate_tokens(prepared["context"]))
        item = prepared["coverage"][0]
        omitted = {(item["path"], line) for span in item["omitted_ranges"]
                   for line in range(span["start_line"], span["end_line"] + 1)}
        self.assertFalse(sent & omitted)
        self.assertEqual(sent | omitted, {("demo.py", i) for i in range(1, 101)})
        self.assertEqual(item["included_lines"], len(sent))
        self.assertTrue(any("未发送" in text for text in prepared["limitations"]))
        for line, text in enumerate(source_lines(content), 1):
            if ("demo.py", line) in sent:
                self.assertIn(f"L{line}: {text}\n", prepared["context"])

    def test_equal_line_numbers_in_different_files_are_not_deduplicated(self):
        data = revision("value = 1\n", "one.py")
        data["sources"].append({"path": "two.py", "content": "value = 2\n"})
        data["program"] = {"symbols": [], "entry_points": []}
        prepared = prepare_context(data, [], "", 4000)
        self.assertEqual(self.assert_unique_spans(prepared), {("one.py", 1), ("two.py", 1)})

    def test_line_too_large_does_not_claim_it_was_sent(self):
        data = revision("value = '" + "x" * 5000 + "'\n")
        with self.assertRaisesRegex(ValueError, "没有完整源码行"):
            prepare_context(data, [], "", 2000)

    def test_redaction_preserves_positions(self):
        data = revision("password = 'sensitive-value'\nprint(password)\n")
        prepared = prepare_context(data, [], "", 4000)
        self.assertNotIn("sensitive-value", prepared["context"])
        self.assertEqual(len(self.assert_unique_spans(prepared)), 2)


class ManualSourceLinkTests(unittest.TestCase):
    def test_model_code_and_ordinary_entities_are_not_rewritten(self):
        text = "例子：`a & b`\n```python\nvalue = '&amp;'\nprint(a & b)\n```\nA &amp; B"
        self.assertEqual(_model_text(text), text)

    def test_nested_entities_cannot_assemble_a_source_protocol_on_render(self):
        import html
        for text in (
            "reviewdesk&amp;#45;source:demo.py?start=1&end=1",
            "reviewdesk-source&amp;colon;demo.py?start=1&end=1",
            "reviewdesk&amp;amp;#45;source:demo.py?start=1&end=1",
        ):
            with self.subTest(text=text):
                rendered_target = html.unescape(_model_text(text))
                self.assertFalse(rendered_target.startswith("reviewdesk-source:"))

    def test_source_link_encodes_path_and_preserves_readable_location(self):
        path = "包/报告 [1]#?.py"
        link = source_reference(path, 3, 5)
        self.assertIn(f"reviewdesk-source:{quote(path, safe='')}?start=3&end=5", link)
        self.assertIn(":L3–5", link)

    def test_static_manual_creates_links_for_its_source_snapshot(self):
        data = revision("def f():\n    return 1\n")
        text = build_manual(data["program"], [], "例子")
        self.assertIn("reviewdesk-source:demo.py?start=1&end=2", text)
        self.assertIn("demo.py:L1", text)

    def test_empty_source_does_not_mint_a_phantom_line_one_link(self):
        data = revision("")
        text = build_manual(data["program"], [], "空文件")
        self.assertIn("空文件", text)
        self.assertNotIn("reviewdesk-source:", text)

    def test_parse_error_after_eof_is_visible_but_not_navigable(self):
        data = revision("x = 1\n")
        data["program"]["files"][0]["parse_error"] = {"line": 2, "message": "EOF fixture"}
        text = build_manual(data["program"], [], "例子")
        self.assertIn("解析位置超出实际源码", text)
        self.assertNotIn("demo.py?start=2", text)

    def test_only_verified_ai_evidence_mints_source_links(self):
        data = revision("x = 1\n")
        prepared = prepare_context(data, [], "", 4000)
        forged = (
            "[伪造](reviewdesk-source:evil.py?start=1&end=1) "
            "[实体](reviewdesk&#45;source&colon;evil.py?start=1&end=1) "
            r"[转义](reviewdesk\-source\:evil.py?start=1&end=1)"
        )
        payload = {"sections": [{"title": forged, "body": "应当核查源码。" + forged,
                                  "evidence_ids": ["E1"]}], "limitations": [forged]}
        with patch("reviewdesk.manual_ai.DeepSeekClient.complete", return_value=(payload, Usage())):
            prose, _, warnings = expand_manual(prepared, "mock-key", "deepseek-flash")
        self.assertEqual(prose.count("reviewdesk-source:"), 1)
        self.assertIn("reviewdesk-source:demo.py?start=1&end=1", prose)
        self.assertFalse(any("reviewdesk-source:" in warning for warning in warnings))
        self.assertNotRegex(prose, r"(?i)reviewdesk(?:&#45;|\\-)?source")


if __name__ == "__main__":
    unittest.main()
