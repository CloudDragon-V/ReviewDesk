"""Developer-authored offline regression tests, separate from blind evaluation."""

import os
from pathlib import Path
import tempfile
import unittest

from reviewdesk.analysis import MAX_FILE_BYTES, analyze_sources, scan_project
from reviewdesk.models import SourceFile


def analyze(code: str):
    return analyze_sources([SourceFile("sample.py", code)])


class RuleTests(unittest.TestCase):
    def test_mutable_defaults_include_keyword_only(self):
        result = analyze("def append(value, items=[], *,\n           options={}):\n    return items\n")
        self.assertEqual([f.rule_id for f in result.findings], ["PY002", "PY002"])
        self.assertTrue(all(f.category == "correctness" and f.severity == "medium" for f in result.findings))

    def test_valid_defaults_and_specific_ignored_exception(self):
        result = analyze("def f(items=None):\n    try:\n        return items\n    except KeyError:\n        pass\n")
        self.assertFalse(result.findings)

    def test_syntax_and_invalid_return_are_errors(self):
        for source in ("def bad(:\n", "return 1\n", "break\n"):
            with self.subTest(source=source):
                findings = analyze(source).findings
                self.assertEqual(findings[0].rule_id, "PY001")
                self.assertEqual(findings[0].severity, "high")

    def test_exception_and_unreachable(self):
        result = analyze("def f():\n    try:\n        raise RuntimeError()\n    except:\n        pass\n    return 1\n    print('dead')\n")
        self.assertEqual({f.rule_id for f in result.findings}, {"PY003", "PY004", "PY005"})
        self.assertEqual(next(f.start_line for f in result.findings if f.rule_id == "PY005"), 7)

    def test_numerical_zero_not_string_format(self):
        self.assertEqual(len(analyze("x = 2 / 0\ny = 3 // 0\nz = 4 % 0\n").findings), 3)
        self.assertFalse(analyze("x = '%s' % 0\n").findings)

    def test_eval_risk_and_shadowing(self):
        self.assertEqual(analyze("def f(text):\n    return eval(text)\n").findings[0].rule_id, "PY007")
        self.assertFalse(analyze("def f(eval, text):\n    return eval(text)\n").findings)
        self.assertFalse(analyze("def eval(text):\n    return text\neval('ok')\n").findings)

    def test_duplicate_dict_key_identity_and_assert_tuple(self):
        result = analyze("items = {'x': 1, 'x': 2}\nflag = 100 is 100\nassert (flag, 'bad')\n")
        self.assertEqual({f.rule_id for f in result.findings}, {"PY008", "PY009", "PY010"})
        self.assertFalse(analyze("x = None\nassert x is None\n").findings)

    def test_control_flow_rules(self):
        result = analyze("def f(x):\n    if False:\n        print(x)\n    try:\n        return 1\n    finally:\n        return 2\n")
        self.assertEqual({f.rule_id for f in result.findings}, {"PY012", "PY014"})

    def test_duplicate_branches_and_self_assignment(self):
        result = analyze("x = 1\nx = x\nif x:\n    print(x)\nelse:\n    print(x)\n")
        self.assertEqual({f.rule_id for f in result.findings}, {"PY011", "PY013"})

    def test_source_is_never_executed_and_evidence_redacted(self):
        code = "raise RuntimeError('must not execute')\napi_key = '" + "sk-" + "x" * 24 + "'\n"
        result = analyze(code)
        self.assertIn("<REDACTED>", result.findings[0].evidence)
        self.assertNotIn("x" * 24, result.findings[0].evidence)

    def test_unreachable_multiline_secret_is_redacted_before_excerpt(self):
        result = analyze('raise ValueError()\npassword = """first-secret\nsecond-secret\nthird-secret\nfourth-secret"""\n')
        self.assertTrue(result.findings)
        self.assertNotIn("secret", result.findings[0].evidence)
        self.assertIn("<REDACTED>", result.findings[0].evidence)

    def test_untrusted_paths_and_size(self):
        result = analyze_sources([SourceFile("../escape.py", "x=1"), SourceFile("C:/abs.py", "x=1"),
                                  SourceFile("ok.py", "x=1"), SourceFile("ok.py", "x=2"),
                                  SourceFile("huge.py", "a" * (MAX_FILE_BYTES + 1))])
        self.assertEqual([source.path for source in result.files], ["ok.py"])
        self.assertTrue(any("4 个文件" in note for note in result.limitations))


