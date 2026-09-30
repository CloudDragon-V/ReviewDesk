"""Selective semantic explanations must be grounded and avoid false certainty."""

import unittest
from reviewdesk.languages import get_adapter
from reviewdesk.manual import build_manual
from reviewdesk.models import SourceFile
from reviewdesk.semantic_notes import reading_routes, semantic_notes


def program(source):
    return get_adapter("python").analyze([SourceFile("sample.py", source)])


class SemanticNotesTests(unittest.TestCase):
    def test_alias_and_shallow_copy_are_distinguished(self):
        p = program("def example():\n    rows = [[1]]\n    same = rows\n    copied = rows.copy()\n    copied[0].append(2)\n    copied.append([3])\n    return rows, copied\n")
        notes = semantic_notes(p)
        by_title = {n["title"]: n for n in notes}
        self.assertIn("赋值别名不是复制容器", by_title)
        copy_note = by_title["浅拷贝只分开外层，嵌套对象仍可共享"]
        self.assertEqual(4, copy_note["line"])
        self.assertIn("不会递归复制", copy_note["body"])
        self.assertIn("另一侧仍可能看到", copy_note["body"])
        self.assertNotIn("rows 的最终值", copy_note["body"])

    def test_custom_copy_and_scalar_assignments_do_not_trigger(self):
        notes = semantic_notes(program("def example(custom):\n    number = 3\n    other = number\n    copied = custom.copy()\n    return copied\n"))
        self.assertFalse(any("复制" in n["title"] or "拷贝" in n["title"] for n in notes))

    def test_slice_of_list_triggers_but_string_slice_does_not(self):
        notes = semantic_notes(program("def example():\n    rows = [[1]]\n    copied = rows[:]\n    text = 'abc'\n    other = text[:]\n    return copied, other\n"))
        copies = [n for n in notes if "浅拷贝" in n["title"]]
        self.assertEqual(1, len(copies))
        self.assertEqual(3, copies[0]["line"])

    def test_only_mutable_default_explains_cross_call_state(self):
        notes = semantic_notes(program("def shared(items=[]):\n    items.append(1)\n    return items\ndef fresh(items=None):\n    return items\n"))
        defaults = [n for n in notes if "默认容器" in n["title"]]
        self.assertEqual(1, len(defaults))
        self.assertIn("同一个函数对象", defaults[0]["body"])
        self.assertIn("显式传入", defaults[0]["body"])

    def test_loop_late_binding_and_default_capture_are_different(self):
        notes = semantic_notes(program("def build():\n    slow = []\n    fixed = []\n    for i in range(3):\n        slow.append(lambda: i)\n        fixed.append(lambda i=i: i)\n    return slow, fixed\n"))
        late = [n for n in notes if "闭包读取" in n["title"]]
        captured = [n for n in notes if "默认参数可以" in n["title"]]
        self.assertEqual(1, len(late))
        self.assertEqual(5, late[0]["line"])
        self.assertIn("如果等循环结束才调用", late[0]["body"])
        self.assertEqual(1, len(captured))
        self.assertEqual(6, captured[0]["line"])

    def test_inner_local_shadowing_does_not_claim_closure(self):
        notes = semantic_notes(program("def outer():\n    value = 1\n    def inner():\n        value = 2\n        return value\n    return inner\n"))
        self.assertFalse(any("闭包读取" in n["title"] for n in notes))

    def test_global_import_and_nested_def_shadowing_do_not_claim_closure(self):
        for inner_body in ["global value\n        return value", "import math as value\n        return value", "def value():\n            return 2\n        return value"]:
            with self.subTest(inner_body=inner_body):
                notes = semantic_notes(program("def outer():\n    value = 1\n    def inner():\n        " + inner_body + "\n    return inner\n"))
                self.assertFalse(any("闭包读取" in n["title"] for n in notes))

    def test_unpacking_rhs_is_not_misread_as_each_target_object(self):
        notes = semantic_notes(program("def values():\n    yield 1\n    yield 2\ndef read():\n    first, second = values()\n    x, y = [1, 2]\n    alias = x\n    copied = x.copy()\n    return first, second\n"))
        self.assertFalse(any("生成器保存" in n["title"] or "复制" in n["title"] or "浅拷贝" in n["title"] for n in notes))

    def test_generator_creation_next_and_remaining_consumption(self):
        notes = semantic_notes(program("def values():\n    yield 1\n    yield 2\ndef read():\n    stream = values()\n    first = next(stream)\n    remaining = list(stream)\n    exhausted = list(stream)\n    return first, remaining, exhausted\n"))
        generator = [n for n in notes if "生成器保存进度" in n["title"]]
        self.assertEqual(1, len(generator))
        self.assertEqual(5, generator[0]["line"])
        self.assertIn("第 6、7、8 行", generator[0]["body"])
        self.assertIn("尚未产出的剩余项", generator[0]["body"])
        self.assertIn("重新创建生成器", generator[0]["body"])

    def test_generator_expression_mentions_immediate_outer_iterable(self):
        notes = semantic_notes(program("def create(items):\n    stream = (x + 1 for x in items)\n    return stream\n"))
        note = next(n for n in notes if "生成器保存进度" in n["title"])
        self.assertIn("最外层 for 的可迭代表达式在创建时就求值", note["body"])

    def test_coroutine_and_async_generator_require_different_consumers(self):
        notes = semantic_notes(program("async def fetch():\n    return 1\nasync def rows():\n    yield 2\nasync def main():\n    request = fetch()\n    stream = rows()\n    return await request\n"))
        titles = [n["title"] for n in notes]
        self.assertIn("创建协程与实际执行是两个时间点", titles)
        self.assertIn("异步生成器需要异步消费", titles)
        async_note = next(n for n in notes if n["title"] == "异步生成器需要异步消费")
        self.assertIn("不能直接交给普通 next()", async_note["body"])
        coroutine = next(n for n in notes if n["title"] == "创建协程与实际执行是两个时间点")
        self.assertIn("并不等于已经创建 Task", coroutine["body"])
        self.assertIn("sleep(0)", coroutine["body"])
        self.assertIn("不会使这个尚未调度的对象自动开始运行", coroutine["body"])

    def test_try_and_nested_argument_order_are_selective(self):
        source = "def inner():\n    return 1\ndef outer(value):\n    return value\ndef main():\n    try:\n        return outer(inner())\n    finally:\n        print('done')\n"
        notes = semantic_notes(program(source))
        self.assertTrue(any("异常分支" in n["title"] for n in notes))
        self.assertTrue(any("嵌套调用先准备参数" in n["title"] for n in notes))
        plain = semantic_notes(program("def plain(value):\n    return value + 1\n"))
        self.assertEqual([], plain)

    def test_entry_route_links_only_resolved_project_targets(self):
        p = program("def child():\n    return 1\ndef main():\n    return child()\nif __name__ == '__main__':\n    print(main())\n")
        routes = reading_routes(p)
        self.assertEqual(["main", "child"], [n["symbol"]["name"] for n in routes[0]["chain"]])
        text = build_manual(p, [], "示例")
        self.assertIn("入口到主要调用的阅读路线", text)
        self.assertIn("不是运行轨迹", text)
        self.assertIn("sample.py:L3", text)

    def test_recursive_route_terminates(self):
        p = program("def loop():\n    return loop()\nif __name__ == '__main__':\n    loop()\n")
        routes = reading_routes(p)
        self.assertLessEqual(len(routes[0]["chain"]), 5)
        self.assertTrue(routes[0]["chain"][-1]["recursive"])

    def test_entry_route_does_not_include_else_or_following_module_call(self):
        p = program("def main():\n    return 1\ndef imported():\n    return 2\nif __name__ == '__main__':\n    main()\nelse:\n    imported()\nimported()\n")
        routes = reading_routes(p)
        self.assertEqual(1, len(routes))
        self.assertEqual("main", routes[0]["chain"][0]["symbol"]["name"])

    def test_generated_notes_escape_untrusted_code_and_have_source_lines(self):
        p = program("def values(items=['<script>']):\n    return items\n")
        text = build_manual(p, [], "安全")
        self.assertIn("容易读错的 Python 语义", text)
        self.assertIn("sample.py:L1", text)
        semantic_section = text.split("### 容易读错的 Python 语义", 1)[1].split("### 文件分工索引", 1)[0]
        self.assertNotIn("<script>", semantic_section)
        self.assertIn("&lt;script&gt;", semantic_section)


if __name__ == "__main__":
    unittest.main()
