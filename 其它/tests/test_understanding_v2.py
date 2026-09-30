"""Boundary tests for static evidence; none execute reviewed input."""

import json
import unittest
from unittest.mock import patch

from reviewdesk.languages import get_adapter, supported_languages
from reviewdesk.models import SourceFile
from reviewdesk.understanding import ANALYSIS_VERSION


def analyze(code: str, extra: list[SourceFile] | None = None) -> dict:
    return get_adapter("python").analyze([SourceFile("main.py", code), *(extra or [])])


def symbol(program: dict, name: str) -> dict:
    return next(item for item in program["symbols"] if item["name"] == name)


class UnderstandingTests(unittest.TestCase):
    def test_language_contract_and_friendly_error(self):
        self.assertIn("python", [item["id"] for item in supported_languages()])
        with self.assertRaisesRegex(ValueError, "暂不支持"):
            get_adapter("javascript")
        with self.assertRaisesRegex(ValueError, "仅接受"):
            get_adapter("python").analyze([SourceFile("x.js", "alert(1)")])

    def test_analysis_output_identifies_the_engine_version(self):
        self.assertEqual(ANALYSIS_VERSION, analyze("value = 1")["analysis_version"])

    def test_input_is_never_executed(self):
        with patch("builtins.open", side_effect=AssertionError("reviewed code must not run")):
            program = analyze("open('should-not-exist', 'w').write('bad')\nraise Exception('not executed')")
        self.assertEqual(1, len(program["files"]))
        self.assertGreaterEqual(len(program["calls"]), 2)

    def test_partial_syntax_failure_preserves_other_file(self):
        program = analyze("def broken(:\n", [SourceFile("ok.py", "def f():\n    return 1")])
        self.assertIn("parse_error", program["files"][0])
        self.assertEqual("f", symbol(program, "f")["name"])
        json.dumps(program, ensure_ascii=False)

    def test_invalid_context_is_rejected_without_execution(self):
        for code in ["return 1", "break", "await f()", "def f(x, x):\n    pass"]:
            self.assertIn("parse_error", analyze(code)["files"][0])

    def test_unicode_separators_preserve_python_physical_line_numbers(self):
        for separator in ("\u2028", "\u2029", "\f"):
            for newline in ("\n", "\r\n", "\r"):
                with self.subTest(separator=repr(separator), newline=repr(newline)):
                    source = newline.join([f'label = "a{separator}b"', "def correct_function():", "    return 123", ""])
                    program = analyze(source)
                    self.assertNotIn("parse_error", program["files"][0])
                    self.assertEqual(3, program["files"][0]["line_count"])
                    function = symbol(program, "correct_function")
                    self.assertEqual((2, 3), (function["start_line"], function["end_line"]))
                    self.assertEqual(3, function["returns"][0]["line"])

    def test_depth_and_size_fail_with_readable_limit(self):
        with patch("reviewdesk.understanding.MAX_AST_DEPTH", 3):
            program = analyze("x = a + b + c")
        self.assertIn("安全分析上限", program["files"][0]["parse_error"]["message"])
        with patch("reviewdesk.understanding.MAX_SOURCE_CHARACTERS", 2):
            program = analyze("a = 1")
        self.assertIn("容量", program["files"][0]["parse_error"]["message"])

    def test_import_alias_cross_file_and_unknown_dependency(self):
        program = analyze("from util import transform as t\nimport util as u\nimport requests\ndef run(x):\n    t(x)\n    u.transform(x)\n    requests.get(x)\n", [SourceFile("util.py", "def transform(x):\n    return x")])
        calls = {call["callee"]: call for call in program["calls"]}
        self.assertTrue(calls["t"]["resolved"])
        self.assertTrue(calls["u.transform"]["resolved"])
        self.assertEqual(calls["t"]["target"], calls["u.transform"]["target"])
        self.assertFalse(calls["requests.get"]["resolved"])

    def test_relative_import(self):
        program = get_adapter("python").analyze([SourceFile("pkg/main.py", "from .util import f\ndef run():\n    f()"), SourceFile("pkg/util.py", "def f():\n    pass")])
        self.assertTrue(program["calls"][0]["resolved"])

    def test_importing_package_does_not_assume_child_module_is_loaded(self):
        files = [SourceFile("pkg/__init__.py", ""),
                 SourceFile("pkg/child.py", "def f():\n    return 1\n")]
        for code in ("import pkg\npkg.child.f()", "import pkg as p\np.child.f()"):
            with self.subTest(code=code):
                call = analyze(code, files)["calls"][0]
                self.assertFalse(call["resolved"])
                self.assertIsNone(call["target"])

    def test_explicit_child_imports_still_resolve(self):
        files = [SourceFile("pkg/__init__.py", ""),
                 SourceFile("pkg/child.py", "def f():\n    return 1\n")]
        for code in ("import pkg.child\npkg.child.f()",
                     "import pkg.child as child\nchild.f()",
                     "from pkg import child\nchild.f()",
                     "from pkg.child import f as alias\nalias()"):
            with self.subTest(code=code):
                call = analyze(code, files)["calls"][0]
                self.assertTrue(call["resolved"])
                self.assertEqual("pkg/child.py::pkg.child.f:1", call["target"])

    def test_package_members_resolve_without_loading_unrelated_children(self):
        files = [SourceFile("pkg/__init__.py", "def f():\n    return 1\n"),
                 SourceFile("pkg/child.py", "def f():\n    return 2\n"),
                 SourceFile("pkg/other.py", "def f():\n    return 3\n")]
        program = analyze("import pkg.child\npkg.f()\npkg.child.f()\npkg.other.f()", files)
        calls = {call["callee"]: call for call in program["calls"]}
        self.assertEqual("pkg/__init__.py::pkg.f:1", calls["pkg.f"]["target"])
        self.assertEqual("pkg/child.py::pkg.child.f:1", calls["pkg.child.f"]["target"])
        self.assertFalse(calls["pkg.other.f"]["resolved"])

    def test_local_parameter_and_later_assignment_shadow_global(self):
        program = analyze("def f():\n    return 1\ndef a(f):\n    return f()\ndef b():\n    f()\n    f = lambda: 2\ndef c():\n    return f()")
        calls = {call["source"]: call for call in program["calls"]}
        self.assertFalse(calls[symbol(program, "a")["id"]]["resolved"])
        self.assertFalse(calls[symbol(program, "b")["id"]]["resolved"])
        self.assertTrue(calls[symbol(program, "c")["id"]]["resolved"])

    def test_decorated_conditional_and_redefined_calls_stay_unknown(self):
        for code in ["@wrap\ndef f():\n    pass\nf()", "if flag:\n    def f():\n        pass\nf()", "def f():\n    pass\nf = other\nf()"]:
            program = analyze(code)
            self.assertFalse(next(c for c in program["calls"] if c["callee"] == "f")["resolved"])

    def test_self_method_and_reassigned_self(self):
        program = analyze("class A:\n    def first(self):\n        return self.second()\n    def second(self):\n        return 2\n    def other(self):\n        self = factory()\n        return self.second()")
        calls = [call for call in program["calls"] if call["callee"] == "self.second"]
        self.assertTrue(calls[0]["resolved"])
        self.assertIn("重写", calls[0]["description"])
        self.assertFalse(calls[1]["resolved"])

    def test_bare_method_name_does_not_look_in_class_namespace(self):
        program = analyze("class A:\n    def target(self):\n        pass\n    def run(self):\n        target()")
        self.assertFalse(program["calls"][0]["resolved"])

    def test_nested_class_does_not_capture_outer_class_dictionary(self):
        program = analyze("class Outer:\n    def helper():\n        return 1\n    class Inner:\n        result = helper()")
        self.assertFalse(program["calls"][0]["resolved"])
        self.assertIsNone(program["calls"][0]["target"])

    def test_nested_class_and_method_skip_all_enclosing_class_dictionaries(self):
        program = analyze("def helper():\n    return 0\nclass Outer:\n    def helper():\n        return 1\n    class Inner:\n        result = helper()\n        def run(self):\n            return helper()")
        self.assertEqual(2, len(program["calls"]))
        self.assertTrue(all(call["target"] == "main.py::main.helper:1" for call in program["calls"]))

    def test_nested_class_preserves_enclosing_function_closure(self):
        program = analyze("def helper():\n    return 0\ndef factory():\n    def helper():\n        return 1\n    class Outer:\n        def helper():\n            return 2\n        class Inner:\n            result = helper()")
        self.assertEqual("main.py::main.factory.helper:4", program["calls"][0]["target"])

    def test_class_body_can_use_its_own_preceding_definition(self):
        program = analyze("class Container:\n    def helper():\n        return 1\n    result = helper()")
        self.assertEqual("main.py::main.Container.helper:2", program["calls"][0]["target"])

    def test_nested_class_parameter_shadow_does_not_fall_back_to_global(self):
        program = analyze("def helper():\n    return 0\ndef factory(helper):\n    class Outer:\n        class Inner:\n            result = helper()")
        self.assertFalse(program["calls"][0]["resolved"])

    def test_staticmethod_parameter_named_self_is_not_assumed_to_be_instance(self):
        program = analyze("class A:\n    def target(self):\n        pass\n    @staticmethod\n    def run(self):\n        self.target()")
        self.assertFalse(program["calls"][0]["resolved"])

    def test_branch_and_unreachable_variable_events(self):
        program = analyze("def f(flag):\n    x = 0\n    if flag:\n        x = 1\n        return x\n        x = 99\n    else:\n        x = 2\n    return x")
        function = symbol(program, "f")
        events = next(v["events"] for v in function["variables"] if v["name"] == "x")
        self.assertIn("为真", events[1]["condition"])
        self.assertIn("不可达", events[2]["condition"])
        self.assertIn("为假", events[3]["condition"])
        self.assertIn("运行时", events[0]["explanation"])

    def test_both_branches_terminate(self):
        program = analyze("def f(flag):\n    if flag:\n        return 1\n    else:\n        raise ValueError()\n    print('unreachable')")
        self.assertIn("不可达", symbol(program, "f")["steps"][-1]["condition"])

    def test_loop_else_continue_break_and_finally(self):
        program = analyze("def f(items):\n    for x in items:\n        if x:\n            break\n        continue\n        x = 99\n    else:\n        done()\n    try:\n        return 1\n    finally:\n        cleanup()\n    unreachable()")
        calls = {call["callee"]: call for call in program["calls"]}
        self.assertIn("未被 break", calls["done"]["condition"])
        self.assertIn("finally", calls["cleanup"]["condition"])
        self.assertNotIn("前序语句未提前中断", calls["cleanup"]["condition"])
        self.assertIn("不可达", calls["unreachable"]["condition"])

    def test_generator_async_and_defaults(self):
        program = analyze("async def f(x: int = 3, /, *, flag: bool = True) -> int:\n    return await run(x)\ndef g():\n    yield 1\n    yield from values()")
        function = symbol(program, "f")
        self.assertTrue(function["is_async"])
        self.assertEqual("3", function["parameters"][0]["default"])
        self.assertIn("仅限位置", function["parameters"][0]["description"])
        self.assertTrue(symbol(program, "g")["is_generator"])
        self.assertEqual(2, len(symbol(program, "g")["returns"]))

    def test_lambda_body_has_own_deferred_scope(self):
        program = analyze("def f():\n    return 1\ncallback = lambda f: f()\nother = lambda: f()")
        calls = [call for call in program["calls"] if call["callee"] == "f"]
        self.assertEqual(2, len(calls))
        self.assertTrue(all("lambda" in call["source"] for call in calls))
        self.assertEqual([False, True], [call["resolved"] for call in calls])
        self.assertTrue(all("实际调用" in call["condition"] for call in calls))

    def test_comprehension_target_shadows_global_function(self):
        program = analyze("def f():\n    return 1\nx = [f() for f in callbacks if ready()]\ny = (f() for _ in values())")
        calls = [call for call in program["calls"] if call["callee"] == "f"]
        self.assertFalse(calls[0]["resolved"])
        self.assertTrue(calls[1]["resolved"])
        self.assertIn("生成器表达式被实际迭代", calls[1]["condition"])
        self.assertIn("筛选条件", calls[0]["condition"])

    def test_comprehension_variables_do_not_merge_with_outer_names(self):
        program = analyze("def f(items):\n    x = 100\n    values = [x + 1 for x in items]\n    return x")
        values = [v for v in symbol(program, "f")["variables"] if v["name"] == "x"]
        self.assertEqual(2, len(values))
        self.assertNotEqual(values[0]["scope"], values[1]["scope"])
        self.assertEqual("100", values[0]["events"][0]["expression"])
        self.assertIn("不覆盖外层", values[1]["events"][0]["explanation"])

    def test_short_circuit_conditions_and_possible_mutation(self):
        program = analyze("def run(items, flag):\n    flag and action()\n    x = yes() if flag else no()\n    removed = items.pop()")
        calls = {call["callee"]: call for call in program["calls"]}
        self.assertIn("短路", calls["action"]["condition"])
        self.assertIn("为真", calls["yes"]["condition"])
        self.assertIn("为假", calls["no"]["condition"])
        events = next(v["events"] for v in symbol(program, "run")["variables"] if v["name"] == "items")
        self.assertEqual("possible_mutation", events[-1]["kind"])

    def test_delete_subscript_records_resolved_index_call(self):
        program = analyze("def pick():\n    return 0\ndef run(data):\n    del data[pick()]")
        self.assertEqual(1, len(program["calls"]))
        call = program["calls"][0]
        self.assertEqual(("pick", 4, "main.py::main.pick:1"),
                         (call["callee"], call["line"], call["target"]))
        deletion = next(v for v in symbol(program, "run")["variables"] if v["name"] == "data[pick()]")
        self.assertEqual("delete", deletion["events"][0]["kind"])

    def test_delete_nested_targets_preserve_all_calls_and_branch_conditions(self):
        program = analyze("def factory():\n    return None\ndef index():\n    return 0\ndef choose(value):\n    return value\ndef run(data, flag):\n    if flag:\n        del factory().items[index()], data[choose(index())]")
        calls = program["calls"]
        self.assertEqual(["factory", "index", "choose", "index"], [call["callee"] for call in calls])
        self.assertTrue(all(call["resolved"] and call["line"] == 9 for call in calls))
        self.assertTrue(all("条件 flag 为真" in call["condition"] for call in calls))

    def test_annotation_does_not_claim_assignment(self):
        function = symbol(analyze("def f():\n    x: int\n    return 1"), "f")
        self.assertFalse(any(v["name"] == "x" for v in function["variables"]))
        self.assertIn("没有给它赋值", function["steps"][0]["text"])

    def test_main_guard_is_detected_but_not_plain_name_string(self):
        program = analyze("if '__main__' == __name__:\n    run()\nif '__main__' == other:\n    run()")
        self.assertEqual(1, len(program["entry_points"]))

    def test_exception_name_cleared_and_match_guard_can_return_false(self):
        program = analyze("def f(value):\n    try:\n        run()\n    except ValueError as error:\n        log(error)\n    match value:\n        case [x] if check(x):\n            handle(x)")
        events = next(v["events"] for v in symbol(program, "f")["variables"] if v["name"] == "error")
        self.assertEqual(["exception", "delete"], [event["kind"] for event in events])
        calls = {call["callee"]: call for call in program["calls"]}
        self.assertNotIn("守卫", calls["check"]["condition"])
        self.assertIn("守卫", calls["handle"]["condition"])


if __name__ == "__main__":
    unittest.main()
