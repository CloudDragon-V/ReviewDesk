"""Conservative Python AST understanding with line-level, non-executed evidence."""

from __future__ import annotations

import ast
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from .context import source_lines
from .models import SourceFile


ANALYSIS_VERSION = "python-ast-2026-09-29"
MAX_AST_NODES = 80_000
MAX_AST_DEPTH = 120
MAX_SOURCE_CHARACTERS = 1_500_000
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
SCOPES = (*FUNCTIONS, ast.ClassDef, ast.Lambda)


def _expr(node: ast.AST | None, limit: int = 320) -> str:
    if node is None:
        return ""
    try:
        value = ast.unparse(node).replace("\n", " ")
    except (RecursionError, ValueError):
        value = "（表达式过于复杂，查看源码）"
    return value if len(value) <= limit else value[:limit] + "…（已截短，完整内容见源码）"


def _condition(parent: str, child: str) -> str:
    return f"{parent}；并且{child}" if parent else child


def _target_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [name for element in node.elts for name in _target_names(element)]
    if isinstance(node, ast.Starred):
        return _target_names(node.value)
    return []


def _purpose(name: str, docstring: str, kind: str) -> str:
    if docstring:
        return "作者文档声明：" + " ".join(docstring.strip().split())[:1200]
    if kind == "module":
        return "未提供模块文档；以下职责依据实际定义、调用和赋值说明。"
    prefixes = {
        "get": "获取或读取数据", "load": "加载数据", "read": "读取内容", "save": "保存数据",
        "write": "写入内容", "create": "创建对象或数据", "build": "构建结果", "parse": "解析输入",
        "validate": "校验输入", "check": "检查条件", "update": "更新状态", "delete": "删除数据",
        "remove": "移除内容", "send": "发送数据", "fetch": "获取外部数据", "render": "生成展示内容",
        "calculate": "计算结果", "compute": "计算结果", "format": "格式化内容", "search": "查找内容",
        "handle": "处理某个事件或请求", "process": "处理数据", "run": "组织执行流程", "main": "组织主流程",
        "test": "验证行为", "__init__": "初始化实例状态", "__enter__": "进入上下文", "__exit__": "退出上下文并清理",
    }
    prefix = name if name.startswith("__") else name.split("_")[0].lower()
    if prefix in prefixes:
        return f"命名推断：可能用于{prefixes[prefix]}；实际行为以下面的源码步骤为准。"
    return "未提供用途说明；职责需结合下面的输入、操作、调用和输出理解。"


