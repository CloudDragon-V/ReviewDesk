"""Context invariants: budgets, grounding, deterministic coverage, redaction."""

import re
import unittest

from reviewdesk.analysis import analyze_sources
from reviewdesk.context import build_context, estimate_tokens, redact_secrets, source_lines
from reviewdesk.models import SourceFile


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.analysis = analyze_sources([
            SourceFile("entry.py", "from helper import divide\ndef main(x):\n    return divide(x)\n"),
            SourceFile("helper.py", "def divide(x):\n    return x / 0\n"),
        ])

    def test_budget_and_exact_reported_spans_across_strategies(self):
        for strategy in ("truncate", "dependency"):
            for budget in (0, 10, 256, 400, 700, 1000, 6000):
                with self.subTest(strategy=strategy, budget=budget):
                    result = build_context(self.analysis, budget, strategy)
                    self.assertEqual(result.estimated_tokens, estimate_tokens(result.text))
                    self.assertLessEqual(result.estimated_tokens, budget)
                    expected = sum(end - start + 1 for spans in result.included_spans.values() for start, end in spans)
                    self.assertEqual(expected, len(re.findall(r"^L\d+: ", result.text, flags=re.MULTILINE)))
                    for path, spans in result.included_spans.items():
                        source = next(s for s in self.analysis.files if s.path == path)
                        for start, end in spans:
                            self.assertGreaterEqual(start, 1)
                            self.assertLessEqual(end, len(source.content.splitlines()))

    def test_whole_source_fits_and_is_deterministic(self):
        first = build_context(self.analysis, 6000)
        second = build_context(self.analysis, 6000)
        self.assertEqual(first, second)
        self.assertEqual(first.included_spans, {"helper.py": [(1, 2)], "entry.py": [(1, 3)]})

    def test_dependency_strategy_reaches_helper_before_unrelated_filler(self):
        analysis = analyze_sources([
            SourceFile("aaa_filler.py", "# Unrelated filler material.\n" * 300),
            SourceFile("entry.py", "from helper import divide\ndef main(x):\n    return divide(x)\n"),
            SourceFile("helper.py", "def divide(x):\n    return x / 0\n"),
        ])
        baseline = build_context(analysis, 1000, "truncate")
        selective = build_context(analysis, 1000, "dependency", ["entry.py"])
        self.assertNotIn("helper.py", baseline.included_spans)
        self.assertIn("entry.py", selective.included_spans)
        self.assertIn("helper.py", selective.included_spans)

    def test_overlong_line_is_omitted_without_false_span(self):
        analysis = analyze_sources([SourceFile("long.py", "#" + "x" * 5000 + "\nvalue = 1\n")])
        result = build_context(analysis, 600, "dependency")
        self.assertEqual(result.included_spans, {"long.py": [(2, 2)]})
        self.assertNotIn("L1:", result.text)
        baseline = build_context(analysis, 600, "truncate")
        self.assertEqual(baseline.included_spans, {})

    def test_invalid_budget_and_strategy(self):
        for budget in (-1, True, 1.5, 1_000_001):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                build_context(self.analysis, budget)
        with self.assertRaises(ValueError):
            build_context(self.analysis, strategy="random")

    def test_empty_analysis_has_no_claimed_spans(self):
        result = build_context(analyze_sources([]), 6000)
        self.assertEqual(result.text, "")
        self.assertEqual(result.included_spans, {})

    def test_multilingual_estimate_is_conservative_and_explained(self):
        self.assertEqual(estimate_tokens("代码 x"), len("代码 x".encode("utf-8")))
        self.assertTrue(any("不是 API" in value for value in build_context(self.analysis).limitations))

    def test_unicode_separator_does_not_invent_python_lines(self):
        code = "text = 'a\u2028b'\nx = 1 / 0\n"
        analysis = analyze_sources([SourceFile("unicode.py", code)])
        result = build_context(analysis, 6000)
        self.assertEqual(len(source_lines(code)), 2)
        self.assertEqual(result.included_spans, {"unicode.py": [(1, 2)]})
        self.assertEqual(analysis.findings[0].start_line, 2)
        self.assertEqual(analysis.findings[0].evidence, "x = 1 / 0")


class RedactionTests(unittest.TestCase):
    def test_assignment_and_known_credentials(self):
        key = "sk-" + "a" * 24
        source = "API_KEY = 'abc123notpublic'\nconfig = {\"password\": \"hunter-placeholder\"}\nvalue='" + key + "'\n"
        redacted = redact_secrets(source)
        for secret in ("abc123notpublic", "hunter-placeholder", key):
            self.assertNotIn(secret, redacted)
        self.assertEqual(redacted.count("\n"), source.count("\n"))

    def test_multiline_private_key_and_secret_preserve_line_numbers(self):
        source = 'PRIVATE_KEY = """-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----"""\npassword = """one\ntwo"""\nvalue = 3\n'
        redacted = redact_secrets(source)
        self.assertEqual(source.count("\n"), redacted.count("\n"))
        for secret in ("private-material", "one", "two"):
            self.assertNotIn(secret, redacted)
        self.assertTrue(redacted.endswith("value = 3\n"))

    def test_bearer_and_url_credential(self):
        redacted = redact_secrets("header='Bearer abcdefghijklmnop'\nurl='https://person:passphrase@example.com'\n")
        self.assertNotIn("abcdefghijklmnop", redacted)
        self.assertNotIn("passphrase", redacted)
        self.assertIn("example.com", redacted)

    def test_context_redacts_before_slicing_multiline_assignment(self):
        source = 'password = """first-line\nsecond-line\nthird-line"""\ndef f(x):\n    return x / 0\n'
        result = build_context(analyze_sources([SourceFile("s.py", source)]), 6000)
        self.assertNotIn("second-line", result.text)
        self.assertIn("L5:     return x / 0", result.text)
        self.assertEqual(result.included_spans, {"s.py": [(1, 5)]})


if __name__ == "__main__":
    unittest.main()