class GraphTests(unittest.TestCase):
    def test_project_import_call_and_containment(self):
        result = analyze_sources([
            SourceFile("app.py", "from helper import twice\ndef main():\n    return twice(2)\n"),
            SourceFile("helper.py", "def twice(x):\n    return x * 2\n"),
        ])
        edges = {(e.source, e.target, e.kind) for e in result.edges}
        self.assertIn(("app.py::<module>", "helper.py::<module>", "imports"), edges)
        self.assertIn(("app.py::main", "helper.py::twice", "calls"), edges)
        self.assertIn(("app.py::<module>", "app.py::main", "contains"), edges)

    def test_relative_import_src_layout_and_method(self):
        result = analyze_sources([
            SourceFile("src/pkg/__init__.py", ""),
            SourceFile("src/pkg/a.py", "from .b import helper\nclass A:\n    def one(self):\n        return self.two()\n    def two(self):\n        return helper()\n"),
            SourceFile("src/pkg/b.py", "def helper():\n    return 1\n"),
        ])
        calls = {(e.source, e.target) for e in result.edges if e.kind == "calls"}
        self.assertIn(("src/pkg/a.py::A.one", "src/pkg/a.py::A.two"), calls)
        self.assertIn(("src/pkg/a.py::A.two", "src/pkg/b.py::helper"), calls)

    def test_call_parameter_does_not_resolve_to_global_function(self):
        result = analyze("def helper():\n    return 1\ndef run(helper):\n    return helper()\n")
        self.assertFalse([e for e in result.edges if e.kind == "calls"])

    def test_overwritten_import_does_not_claim_call(self):
        result = analyze_sources([SourceFile("a.py", "from b import helper\nhelper = object()\nhelper()\n"),
                                  SourceFile("b.py", "def helper():\n    return 1\n")])
        self.assertFalse([e for e in result.edges if e.kind == "calls"])

    def test_duplicate_definitions_have_unique_node_ids(self):
        result = analyze("def f():\n    return 1\ndef f():\n    return 2\n")
        self.assertEqual(len(result.nodes), len({n.id for n in result.nodes}))


class ScanTests(unittest.TestCase):
    def test_scan_is_bounded_read_only_and_skips_sensitive_generated_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "main.py").write_text("x = 1\n", encoding="utf-8")
            (root / "secrets.py").write_text("api_key = 'do-not-read'", encoding="utf-8")
            (root / "binary.py").write_bytes(b"abc\x00def")
            (root / "large.py").write_bytes(b"#" * (MAX_FILE_BYTES + 1))
            (root / ".hidden.py").write_text("x=1", encoding="utf-8")
            (root / "venv").mkdir()
            (root / "venv" / "bad.py").write_text("x=1", encoding="utf-8")
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            result = scan_project(root)
            self.assertEqual([f.path for f in result.files], ["main.py"])
            after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertGreaterEqual(len(result.limitations), 5)

    def test_declared_encoding_and_max_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.py").write_bytes("# coding: latin-1\nname = 'café'\n".encode("latin-1"))
            (root / "b.py").write_text("x=1", encoding="utf-8")
            result = scan_project(root, max_files=1)
            self.assertEqual(len(result.files), 1)
            self.assertIn("café", result.files[0].content)
            self.assertTrue(any("1 个 Python" in note for note in result.limitations))

    def test_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as external:
            root, outside = Path(temporary), Path(external)
            (outside / "outside.py").write_text("x=1", encoding="utf-8")
            try:
                os.symlink(outside, root / "linked", target_is_directory=True)
            except OSError:
                if os.name != "nt":
                    self.skipTest("Host does not allow creating test symlinks")
                # A real NTFS junction exercises the Windows reparse branch
                # without requiring elevated symlink privileges.
                import _winapi
                _winapi.CreateJunction(str(outside), str(root / "linked"))
            self.assertFalse(scan_project(root).files)
            with self.assertRaises(ValueError):
                scan_project(root / "linked")

    def test_invalid_root_and_limit_fail_clearly(self):
        with self.assertRaises(ValueError):
            scan_project(Path.cwd(), max_files=0)
        with self.assertRaises(OSError):
            scan_project(Path("nonexistent-test-root-reviewdesk"))


if __name__ == "__main__":
    unittest.main()
