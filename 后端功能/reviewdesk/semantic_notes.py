"""Small, evidence-based explanations of Python semantics, without execution.

The Program deliberately stores no source text.  Its expression fragments may be
parsed as syntax, but are never evaluated; no value propagation is attempted.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict


def _expression(text: str) -> ast.expr | None:
    if not text or len(text) > 12000:
        return None
    try:
        return ast.parse(text, mode="eval").body
    except (SyntaxError, ValueError, RecursionError):
        return None


def _events(symbol: dict) -> list[tuple[str, dict]]:
    return sorted(((v["name"], e) for v in symbol.get("variables", [])
                   if v.get("scope") == symbol.get("qualified_name")
                   for e in v.get("events", [])), key=lambda item: item[1].get("line", 0))


def _literal_container(node: ast.AST | None) -> bool:
    return isinstance(node, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp))


def semantic_notes(program: dict) -> list[dict]:
    """Return selective notes, each tied to an actual recorded source structure."""
    symbols = program.get("symbols", [])
    lookup = {s["id"]: s for s in symbols}
    by_name = {(s.get("path"), s.get("qualified_name")): s for s in symbols}
    calls_by_source: dict[str, list[dict]] = defaultdict(list)
    for call in program.get("calls", []):
        calls_by_source[call.get("source", "")].append(call)
    notes: list[dict] = []

    def add(symbol: dict, line: int, title: str, body: str) -> None:
        notes.append({"path": symbol["path"], "line": line,
                      "scope": symbol.get("qualified_name", ""), "title": title, "body": body})

    for symbol in symbols:
        events = _events(symbol)
        local_calls = calls_by_source[symbol["id"]]
        call_targets = {(c.get("line"), c.get("expression")): lookup.get(c.get("target"))
                        for c in local_calls if c.get("resolved")}
        for parameter in symbol.get("parameters", []):
            default = _expression(parameter.get("default", ""))
            if _literal_container(default):
                name = parameter["name"]
                add(symbol, symbol["start_line"], "默认容器会跨调用共享",
                    f"参数 {name} 的默认表达式是 {parameter['default']}。它在这次函数定义执行时创建，"
                    "并不是每次调用都重新创建。对同一个函数对象，省略该参数的调用会取得同一个默认容器；"
                    "若函数原地修改它，后续省略参数的调用可能看见先前留下的内容。显式传入另一个对象则使用传入对象。"
                    "在函数内把参数名重新赋值，与修改原容器是两回事；通常可用 None 作默认值，再在函数体内按需新建容器。")

        # Only infer a possible built-in container receiver from actual literal
        # syntax in this scope.  Branches and rebinding remain explicit conditions.
        prior: dict[str, tuple[ast.expr | None, int]] = {}
        shown: set[str] = set()
        deferred: list[tuple[str, dict, str]] = []
        unpacked_lines = {s["line"] for s in symbol.get("steps", []) if "此处有解包" in s.get("text", "")}
        for name, event in events:
            if event.get("kind") not in {"assign", "parameter", "annotated_assign"}:
                continue
            expression = event.get("expression", "")
            node = _expression(expression)
            line = event.get("line", symbol["start_line"])
            if line in unpacked_lines:
                # The recorded expression is the entire RHS, not this target's
                # individual element.  Do not pretend unpacking is an alias.
                prior[name] = (None, line)
                continue
            if event.get("kind") != "parameter" and isinstance(node, ast.Name) and "alias" not in shown:
                origin, origin_line = prior.get(node.id, (None, 0))
                if _literal_container(origin):
                    add(symbol, line, "赋值别名不是复制容器",
                        f"这里把 {node.id} 赋给 {name}。当该行执行时，两个名称指向同一个对象，不会复制内容。"
                        f"若第 {origin_line} 行建立的容器仍是此时的对象，经任一名称做 append、下标写入等原地修改，"
                        "另一个名称也会观察到该对象的变化；随后仅把某个名称重新绑定到其他对象，并不会同步重新绑定另一个名称。")
                    shown.add("alias")
            receiver = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "copy" and not node.args and not node.keywords:
                receiver = node.func.value
            elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
                receiver = node.value
            if isinstance(receiver, ast.Name) and "copy" not in shown:
                origin, origin_line = prior.get(receiver.id, (None, 0))
                is_list_slice = isinstance(node, ast.Subscript) and isinstance(origin, (ast.List, ast.ListComp))
                if _literal_container(origin) and (not isinstance(node, ast.Subscript) or is_list_slice):
                    add(symbol, line, "浅拷贝只分开外层，嵌套对象仍可共享",
                        f"表达式 {expression} 把结果绑定给 {name}。如果 {receiver.id} 仍是第 {origin_line} 行建立的内置容器，"
                        "此操作创建新的外层容器，但不会递归复制其中的对象。向新列表追加元素、给新字典替换一个键值等只改变新外层；"
                        "如果已有元素本身是可变列表或字典，通过任一外层入口修改那个嵌套对象，另一侧仍可能看到变化。"
                        "因此要分别跟踪‘外层是否同一对象’与‘内部元素是否同一对象’，不能把浅拷贝理解为完全独立的深拷贝。")
                    shown.add("copy")
            target = call_targets.get((line, expression))
            if isinstance(node, ast.GeneratorExp):
                deferred.append((name, event, "生成器表达式"))
            elif isinstance(node, ast.Call) and target and target.get("is_generator"):
                deferred.append((name, event, "异步生成器" if target.get("is_async") else "生成器函数"))
            elif isinstance(node, ast.Call) and target and target.get("is_async"):
                deferred.append((name, event, "协程"))
            prior[name] = (node, line)

        for name, event, kind in deferred[:3]:
            line = event["line"]
            if kind == "协程":
                add(symbol, line, "创建协程与实际执行是两个时间点",
                    f"{name} 接收 {event['expression']} 创建的协程对象。直接调用这里的 async def 函数不会立即执行其函数体，"
                    "也不能把这个变量当作函数最终返回的数据。后续 await 它，或把它交给事件循环创建任务、运行或调度，才会推进函数体。"
                    "只把裸协程保存到变量，并不等于已经创建 Task；先 await 另一个协程，甚至 await asyncio.sleep(0)，都不会使这个尚未调度的对象自动开始运行。"
                    "await 期间可能把执行机会交给已经调度的其他任务；执行至正常 return 后，等待方才拿到结果。如果对象始终没有被等待或调度，函数体不会因此自动完成。")
            elif kind == "异步生成器":
                add(symbol, line, "异步生成器需要异步消费",
                    f"{name} 接收 {event['expression']} 创建的异步生成器对象。创建对象与执行函数体分开，"
                    "后续 async for 或等待 __anext__() 才逐步推进，不能直接交给普通 next() 或 list() 当作同步生成器使用。"
                    "yield 交出一项并保存当前位置；再次推进继续向后，不会自动回到开头。")
            else:
                consumers = [str(c["line"]) for c in local_calls if c.get("callee") in {"next", "list", "tuple", "set", "sum"}
                             and c.get("arguments", [None])[0:1] == [name] and c.get("line", 0) >= line]
                detail = f"本作用域第 {'、'.join(consumers[:6])} 行存在对该变量的消费调用；若这些名称指向相应内置函数，" if consumers else "后续若用内置 next()、for、list() 等消费它，"
                immediate = "生成器表达式最外层 for 的可迭代表达式在创建时就求值，元素表达式与后续迭代工作仍延后。" if kind == "生成器表达式" else "函数体通常到第一次推进时才开始。"
                add(symbol, line, "生成器保存进度，消费后不会自动回退",
                    f"{name} 接收 {event['expression']} 创建的生成器对象，不是已经计算好的完整列表。{immediate}"
                    f"{detail}会沿同一个对象的当前位置继续：next() 请求下一项，list() 收集当时尚未产出的剩余项。"
                    "已经取出的项不会因再次 list() 而重现，耗尽后也不会自动重新执行一遍；若要从头计算，需要重新创建生成器。"
                    "上述顺序只在相应消费行确实依次执行且中途未异常退出时成立。")

        parent_name = symbol.get("qualified_name", "").rsplit(".", 1)[0]
        parent = by_name.get((symbol.get("path"), parent_name))
        if parent and parent.get("kind") in {"function", "method"}:
            outer_names = {v["name"] for v in parent.get("variables", []) if v.get("scope") == parent["qualified_name"]}
            declared_names = {v["name"] for v in symbol.get("variables", [])}
            declared_names.update(s.get("name") for s in symbols if s.get("path") == symbol.get("path")
                                  and s.get("qualified_name", "").rsplit(".", 1)[0] == symbol["qualified_name"])
            parameter_names = {p["name"] for p in symbol.get("parameters", [])}
            expressions = [(r.get("line", symbol["start_line"]), r.get("expression", "")) for r in symbol.get("returns", [])]
            expressions += [(e.get("line", symbol["start_line"]), e.get("expression", "")) for _, e in events if e.get("kind") != "parameter"]
            expressions += [(c["line"], c.get("expression", "")) for c in local_calls]
            captured: set[str] = set()
            capture_line = symbol["start_line"]
            ambiguous_binding = any(s.get("kind") in {"global", "nonlocal", "import", "importfrom"}
                                    for s in symbol.get("steps", []))
            for line, expression in expressions:
                if ambiguous_binding:
                    break
                tree = _expression(expression)
                if tree is None:
                    continue
                # Do not guess free variables inside another nested lambda or
                # comprehension: their own bindings need a separate scope pass.
                if any(isinstance(n, (ast.Lambda, ast.comprehension, ast.NamedExpr)) for n in ast.walk(tree)):
                    continue
                loaded = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
                found = (loaded & outer_names) - declared_names - parameter_names
                if found:
                    capture_line = line
                    captured.update(found)
            if captured:
                names = "、".join(sorted(captured))
                looping = any(v["name"] in captured and any(e.get("kind") == "iteration" for e in v.get("events", []))
                              for v in parent.get("variables", []))
                extra = "这里包含外层循环变量：同一次外层调用中创建的多个函数可能读取同一个循环变量绑定；如果等循环结束才调用它们，读取的是调用时保留的绑定，而不是各自创建那一轮的快照。" if looping else "外层函数返回后，只要内部函数仍被引用，所需的闭包绑定仍可保留；其内容也可能被其他闭包或 nonlocal 赋值改变。"
                add(symbol, capture_line, "闭包读取的是外层绑定，不是创建时快照",
                    f"内部函数在此读取外层作用域的 {names}，这些名称没有被当前函数的参数或已识别局部赋值覆盖。"
                    "函数创建时保存对外层绑定的访问关系，函数体真正执行时才读取该绑定当时的内容。" + extra)
            captures_by_default = [p for p in symbol.get("parameters", []) if isinstance(_expression(p.get("default", "")), ast.Name)
                                   and p.get("default") in outer_names]
            if captures_by_default:
                details = "、".join(f"{p['name']}={p['default']}" for p in captures_by_default)
                add(symbol, symbol["start_line"], "默认参数可以保留定义时的对象",
                    f"内部函数的参数写有 {details}。每次执行这条函数定义时，默认表达式会当场求值；"
                    "以后调用时若省略该参数，使用的是当时保存的对象，和在函数体中延迟读取外层同名变量不同。"
                    "如果当时保存的是可变对象，保存的是对象引用，不是对内部内容进行深拷贝。")

        kinds = {s.get("kind") for s in symbol.get("steps", [])}
        if "try" in kinds:
            line = next(s["line"] for s in symbol["steps"] if s.get("kind") == "try")
            add(symbol, line, "异常分支与收尾语句不能按行号硬拼",
                "本作用域有 try 结构。先进入 try；发生异常时，才寻找匹配的 except，未匹配的异常继续向外传播。"
                "若源码有 else，它只在 try 正常完成、没有异常且没有提前 return/break/continue 时进入；若源码有 finally，离开结构时会先执行收尾语句。"
                "特别是 return 的表达式先求值，但返回动作可能要等 finally 完成；finally 若再次 return 或抛异常，还可能改变原来的返回或异常结果。"
                "因此应沿某一实际分支阅读，不能把 try、每个 except、else 全部视为一次运行都会发生。")
        nested = next((c for c in local_calls if isinstance(_expression(c.get("expression", "")), ast.Call)
                       and any(isinstance(n, ast.Call) for arg in _expression(c.get("expression", "")).args for n in ast.walk(arg))), None)
        if nested:
            add(symbol, nested["line"], "嵌套调用先准备参数，再进入外层函数",
                f"{nested['expression']} 中存在作为参数的内层调用。Python 先取得被调用对象，再按求值规则从左到右准备各个参数；"
                "某个参数中的内层函数需要先返回，外层函数才能收到该参数并开始执行。若参数求值抛异常，外层函数就不会进入。"
                "调用图列出谁依赖谁，列表顺序本身不能替代这一求值顺序；短路表达式或条件表达式内部仍只求值实际选中的部分。")
    return notes


def reading_routes(program: dict) -> list[dict]:
    """Bounded source-linked reading routes; never claim a runtime trace."""
    lookup = {s["id"]: s for s in program.get("symbols", [])}
    outgoing: dict[str, list[dict]] = defaultdict(list)
    for call in program.get("calls", []):
        if call.get("resolved") and call.get("target") in lookup:
            outgoing[call.get("source", "")].append(call)
    for calls in outgoing.values():
        calls.sort(key=lambda c: c.get("line", 0))
    routes: list[dict] = []
    for entry in program.get("entry_points", [])[:6]:
        root = lookup.get(entry.get("symbol_id"))
        if not root:
            continue
        main_branch = re.compile(r"条件 (?:__name__ == ['\"]__main__['\"]|['\"]__main__['\"] == __name__) 为真")
        first_calls = [c for c in outgoing[root["id"]] if c.get("line", 0) >= entry.get("line", 0)
                       and main_branch.search(c.get("condition", ""))]
        for first in first_calls[:4]:
            chain: list[dict] = []
            seen = {root["id"]}
            call = first
            while call and len(chain) < 5:
                target = lookup[call["target"]]
                chain.append({"call": call, "symbol": target, "recursive": target["id"] in seen})
                if target["id"] in seen:
                    break
                seen.add(target["id"])
                following = outgoing[target["id"]]
                call = following[0] if following else None
            routes.append({"entry": entry, "chain": chain})
    return routes
