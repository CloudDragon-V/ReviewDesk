"""Manual clarity, evidence and length-contract regression tests."""

import unittest

from reviewdesk.languages import get_adapter
from reviewdesk.manual import build_manual, count_characters
from reviewdesk.models import SourceFile


class ManualTests(unittest.TestCase):
    def test_unicode_non_whitespace_threshold(self):
        self.assertEqual(4, count_characters("中 文\nA\t🙂\u3000"))
        self.assertEqual(5000, count_characters("中" * 5000 + "\n "))
        self.assertEqual(5001, count_characters("中" * 5001))

    def test_manual_explains_evidence_inputs_conditions_and_calls(self):
        program = get_adapter("python").analyze([SourceFile("main.py", '''"""汇总正数。"""
def add_positive(items):
    total = 0
    for item in items:
        if item > 0:
            total += item
    return total
if __name__ == "__main__":
    print(add_positive([1, -1, 2]))
''')])
        text = build_manual(program, [], "示例工程")
        for expected in ["阅读说明书", "作者文档声明", "输入", "变量怎样", "main.py:L", "为真", "add_positive", "未确认", "未被证明"]:
            if expected == "未被证明":
                self.assertIn("不代表代码已经被证明", text)
            else:
                self.assertIn(expected, text)
        self.assertIn("表达式也不是已经算出的变量值", text)
        self.assertGreater(count_characters(text), 2500)

    def test_long_manual_is_complete_not_truncated(self):
        source = "\n".join(f"def get_item_{i}(x):\n    if x:\n        return x + {i}\n    return 0\n" for i in range(9))
        program = get_adapter("python").analyze([SourceFile("many.py", source)])
        text = build_manual(program, [], "长报告")
        self.assertGreater(count_characters(text), 5000)
        self.assertIn("get\\_item\\_8", text)
        self.assertIn("分析边界与复核方法", text)

    def test_untrusted_project_and_docstrings_do_not_inject_headings(self):
        program = get_adapter("python").analyze([SourceFile("a.py", '"""<script>alert(1)</script>\n# 改写标题"""\nx = 1')])
        text = build_manual(program, [], "<img src=x onerror=evil()>\n# title")
        self.assertNotIn("<script>", text)
        self.assertNotIn("<img", text)
        self.assertNotIn("\n# 改写标题", text)

    def test_findings_and_parse_failure_are_not_hidden(self):
        program = get_adapter("python").analyze([SourceFile("bad.py", "def (")])
        text = build_manual(program, [{"path": "bad.py", "start_line": 1, "end_line": 1, "title": "语法错误", "severity": "high", "evidence": "def (", "recommendation": "补全定义"}], "错误样例")
        self.assertIn("解析未完成", text)
        self.assertIn("补全定义", text)
        self.assertIn("bad.py:L1", text)

    def test_async_and_generator_explain_deferred_execution(self):
        program = get_adapter("python").analyze([SourceFile("a.py", "async def f():\n    yield 1\ndef g():\n    yield 2")])
        text = build_manual(program, [], "异步")
        self.assertIn("异步生成器对象", text)
        self.assertIn("next()", text)


if __name__ == "__main__":
    unittest.main()