def _parameters(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> list[dict]:
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    result = []
    for i, (arg, default) in enumerate(zip(positional, defaults)):
        mode = "仅限位置参数" if i < len(args.posonlyargs) else "位置或关键字参数"
        result.append({"name": arg.arg, "annotation": _expr(arg.annotation), "default": _expr(default),
                       "description": mode + ("；缺省时使用定义函数时计算的默认值" if default is not None else "；调用时需要提供")})
    if args.vararg:
        result.append({"name": "*" + args.vararg.arg, "annotation": _expr(args.vararg.annotation), "default": "",
                       "description": "收集多余的位置参数，函数内以元组形式访问"})
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        result.append({"name": arg.arg, "annotation": _expr(arg.annotation), "default": _expr(default),
                       "description": "仅限关键字参数" + ("；有默认值" if default is not None else "；调用时需要提供")})
    if args.kwarg:
        result.append({"name": "**" + args.kwarg.arg, "annotation": _expr(args.kwarg.annotation), "default": "",
                       "description": "收集多余的关键字参数，函数内以字典形式访问"})
    return result


@dataclass(frozen=True)
class _ImportBinding:
    """Keep the visible binding separate from the explicitly imported path."""

    reference: str
    imported: str


@dataclass
class _Scope:
    node: ast.AST
    symbol: dict
    path: str
    module_name: str
    parent: _Scope | None = None
    bindings: dict[str, list[tuple[str, Any, int]]] = field(default_factory=lambda: defaultdict(list))
    globals: set[str] = field(default_factory=set)
    nonlocals: set[str] = field(default_factory=set)


class PythonAdapter:
    """Analyze Python files using syntax only; never import or run those files."""

    language = "python"
    label = "Python"

    def analyze(self, sources: list[SourceFile]) -> dict:
        """Return modules, symbols, static calls, conditional steps and variable events."""
        return _Analyzer().analyze(sources)


class _Analyzer:
    def __init__(self) -> None:
        self.program: dict = {"language": "python", "analysis_version": ANALYSIS_VERSION,
                              "files": [], "symbols": [], "calls": [], "entry_points": [],
                              "limitations": [
                                  "本说明来自静态语法分析，未执行、导入或修改被审查代码；变量事件是源码中的可能变化，不是一次真实运行的轨迹。",
                                  "函数调用边表示可追溯的静态定义关系；猴子补丁、继承重写、反射、回调和运行时类型可能改变实际调用目标。未解析调用保留在结果中。",
                                  "注释与文档代表作者声明，名称推断仅作阅读辅助；两者都不保证实现正确。类型标注不等于运行时类型校验。",
                              ]}
        self.scopes: list[_Scope] = []
        self.by_node: dict[int, _Scope] = {}
        self.module_scopes: dict[str, list[_Scope]] = defaultdict(list)
        self.class_mutations: dict[int, set[str]] = {}

    def analyze(self, sources: list[SourceFile]) -> dict:
        seen: set[str] = set()
        for source in sources:
            path = str(source.path).replace("\\", "/")
            if path in seen:
                raise ValueError(f"源文件路径重复：{path}")
            seen.add(path)
            if not path.lower().endswith((".py", ".pyi")):
                raise ValueError(f"Python 适配器仅接受 .py 或 .pyi 文件：{path}")
            file = {"path": path, "purpose": "", "line_count": len(source_lines(source.content))}
            self.program["files"].append(file)
            try:
                if len(source.content) > MAX_SOURCE_CHARACTERS:
                    raise ValueError("源码超过单文件分析容量")
                tree = ast.parse(source.content, filename=path)
                self._check_tree(tree)
                # Compilation validates context rules ast.parse does not check,
                # such as return outside a function. The code object is discarded.
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", SyntaxWarning)
                    compile(tree, path, "exec", dont_inherit=True)
                module = self._make_scope(tree, path, self._module_name(path), None)
                file["purpose"] = module.symbol["purpose"]
                self.module_scopes[module.module_name].append(module)
                self._collect(module, tree.body)
            except (SyntaxError, ValueError, RecursionError, MemoryError) as error:
                line = getattr(error, "lineno", 1) or 1
                reason = error.msg if isinstance(error, SyntaxError) else str(error) or "语法结构过于复杂"
                file["purpose"] = "该文件未完成静态解析。"
                file["parse_error"] = {"line": line, "message": reason}
                self.program["limitations"].append(f"{path}:{line} 无法分析：{reason}。其余文件仍可查看。")
        for scope in self.scopes:
            self._describe(scope)
        return self.program

    @staticmethod
    def _check_tree(tree: ast.AST) -> None:
        count, stack = 0, [(tree, 0)]
        while stack:
            node, depth = stack.pop()
            count += 1
            if count > MAX_AST_NODES or depth > MAX_AST_DEPTH:
                raise ValueError("AST 节点数量或嵌套深度超出安全分析上限")
            stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))

    @staticmethod
    def _module_name(path: str) -> str:
        p = PurePosixPath(path)
        parts = list(p.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts) or "__init__"

    def _make_scope(self, node: ast.AST, path: str, module_name: str, parent: _Scope | None) -> _Scope:
        kind = "module" if isinstance(node, ast.Module) else "class" if isinstance(node, ast.ClassDef) else "method" if parent and isinstance(parent.node, ast.ClassDef) else "function"
        name = PurePosixPath(path).name if kind == "module" else (f"<lambda:{node.lineno}:{node.col_offset}>" if isinstance(node, ast.Lambda) else node.name)
        qualified = module_name if kind == "module" else (parent.symbol["qualified_name"] + "." + name)
        start = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", None) or max((getattr(n, "end_lineno", 1) or 1 for n in ast.walk(node)), default=1)
        docstring = "" if isinstance(node, ast.Lambda) else ast.get_docstring(node, clean=True) or ""
        params = _parameters(node) if isinstance(node, (*FUNCTIONS, ast.Lambda)) else []
        if isinstance(node, (*FUNCTIONS, ast.Lambda)):
            signature = ("async " if isinstance(node, ast.AsyncFunctionDef) else "") + name + "(" + _expr(node.args, 1000) + ")"
            if getattr(node, "returns", None):
                signature += " -> " + _expr(node.returns)
        elif isinstance(node, ast.ClassDef):
            signature = "class " + node.name + "(" + ", ".join(_expr(base) for base in node.bases) + ")"
        else:
            signature = path
        symbol = {"id": f"{path}::{qualified}:{start}", "path": path, "name": name, "qualified_name": qualified,
                  "kind": kind, "start_line": start, "end_line": end, "signature": signature, "docstring": docstring,
                  "purpose": _purpose(name, docstring, kind), "parameters": params, "returns": [], "steps": [], "variables": [],
                  "is_async": isinstance(node, ast.AsyncFunctionDef), "is_generator": False, "is_lambda": isinstance(node, ast.Lambda),
                  "return_annotation": _expr(getattr(node, "returns", None))}
        scope = _Scope(node, symbol, path, module_name, parent)
        self.scopes.append(scope)
        self.by_node[id(node)] = scope
        self.program["symbols"].append(symbol)
        for param in params:
            scope.bindings[param["name"].lstrip("*")].append(("parameter", None, start))
        return scope

    def _collect(self, scope: _Scope, statements: list[ast.stmt], conditional: bool = False) -> None:
        """Collect lexical bindings first: a later local assignment still shadows a global."""
        for node in statements:
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                (scope.globals if isinstance(node, ast.Global) else scope.nonlocals).update(node.names)
            elif isinstance(node, (*FUNCTIONS, ast.ClassDef)):
                child = self._make_scope(node, scope.path, scope.module_name, scope)
                decorated = bool(node.decorator_list)
                scope.bindings[node.name].append(("unknown" if conditional or decorated else "symbol", child, node.lineno))
                self._collect(child, node.body)
                args = getattr(node, "args", None)
                header_expressions = [*node.decorator_list, *getattr(args, "defaults", []), *[v for v in getattr(args, "kw_defaults", []) if v is not None]]
                if isinstance(node, ast.ClassDef):
                    header_expressions += [*node.bases, *[kw.value for kw in node.keywords]]
                for expr in header_expressions:
                    self._collect_namedexpr(scope, expr)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    reference = alias.name if alias.asname else alias.name.split(".")[0]
                    value = _ImportBinding(reference, alias.name)
                    scope.bindings[alias.asname or reference].append(("unknown" if conditional else "import", value, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                base = self._import_base(scope, node)
                for alias in node.names:
                    fullname = base + "." + alias.name if base else alias.name
                    value = _ImportBinding(fullname, fullname)
                    scope.bindings[alias.asname or alias.name].append(("unknown" if conditional or alias.name == "*" else "import", value, node.lineno))
            else:
                # Nested suites share this scope, unlike function/class bodies.
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.stmt):
                        self._collect(scope, [child], conditional=True)
                    elif isinstance(child, ast.ExceptHandler):
                        if child.name:
                            scope.bindings[child.name].append(("unknown", None, child.lineno))
                        self._collect(scope, child.body, conditional=True)
                    elif isinstance(child, ast.match_case):
                        for part in ast.walk(child.pattern):
                            name = getattr(part, "name", None) or (getattr(part, "rest", None) if isinstance(part, ast.MatchMapping) else None)
                            if name:
                                scope.bindings[name].append(("unknown", None, getattr(part, "lineno", node.lineno)))
                        self._collect(scope, child.body, conditional=True)
                    elif isinstance(child, ast.expr):
                        self._collect_namedexpr(scope, child)
                targets: list[ast.AST] = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor)):
                    targets = [node.target]
                elif isinstance(node, (ast.With, ast.AsyncWith)):
                    targets = [item.optional_vars for item in node.items if item.optional_vars]
                elif isinstance(node, ast.Delete):
                    targets = node.targets
                for target in targets:
                    for name in _target_names(target):
                        scope.bindings[name].append(("unknown", None, node.lineno))

    def _collect_namedexpr(self, scope: _Scope, expr: ast.AST) -> None:
        stack = [expr]
        while stack:
            node = stack.pop()
            if isinstance(node, ast.Lambda):
                if id(node) not in self.by_node:
                    child = self._make_scope(node, scope.path, scope.module_name, scope)
                    self._collect_namedexpr(child, node.body)
                    for default in [*node.args.defaults, *[d for d in node.args.kw_defaults if d is not None]]:
                        self._collect_namedexpr(scope, default)
                continue
            if isinstance(node, ast.NamedExpr):
                for name in _target_names(node.target):
                    scope.bindings[name].append(("unknown", None, node.lineno))
            if not isinstance(node, SCOPES):
                stack.extend(ast.iter_child_nodes(node))

    @staticmethod
    def _import_base(scope: _Scope, node: ast.ImportFrom) -> str:
        if not node.level:
            return node.module or ""
        parts = scope.module_name.split(".")
        if PurePosixPath(scope.path).stem != "__init__":
            parts.pop()
        if node.level > len(parts) + 1:
            return "<无法解析的相对导入>"
        parts = parts[:len(parts) - node.level + 1]
        return ".".join([*parts, *([node.module] if node.module else [])])

    def _lookup(self, scope: _Scope, name: str, line: int) -> tuple[str, Any] | None:
        origin = scope
        if name in scope.globals:
            while scope.parent:
                scope = scope.parent
            # Any writes via global/nonlocal can change the binding at runtime.
            if name in origin.bindings:
                return None
        elif name in scope.nonlocals:
            return None
        while scope:
            candidates = scope.bindings.get(name, [])
            if candidates:
                if len(candidates) != 1:
                    return None
                kind, value, defined_line = candidates[0]
                if kind == "unknown" or kind == "parameter":
                    return None
                if scope is origin and defined_line > line:
                    return None
                return kind, value
            scope = scope.parent
            # Only the current class body can see its own dictionary. Enclosing
            # classes are not lexical scopes for nested classes or functions.
            while scope and isinstance(scope.node, ast.ClassDef):
                scope = scope.parent
        return None

    def _resolve_import(self, fullname: str, imported: str) -> _Scope | None:
        matches = []
        for prefix, modules in self.module_scopes.items():
            # A source file's existence does not load that submodule. Restrict
            # module roots to the explicit import and its parent packages.
            if prefix != imported and not imported.startswith(prefix + "."):
                continue
            if fullname == prefix:
                matches.extend(modules)
            elif fullname.startswith(prefix + "."):
                tail = fullname[len(prefix) + 1:].split(".")
                for module in modules:
                    current = module
                    for part in tail:
                        entries = current.bindings.get(part, [])
                        if len(entries) != 1 or entries[0][0] != "symbol":
                            current = None
                            break
                        current = entries[0][1]
                    if current:
                        matches.append(current)
        return matches[0] if len(matches) == 1 else None

    def _resolve(self, scope: _Scope, call: ast.Call, shadows: set[str]) -> tuple[_Scope | None, str]:
        node = call.func
        parts = []
        while isinstance(node, ast.Attribute):
            parts.insert(0, node.attr)
            node = node.value
        if not isinstance(node, ast.Name) or node.id in shadows:
            return None, "调用对象由表达式、闭包或运行时数据决定，静态分析无法确认。"
        name = node.id
        if name == "self" and parts and len(parts) == 1 and isinstance(scope.node, FUNCTIONS) and scope.parent and isinstance(scope.parent.node, ast.ClassDef):
            bindings = scope.bindings.get(name, [])
            if len(bindings) == 1 and bindings[0][0] == "parameter" and not scope.node.decorator_list and scope.symbol["parameters"] and scope.symbol["parameters"][0]["name"] == "self":
                entries = scope.parent.bindings.get(parts[0], [])
                class_id = id(scope.parent.node)
                if class_id not in self.class_mutations:
                    self.class_mutations[class_id] = {_expr(target) for part in ast.walk(scope.parent.node) if isinstance(part, (ast.Assign, ast.AnnAssign, ast.AugAssign)) for target in (part.targets if isinstance(part, ast.Assign) else [part.target])}
                mutated = "self." + parts[0] in self.class_mutations[class_id]
                if len(entries) == 1 and entries[0][0] == "symbol" and not mutated:
                    return entries[0][1], "按 self 与同类唯一方法定义建立静态关联；子类重写或运行时替换仍可能改变目标。"
            return None, "self 的成员可能被赋值或动态提供，无法确认其调用目标。"
        binding = self._lookup(scope, name, call.lineno)
        if not binding:
            return None, "该名称可能是内置函数、外部依赖、参数、被重新赋值的对象或动态调用；未确认项目内目标。"
        kind, value = binding
        if kind == "import":
            target = self._resolve_import(".".join([value.reference, *parts]), value.imported)
            if target and not isinstance(target.node, ast.Module):
                return target, "根据 import/from 的别名与项目内唯一声明匹配。"
            return None, "导入目标不在本次代码快照内，或成员/重导出无法唯一解析。"
        if not parts:
            return value, "根据当前词法作用域内唯一且未被覆盖的定义匹配。"
        if isinstance(value.node, ast.ClassDef) and len(parts) == 1:
            entries = value.bindings.get(parts[0], [])
            if len(entries) == 1 and entries[0][0] == "symbol":
                return entries[0][1], "根据显式类名及其唯一方法声明匹配；描述符与运行时替换未验证。"
        return None, "属性调用需要运行时对象信息，未建立确定的项目内连边。"

    def _calls(self, scope: _Scope, expr: ast.AST | None, condition: str) -> None:
        if expr is None:
            return
        def visit(node: ast.AST, shadows: set[str], current: str) -> None:
            if isinstance(node, ast.Lambda):
                # Its own synthetic function scope describes the deferred body.
                for default in [*node.args.defaults, *[d for d in node.args.kw_defaults if d is not None]]:
                    visit(default, shadows, current)
                return
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                inner = set(shadows)
                loop_condition = current
                for index, generator in enumerate(node.generators):
                    visit(generator.iter, inner, loop_condition)
                    if index == 0 and isinstance(node, ast.GeneratorExp):
                        loop_condition = _condition(current, "生成器表达式被实际迭代（仅创建对象时不会执行其主体）")
                    loop_condition = _condition(loop_condition, f"推导式从 {_expr(generator.iter)} 取得本轮元素")
                    iteration_names = _target_names(generator.target)
                    inner.update(iteration_names)
                    for name in iteration_names:
                        self._event(scope, name, getattr(generator.target, "lineno", node.lineno), "iteration", _expr(generator.iter) + " 的当前迭代项", "推导式自己的局部迭代变量，不覆盖外层同名变量", loop_condition, location_override=scope.symbol["qualified_name"] + f".<推导式:{node.lineno}:{node.col_offset}>")
                    for test in generator.ifs:
                        visit(test, inner, loop_condition)
                        loop_condition = _condition(loop_condition, f"筛选条件 {_expr(test)} 为真")
                for result in ([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]):
                    visit(result, inner, loop_condition)
                return
            if isinstance(node, ast.BoolOp):
                path_condition = current
                for value in node.values:
                    visit(value, shadows, path_condition)
                    path_condition = _condition(path_condition, f"短路表达式前项 {_expr(value)} 为{'真' if isinstance(node.op, ast.And) else '假'}")
                return
            if isinstance(node, ast.IfExp):
                visit(node.test, shadows, current)
                visit(node.body, shadows, _condition(current, f"条件表达式 {_expr(node.test)} 为真"))
                visit(node.orelse, shadows, _condition(current, f"条件表达式 {_expr(node.test)} 为假"))
                return
            if isinstance(node, ast.Compare):
                visit(node.left, shadows, current)
                path_condition = current
                for index, comparator in enumerate(node.comparators):
                    visit(comparator, shadows, path_condition)
                    if index < len(node.comparators) - 1:
                        path_condition = _condition(path_condition, "链式比较的前一项比较为真")
                return
            if isinstance(node, ast.Call):
                target, reason = self._resolve(scope, node, shadows)
                self.program["calls"].append({"id": f"call-{len(self.program['calls']) + 1}", "source": scope.symbol["id"],
                                              "target": target.symbol["id"] if target else None, "callee": _expr(node.func),
                                              "path": scope.path, "line": node.lineno, "resolved": target is not None,
                                              "description": reason, "condition": current, "expression": _expr(node),
                                              "arguments": [_expr(arg) for arg in node.args] + [f"{kw.arg or '**'}={_expr(kw.value)}" for kw in node.keywords]})
                if isinstance(node.func, ast.Attribute) and node.func.attr in {"append", "extend", "insert", "pop", "remove", "clear", "update", "add", "discard", "sort", "reverse", "setdefault"}:
                    self._event(scope, _expr(node.func.value), node.lineno, "possible_mutation", _expr(node), "按常见方法名推测可能修改对象；自定义同名方法的语义需查看实现", current)
            if isinstance(node, (ast.Yield, ast.YieldFrom)):
                scope.symbol["is_generator"] = True
                scope.symbol["returns"].append({"line": node.lineno, "expression": _expr(node.value), "description": "yield from 委托给另一迭代器逐项产出" if isinstance(node, ast.YieldFrom) else "yield 暂停函数并产出一个值，下次迭代时继续", "condition": current, "kind": "yield"})
            if isinstance(node, ast.NamedExpr):
                self._event(scope, _expr(node.target), node.lineno, "assign", _expr(node.value), "在表达式求值过程中绑定名称（海象运算符）", current)
            for child in ast.iter_child_nodes(node):
                visit(child, shadows, current)
        visit(expr, set(), condition)

    @staticmethod
    def _event(scope: _Scope, name: str, line: int, kind: str, expression: str, explanation: str, condition: str, *, location_override: str | None = None) -> None:
        variables = scope.symbol["variables"]
        location = location_override or (scope.module_name if name in scope.globals else "外层函数（nonlocal）" if name in scope.nonlocals else scope.symbol["qualified_name"])
        variable = next((item for item in variables if item["name"] == name and item["scope"] == location), None)
        if variable is None:
            variable = {"name": name, "scope": location, "events": []}
            variables.append(variable)
        if not location_override and name in scope.globals:
            explanation += "；通过 global 指向模块命名空间"
        elif not location_override and name in scope.nonlocals:
            explanation += "；通过 nonlocal 指向外层函数绑定"
        variable["events"].append({"line": line, "kind": kind, "expression": expression,
                                   "explanation": explanation + "；实际值取决于该路径是否执行及运行时输入", "condition": condition})

    def _describe(self, scope: _Scope) -> None:
        for param in scope.symbol["parameters"]:
            self._event(scope, param["name"].lstrip("*"), scope.symbol["start_line"], "parameter", param["default"] or "由调用方传入", "调用时绑定参数；默认值只在调用者省略对应参数时采用", "函数被调用并开始执行时")
        if isinstance(scope.node, ast.Lambda):
            condition = "该 lambda 函数被实际调用并开始执行时；创建函数对象时不执行"
            scope.symbol["steps"].append({"line": scope.node.lineno, "kind": "lambda", "text": "计算表达式 " + _expr(scope.node.body) + " 并把结果作为 lambda 返回值。", "condition": condition})
            scope.symbol["returns"].append({"line": scope.node.lineno, "expression": _expr(scope.node.body), "description": "lambda 的表达式结果", "condition": condition, "kind": "return"})
            self._calls(scope, scope.node.body, condition)
        else:
            self._suite(scope, scope.node.body, "")

    def _suite(self, scope: _Scope, statements: list[ast.stmt], condition: str) -> bool:
        """Return true only for definitely terminating suites; unknown control flow stays conditional."""
        terminated = False
        for node in statements:
            active = condition or "控制流沿当前路径实际到达本行"
            if terminated:
                active = _condition(condition, "不可达：同一顺序块前面已 return/raise/break/continue 或两分支均终止")
            step: dict = {"line": node.lineno, "kind": type(node).__name__.lower(), "text": "", "condition": active}
            scope.symbol["steps"].append(step)
            stop = self._statement(scope, node, step, active)
            terminated = terminated or stop
        return terminated

    def _statement(self, scope: _Scope, node: ast.stmt, step: dict, active: str) -> bool:
        if isinstance(node, (*FUNCTIONS, ast.ClassDef)):
            if isinstance(node, ast.ClassDef):
                step["text"] = f"定义类 {node.name}：计算基类与装饰器表达式，执行类体，形成类对象；实例方法体留到调用时执行。"
                exprs = [*node.bases, *[kw.value for kw in node.keywords], *node.decorator_list]
            else:
                step["text"] = f"定义{'异步' if isinstance(node, ast.AsyncFunctionDef) else ''}函数 {node.name}：计算默认值及装饰器表达式，绑定函数对象；此处不执行其函数体。"
                exprs = [*node.args.defaults, *[value for value in node.args.kw_defaults if value is not None], *node.decorator_list]
            for expr in exprs:
                self._calls(scope, expr, active)
            return False
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            step["text"] = f"执行导入 {_expr(node)}，把模块或成员绑定到当前命名空间；首次导入可能执行依赖模块的顶层代码。"
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            target_text = "、".join(_expr(target) for target in targets)
            if isinstance(node, ast.AugAssign):
                operation = _expr(node)
                step["text"] = f"更新 {target_text}：{operation}。先读取原值再进行运算；是否原地修改对象由其类型决定。"
                kind, explanation, expression = "update", "读取已有值并执行增量运算", operation
            elif value is None:
                step["text"] = f"为 {target_text} 声明类型标注 {_expr(node.annotation)}；这一行没有给它赋值。"
                return False
            else:
                step["text"] = f"先计算 {_expr(value)}，再将结果绑定或写入 {target_text}。" + ("此处有解包，右侧各部分按结构分配。" if any(isinstance(t, (ast.Tuple, ast.List)) for t in targets) else "")
                kind, explanation, expression = "assign", "把右侧表达式结果绑定到名称或写入对象位置；表达式文字不是已求出的值", _expr(value)
            for target in targets:
                names = _target_names(target) or [_expr(target)]
                for name in names:
                    self._event(scope, name, node.lineno, kind, expression, explanation, active)
                if isinstance(target, (ast.Attribute, ast.Subscript)):
                    self._calls(scope, target, active)
            self._calls(scope, value, active)
        elif isinstance(node, ast.Return):
            step["text"] = f"返回 {_expr(node.value) or 'None'}，结束本次函数执行；finally 或上下文清理仍可能在离开前运行。"
            scope.symbol["returns"].append({"line": node.lineno, "expression": _expr(node.value) or "None", "description": "显式 return 的候选输出；其值需在此路径实际执行时计算", "condition": active, "kind": "return"})
            self._calls(scope, node.value, active)
            return True
        elif isinstance(node, ast.If):
            test = _expr(node.test)
            step["text"] = f"判断 {test} 的真假；真时进入 if 分支，假时进入 else 分支（若有），每次只选择符合条件的一条路径。"
            self._calls(scope, node.test, active)
            if self._main_guard(node.test) and isinstance(scope.node, ast.Module):
                self.program["entry_points"].append({"path": scope.path, "line": node.lineno, "symbol_id": scope.symbol["id"], "description": "直接作为程序运行时进入 __main__ 分支；普通 import 不进入该分支。"})
            a = self._suite(scope, node.body, _condition(active, f"条件 {test} 为真"))
            b = self._suite(scope, node.orelse, _condition(active, f"条件 {test} 为假")) if node.orelse else False
            return a and b
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            if isinstance(node, ast.While):
                test = _expr(node.test)
                step["text"] = f"循环判断 {test}；条件为真时执行循环体，然后再次判断，可能执行零次或多次。"
                self._calls(scope, node.test, active)
                loop_condition = f"循环条件 {test} 为真且进入本轮迭代"
            else:
                target, iterable = _expr(node.target), _expr(node.iter)
                step["text"] = f"{'异步' if isinstance(node, ast.AsyncFor) else ''}遍历 {iterable}，每轮把取到的一项绑定给 {target}，再执行循环体；迭代次数依赖输入。"
                self._calls(scope, node.iter, active)
                loop_condition = f"{iterable} 仍有下一项并进入本轮迭代"
                for name in _target_names(node.target) or [target]:
                    self._event(scope, name, node.lineno, "iteration", f"{iterable} 的当前迭代项", "每轮更新循环目标；可能一次也不绑定", _condition(active, loop_condition))
            self._suite(scope, node.body, _condition(active, loop_condition))
            self._suite(scope, node.orelse, _condition(active, "循环自然结束且未被 break 中断，才执行循环 else"))
        elif isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            step["text"] = "进入异常处理结构：先执行 try；出现匹配异常时转到处理分支；无异常并正常走完 try 才执行 else；离开结构时执行 finally（若有）。"
            body_stops = self._suite(scope, node.body, _condition(active, "进入 try，尚未发生转入处理分支的异常"))
            handlers_stop = []
            for handler in node.handlers:
                cond = _condition(active, f"try 中产生匹配 {_expr(handler.type) or '任意异常'} 的异常")
                self._calls(scope, handler.type, _condition(active, "try 中产生异常且正在计算待匹配的异常类型"))
                if handler.name:
                    self._event(scope, handler.name, handler.lineno, "exception", "本次匹配的异常对象", "异常处理分支开始时绑定；离开该处理分支后 Python 会清除此绑定", cond)
                handlers_stop.append(self._suite(scope, handler.body, cond))
                if handler.name:
                    self._event(scope, handler.name, handler.end_lineno, "delete", "清除异常名称绑定", "Python 在退出 except 分支时清除异常名称，避免异常对象引用长期保留", _condition(cond, "即将退出该异常处理分支（也包含提前返回等离开路径）"))
            else_stops = self._suite(scope, node.orelse, _condition(active, "try 未发生异常且未通过 return/break/continue 提前离开"))
            finally_stops = self._suite(scope, node.finalbody, _condition(active, "控制流离开 try 结构并进入 finally；进程被强制终止等情况除外"))
            return finally_stops or ((body_stops or else_stops) and all(handlers_stop))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            step["text"] = "进入上下文管理器 " + "、".join(_expr(item.context_expr) for item in node.items) + "；成功进入后执行代码块，退出时触发清理逻辑，管理器也可能处理异常。"
            for item in node.items:
                self._calls(scope, item.context_expr, active)
                if item.optional_vars:
                    for name in _target_names(item.optional_vars) or [_expr(item.optional_vars)]:
                        self._event(scope, name, node.lineno, "context", _expr(item.context_expr) + " 的进入结果", "as 绑定的是 __enter__/__aenter__ 的返回值，未必是管理器对象本身", _condition(active, "上下文管理器成功进入"))
            self._suite(scope, node.body, _condition(active, "所有上下文管理器均成功进入"))
        elif isinstance(node, ast.Match):
            step["text"] = f"对 {_expr(node.subject)} 做结构模式匹配；选择首个模式匹配且守卫条件成立的 case，可能没有分支匹配。"
            self._calls(scope, node.subject, active)
            for case in node.cases:
                matched = _condition(active, f"前序 case 未命中，模式 {_expr(case.pattern)} 匹配")
                cond = _condition(matched, f"守卫 {_expr(case.guard)} 为真") if case.guard else matched
                self._calls(scope, case.guard, matched)
                for part in ast.walk(case.pattern):
                    name = getattr(part, "name", None) or (getattr(part, "rest", None) if isinstance(part, ast.MatchMapping) else None)
                    if name:
                        self._event(scope, name, getattr(part, "lineno", node.lineno), "pattern", _expr(node.subject), "模式匹配成功时绑定匹配到的对应部分", cond)
                self._suite(scope, case.body, cond)
        elif isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                step["text"] = "字符串说明或文档；不把其文字当作已实现行为。"
            else:
                step["text"] = f"求值 {_expr(node.value)}；若未赋值或返回，结果通常不再使用，但调用可能修改状态、执行 I/O 或抛出异常。"
                if isinstance(node.value, ast.Await):
                    step["text"] += "await 会等待可等待对象，期间可能把执行机会交回事件循环。"
            self._calls(scope, node.value, active)
        elif isinstance(node, ast.Raise):
            step["text"] = f"抛出 {_expr(node.exc) or '当前正在处理的异常'}，中断当前顺序块并寻找异常处理或清理路径。"
            self._calls(scope, node.exc, active)
            self._calls(scope, node.cause, active)
            return True
        elif isinstance(node, (ast.Break, ast.Continue)):
            step["text"] = "结束最近一层循环；该循环的 else 不会执行。" if isinstance(node, ast.Break) else "跳过本轮剩余语句，进入最近一层循环的下一轮判断或取值。"
            step["text"] += "若需穿过 finally 或上下文管理器，先完成相应清理。"
            return True
        elif isinstance(node, ast.Assert):
            step["text"] = f"断言 {_expr(node.test)} 为真；普通模式下为假会抛出 AssertionError。Python 优化模式可跳过 assert，因此不能把它当作必然执行的输入校验。"
            self._calls(scope, node.test, active)
            self._calls(scope, node.msg, _condition(active, "断言失败且断言未被优化禁用"))
        elif isinstance(node, ast.Delete):
            step["text"] = f"删除名称绑定或对象位置：{', '.join(_expr(target) for target in node.targets)}；这不保证对象立即销毁。"
            for target in node.targets:
                self._event(scope, _expr(target), node.lineno, "delete", "删除绑定或成员", "后续访问可能失败，具体结果取决于对象语义", active)
                self._calls(scope, target, active)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            step["text"] = f"声明 {', '.join(node.names)} 使用{'模块' if isinstance(node, ast.Global) else '外层函数'}作用域的绑定；声明本身不赋值。"
        elif isinstance(node, ast.Pass):
            step["text"] = "占位语句 pass，本行不执行额外操作。"
        else:
            step["text"] = f"执行 {type(node).__name__} 语法：{_expr(node)}；该语法的细节需结合源码阅读。"
        return False

    @staticmethod
    def _main_guard(node: ast.AST) -> bool:
        if not isinstance(node, ast.Compare) or len(node.ops) != 1 or not isinstance(node.ops[0], ast.Eq):
            return False
        sides = (node.left, node.comparators[0])
        return any(isinstance(sides[i], ast.Name) and sides[i].id == "__name__" and isinstance(sides[1 - i], ast.Constant) and sides[1 - i].value == "__main__" for i in (0, 1))
