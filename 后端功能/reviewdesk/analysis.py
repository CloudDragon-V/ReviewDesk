"""Bounded, read-only Python analysis; no imported project code is executed.

Rules deliberately report local, explainable evidence rather than attempt whole-
program type inference. The graph contains only statically resolvable project
relationships; an absent edge does not prove that two objects are unrelated.
"""

from __future__ import annotations

import ast
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import stat
import tokenize
import warnings

from .models import Analysis, Finding, GraphEdge, GraphNode, SourceFile


MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 20_000
MAX_DEPTH = 20
MAX_AST_NODES = 80_000
MAX_FINDINGS = 2000
_EXCLUDED_DIRS = {
    "__pycache__", "venv", "env", "node_modules", "site-packages", "dist",
    "build", "vendor", "vendors", "htmlcov", "coverage", "$recycle.bin",
}
_SENSITIVE_FILES = {
    "secrets.py", "credentials.py", "private_key.py", "api_keys.py",
    "local_settings.py", "settings_local.py", "secret.py", "passwords.py",
}
_GRAPH_NOTE = (
    "关系图来自 Python AST：仅显示可静态定位的项目内定义、导入和调用；"
    "不推断动态导入、反射、实例类型或运行时分派，源码不会被执行。"
)


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _valid_path(path: str) -> str | None:
    """Reject absolute, traversal and control-containing untrusted paths."""
    if not isinstance(path, str) or not path or any(ord(c) < 32 for c in path):
        return None
    normalized = path.replace("\\", "/")
    value = PurePosixPath(normalized)
    if value.is_absolute() or ":" in normalized or ".." in value.parts:
        return None
    if not value.parts or any(part.startswith(".") for part in value.parts):
        return None
    return value.as_posix()


def scan_project(root: Path, max_files: int = 500) -> Analysis:
    """Read a bounded snapshot without following symlinks or Windows junctions.

    Input errors raise ValueError/OSError. Individual unreadable, oversized or
    unsupported files are skipped and counted in limitations. Python encoding
    declarations are honored. Snapshot construction never writes to the target.
    """
    if not isinstance(max_files, int) or isinstance(max_files, bool) or not 1 <= max_files <= 2000:
        raise ValueError("max_files 必须是 1 到 2000 的整数")
    root = Path(root).absolute()
    # Checking ancestors also rejects a normal-looking path inside a junction.
    for ancestor in (root, *root.parents):
        if _is_link(ancestor.lstat()):
            raise ValueError("为避免越界读取，请选择不经过符号链接或目录联接的真实目录")
    if not root.is_dir():
        raise ValueError("请选择一个存在的项目目录")
    counts: dict[str, int] = {}
    files: list[SourceFile] = []
    total_bytes = 0
    seen_entries = 0
    stack = [(root, 0)]

    def skip(reason: str) -> None:
        counts[reason] = counts.get(reason, 0) + 1

    while stack and len(files) < max_files and seen_entries < MAX_ENTRIES:
        directory, depth = stack.pop()
        try:
            # Recheck just before enumerating, not only when discovering a child.
            if _is_link(directory.lstat()):
                skip("符号链接或目录联接")
                continue
            entries: list[os.DirEntry[str]] = []
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    if seen_entries >= MAX_ENTRIES:
                        break
                    seen_entries += 1
                    entries.append(entry)
            entries.sort(key=lambda item: (item.name.casefold(), item.name))
        except OSError:
            skip("无法读取的目录")
            continue
        children: list[tuple[Path, int]] = []
        for entry in entries:
            if len(files) >= max_files:
                break
            name = entry.name.casefold()
            if name.startswith("."):
                skip("隐藏文件或目录")
                continue
            try:
                info = entry.stat(follow_symlinks=False)
                if _is_link(info):
                    skip("符号链接或目录联接")
                    continue
                if getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 2):
                    skip("隐藏文件或目录")
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if name in _EXCLUDED_DIRS or name.endswith(".egg-info"):
                        skip("生成文件或依赖目录")
                    elif depth >= MAX_DEPTH:
                        skip("超过目录深度限制")
                    else:
                        children.append((Path(entry.path), depth + 1))
                    continue
                if not stat.S_ISREG(info.st_mode) or not name.endswith(".py"):
                    continue
                if name in _SENSITIVE_FILES or name.startswith(("secret_", "credentials_")):
                    skip("敏感配置文件")
                    continue
                if info.st_size > MAX_FILE_BYTES:
                    skip("超过单文件 256 KiB 限制")
                    continue
                if total_bytes + info.st_size > MAX_TOTAL_BYTES:
                    skip("超过项目 8 MiB 限制")
                    continue
                path = Path(entry.path)
                # Windows DirEntry.stat() intentionally leaves st_ino/st_dev at
                # zero; lstat supplies file identity for the handle comparison.
                info = path.lstat()
                if _is_link(info) or not stat.S_ISREG(info.st_mode):
                    skip("读取时发生变化的文件")
                    continue
                # fstat comparison catches a replaced file; O_NOFOLLOW is used
                # when the host supports it. Concurrent directory replacement
                # is not an OS sandbox; review a stable project snapshot.
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as handle:
                    opened = os.fstat(handle.fileno())
                    if _is_link(opened) or not stat.S_ISREG(opened.st_mode) or (
                        opened.st_dev, opened.st_ino
                    ) != (info.st_dev, info.st_ino):
                        skip("读取时发生变化的文件")
                        continue
                    raw = handle.read(MAX_FILE_BYTES + 1)
                if len(raw) > MAX_FILE_BYTES or total_bytes + len(raw) > MAX_TOTAL_BYTES:
                    skip("读取时超过大小限制的文件")
                    continue
                if b"\x00" in raw:
                    skip("含二进制空字节的文件")
                    continue
                encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
                content = raw.decode(encoding)
                relative = path.relative_to(root).as_posix()
                if _valid_path(relative) is None:
                    skip("不支持的文件路径")
                    continue
                files.append(SourceFile(relative, content, hashlib.sha256(raw).hexdigest()))
                total_bytes += len(raw)
            except (OSError, UnicodeError, SyntaxError, LookupError):
                skip("读取失败或编码不支持的文件")
        stack.extend(reversed(children))
    result = analyze_sources(files)
    if len(files) >= max_files:
        result.limitations.append(f"扫描达到 {max_files} 个 Python 文件上限，后续文件未读取。")
    if seen_entries >= MAX_ENTRIES:
        result.limitations.append(f"扫描达到 {MAX_ENTRIES} 个目录项上限，后续目录项未读取。")
    result.limitations.extend(f"已跳过{reason}：{count} 项。" for reason, count in sorted(counts.items()))
    result.limitations.append("仅扫描 .py 文件；扫描时请保持项目目录稳定，文件链接检查不替代操作系统沙箱。")
    return result


def _module_name(path: str) -> str:
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or "__init__"


def _definition_id(path: str, qualifier: str = "") -> str:
    return f"{path}::{qualifier or '<module>'}"


def analyze_sources(files: list[SourceFile]) -> Analysis:
    """Analyze validated in-memory Python files, without running or importing them."""
    result = Analysis(limitations=[_GRAPH_NOTE])
    trees: dict[str, ast.Module] = {}
    paths: set[str] = set()
    total_bytes = 0
    omitted = 0
    for source in files[:2000]:
        path = _valid_path(source.path)
        if path is None or path in paths or not isinstance(source.content, str):
            omitted += 1
            continue
        raw = source.content.encode("utf-8", errors="replace")
        if len(raw) > MAX_FILE_BYTES or total_bytes + len(raw) > MAX_TOTAL_BYTES or "\x00" in source.content:
            omitted += 1
            continue
        total_bytes += len(raw)
        paths.add(path)
        snapshot = SourceFile(path, source.content, source.sha256 or hashlib.sha256(raw).hexdigest())
        result.files.append(snapshot)
        result.nodes.append(GraphNode(_definition_id(path), _module_name(path), "module", path, 1))
        try:
            tree = ast.parse(source.content, filename=path)
            if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
                result.limitations.append(f"{path}：语法树超过 {MAX_AST_NODES} 节点上限，仅保留源码快照。")
                continue
            # ast.parse alone accepts return/break outside a valid scope.
            # Compilation validates those scopes without executing any code.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                compile(tree, path, "exec")
            trees[path] = tree
            _Rules(snapshot, result).visit(tree)
        except SyntaxError as error:
            from .context import source_lines
            line = min(max(1, error.lineno or 1), max(1, len(source_lines(source.content))))
            _Rules(snapshot, result).add(
                "PY001", line, line, "correctness", "high", "Python 语法错误",
                f"先修复语法错误，再进行结构审查。解析器信息：{error.msg}",
            )
        except (RecursionError, MemoryError, ValueError):
            result.limitations.append(f"{path}：源码过于复杂，已中止该文件的 AST 分析。")
    if len(files) > 2000:
        omitted += len(files) - 2000
    if omitted:
        result.limitations.append(f"输入中有 {omitted} 个文件因路径、重复、二进制或大小限制被跳过。")
    try:
        _build_graph(trees, result)
    except (RecursionError, MemoryError):
        result.limitations.append("部分语法树过深，关系图不完整。")
    result.findings.sort(key=lambda finding: (finding.path, finding.start_line, finding.rule_id))
    result.nodes = list({node.id: node for node in result.nodes}.values())
    result.edges = sorted(
        { (edge.source, edge.target, edge.kind): edge for edge in result.edges }.values(),
        key=lambda edge: (edge.source, edge.kind, edge.target),
    )
    return result


class _Rules(ast.NodeVisitor):
    def __init__(self, source: SourceFile, result: Analysis) -> None:
        from .context import redact_secrets, source_lines

        self.source = source
        # Redact before taking an excerpt; otherwise the middle of a multiline
        # password can lose the assignment marker that identifies it as secret.
        self.lines = source_lines(redact_secrets(source.content))
        self.result = result
        self.scopes: list[set[str]] = []
        self.emitted: set[tuple[str, int, int]] = set()

    def add(self, rule: str, start: int, end: int, category: str, severity: str,
            title: str, recommendation: str, confidence: float = 0.95) -> None:
        if len(self.result.findings) >= MAX_FINDINGS:
            note = f"静态建议达到 {MAX_FINDINGS} 条上限；后续建议已省略，请缩小项目范围。"
            if note not in self.result.limitations:
                self.result.limitations.append(note)
            return
        end = max(start, min(end, len(self.lines)))
        identity = (rule, start, end)
        if identity in self.emitted:
            return
        self.emitted.add(identity)
        evidence = "\n".join(self.lines[start - 1:min(end, start + 2)])
        evidence = evidence[:600]
        self.result.findings.append(Finding(
            self.source.path, start, end, category, severity, title, evidence,
            recommendation, rule_id=rule, confidence=confidence,
        ))

    def report(self, node: ast.AST, rule: str, category: str, severity: str,
               title: str, recommendation: str, confidence: float = 0.95) -> None:
        self.add(rule, node.lineno, getattr(node, "end_lineno", node.lineno),
                 category, severity, title, recommendation, confidence)

    def _shadowed(self, name: str) -> bool:
        return any(name in scope for scope in reversed(self.scopes))

    def _enter_scope(self, node: ast.AST) -> None:
        collector = _Bindings()
        for child in getattr(node, "body", []):
            collector.visit(child)
        arguments = getattr(node, "args", None)
        if arguments:
            collector.names.update(arg.arg for arg in (
                arguments.posonlyargs + arguments.args + arguments.kwonlyargs
            ))
            if arguments.vararg:
                collector.names.add(arguments.vararg.arg)
            if arguments.kwarg:
                collector.names.add(arguments.kwarg.arg)
        self.scopes.append(collector.names)

    def visit_Module(self, node: ast.Module) -> None:
        self._enter_scope(node)
        self.generic_visit(node)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for default in node.args.defaults + [item for item in node.args.kw_defaults if item is not None]:
            if isinstance(default, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
                self.report(default, "PY002", "correctness", "medium", "可变对象作为默认参数",
                            "默认值只在函数定义时创建，后续调用会共享同一对象；改为 None 并在函数内创建。")
        self._enter_scope(node)
        self.generic_visit(node)
        self.scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._enter_scope(node)
        self.generic_visit(node)
        self.scopes.pop()

    def generic_visit(self, node: ast.AST) -> None:
        # Only statements after a direct terminator are certain to be unreachable.
        for _, value in ast.iter_fields(node):
            if isinstance(value, list) and value and all(isinstance(item, ast.stmt) for item in value):
                terminated = False
                for statement in value:
                    if terminated:
                        self.report(statement, "PY005", "redundancy", "medium", "不可达语句",
                                    "前一条语句已经直接结束该控制流；删除此语句或调整终止语句的位置。")
                    if isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                        terminated = True
        super().generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self.report(node, "PY003", "correctness", "medium", "裸 except 捕获所有异常",
                        "明确捕获预期异常类型；裸 except 也会拦截 KeyboardInterrupt 和 SystemExit。")
        broad = node.type is None or isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}
        if broad and node.body and all(isinstance(item, ast.Pass) for item in node.body):
            self.report(node, "PY004", "correctness", "medium", "异常被静默吞掉",
                        "为预期失败添加明确处理、记录或重新抛出；若忽略是设计要求，缩小捕获范围并说明原因。", 0.9)
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and isinstance(node.right, ast.Constant):
            if type(node.right.value) in {int, float, complex, bool} and node.right.value == 0:
                # String formatting uses % with different semantics.
                string_mod = isinstance(node.op, ast.Mod) and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)
                if not string_mod:
                    self.report(node, "PY006", "correctness", "high", "除数为零",
                                "分母为字面量零，数值运算将失败；检查表达式并对实际分母做有效性判断。", 0.96)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"} and not self._shadowed(node.func.id):
            if node.args and not isinstance(node.args[0], ast.Constant):
                self.report(node, "PY007", "correctness", "medium", "动态执行代码存在注入风险",
                            "确认传入代码完全可信；普通数据解析应使用 json.loads 或 ast.literal_eval，并校验数据结构。", 0.85)
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        seen: set[object] = set()
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, (str, int, float, bytes, bool, type(None))):
                if key.value in seen:
                    self.report(key, "PY008", "correctness", "medium", "字典中有重复的常量键",
                                "后出现的同值键会覆盖前面的值；确认键名是否写错，并合并或删除重复项。")
                seen.add(key.value)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        operands = [node.left, *node.comparators]
        for index, operator in enumerate(node.ops):
            if isinstance(operator, (ast.Is, ast.IsNot)) and any(
                isinstance(value, ast.Constant) and value.value is not None
                and value.value is not Ellipsis and type(value.value) is not bool
                for value in operands[index:index + 2]
            ):
                self.report(node, "PY009", "correctness", "medium", "使用 is 比较普通字面量",
                            "is 比较对象身份，数值或字符串内容比较通常应使用 == / !=；None 等单例保持 is。")
                break
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        if isinstance(node.test, ast.Tuple) and node.test.elts:
            self.report(node, "PY010", "correctness", "high", "非空元组使断言恒为真",
                        "assert (condition, message) 检查的是非空元组；改为 assert condition, message。")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Name):
            if node.targets[0].id == node.value.id:
                self.report(node, "PY011", "redundancy", "info", "变量赋值给自身",
                            "此赋值没有更新变量；确认是否把预期的另一个变量写错。若用于作用域声明则保留并说明。", 0.85)
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        if isinstance(node.test, ast.Constant):
            inactive = node.orelse if bool(node.test.value) else node.body
            if inactive:
                self.add("PY012", inactive[0].lineno, inactive[-1].end_lineno,
                         "redundancy", "info", "常量条件导致分支永不执行",
                         "当前条件固定，所标记分支不会执行；检查是否遗留了调试开关或删除无用分支。", 0.9)
        elif node.orelse and len(node.body) == len(node.orelse) and all(
            ast.dump(left, include_attributes=False) == ast.dump(right, include_attributes=False)
            for left, right in zip(node.body, node.orelse)
        ):
            self.report(node, "PY013", "redundancy", "info", "条件两侧执行完全相同的语句",
                        "可以合并重复分支；合并前保留条件表达式可能产生的副作用或异常。", 0.9)
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        if isinstance(node.test, ast.Constant) and not bool(node.test.value) and node.body:
            self.add("PY012", node.body[0].lineno, node.body[-1].end_lineno,
                     "redundancy", "info", "常量条件导致循环体永不执行",
                     "循环条件固定为假；检查条件是否写错，或删除无用的循环体。")
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try | ast.TryStar) -> None:
        for statement in node.finalbody:
            if isinstance(statement, (ast.Return, ast.Break, ast.Continue)):
                self.report(statement, "PY014", "correctness", "medium", "finally 中结束控制流会抑制异常",
                            "finally 应负责清理；将 return / break / continue 移至正常流程，避免覆盖原异常或返回值。")
        self.generic_visit(node)

    visit_TryStar = visit_Try


class _Bindings(ast.NodeVisitor):
    """Collect assignments without confusing child lexical scopes with parents."""
    def __init__(self) -> None:
        self.names: set[str] = set()
        self.assigned: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)
            self.assigned.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(alias.asname or alias.name for alias in node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
            self.assigned.add(node.name)
        self.generic_visit(node)


class _Graph(ast.NodeVisitor):
    def __init__(self, path: str, result: Analysis, definitions: dict[tuple[str, str], str],
                 modules: dict[str, str], collect: bool) -> None:
        self.path, self.result, self.definitions, self.modules = path, result, definitions, modules
        self.collect = collect
        self.qualifiers = [""]
        self.kinds = ["module"]
        self.aliases: list[dict[str, tuple[str, str]]] = [{}]
        self.blocked: list[set[str]] = [set()]

    def _rebindings(self, node: ast.AST) -> set[str]:
        collector = _Bindings()
        for statement in getattr(node, "body", []):
            collector.visit(statement)
        arguments = getattr(node, "args", None)
        if arguments:
            collector.assigned.update(arg.arg for arg in arguments.posonlyargs + arguments.args + arguments.kwonlyargs)
            if arguments.vararg:
                collector.assigned.add(arguments.vararg.arg)
            if arguments.kwarg:
                collector.assigned.add(arguments.kwarg.arg)
        return collector.assigned

    def visit_Module(self, node: ast.Module) -> None:
        self.blocked[0] = self._rebindings(node)
        self.generic_visit(node)

    @property
    def current(self) -> str:
        return _definition_id(self.path, self.qualifiers[-1])

    def _definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        qualifier = ".".join(part for part in (self.qualifiers[-1], node.name) if part)
        identifier = _definition_id(self.path, qualifier)
        kind = "class" if isinstance(node, ast.ClassDef) else "method" if self.kinds[-1] == "class" else "function"
        if self.collect:
            self.definitions[(self.path, qualifier)] = identifier
            self.result.nodes.append(GraphNode(identifier, qualifier, kind, self.path, node.lineno))
            self.result.edges.append(GraphEdge(self.current, identifier, "contains"))
        # Defaults/decorators execute in the parent lexical scope.
        for decorator in node.decorator_list:
            self.visit(decorator)
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                self.visit(base)
        else:
            for default in node.args.defaults + [item for item in node.args.kw_defaults if item is not None]:
                self.visit(default)
        self.qualifiers.append(qualifier)
        self.kinds.append(kind)
        self.aliases.append({})
        self.blocked.append(self._rebindings(node))
        for statement in node.body:
            self.visit(statement)
        self.aliases.pop()
        self.blocked.pop()
        self.kinds.pop()
        self.qualifiers.pop()

    visit_FunctionDef = _definition
    visit_AsyncFunctionDef = _definition
    visit_ClassDef = _definition

    def _import_module(self, node: ast.ImportFrom) -> str:
        if not node.level:
            return node.module or ""
        current = _module_name(self.path).split(".")
        if not self.path.endswith("__init__.py"):
            current.pop()
        if node.level > len(current):
            return ""
        package = current[:len(current) - node.level + 1]
        return ".".join(package + ([node.module] if node.module else []))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            module_path = self.modules.get(alias.name)
            if module_path and not self.collect:
                self.result.edges.append(GraphEdge(self.current, _definition_id(module_path), "imports"))
            if alias.asname:
                self.aliases[-1][alias.asname] = (alias.name, "")
            else:
                self.aliases[-1][alias.name.split(".")[0]] = (alias.name.split(".")[0], "")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = self._import_module(node)
        for alias in node.names:
            if alias.name == "*":
                continue
            full_module = f"{module}.{alias.name}" if module else alias.name
            module_path = self.modules.get(full_module) or self.modules.get(module)
            if module_path and not self.collect:
                self.result.edges.append(GraphEdge(self.current, _definition_id(module_path), "imports"))
            self.aliases[-1][alias.asname or alias.name] = (
                (full_module, "") if full_module in self.modules else (module, alias.name)
            )

    def _resolve(self, node: ast.AST) -> str | None:
        components: list[str] = []
        while isinstance(node, ast.Attribute):
            components.insert(0, node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        components.insert(0, node.id)
        head = components[0]
        if head in {"self", "cls"} and len(components) > 1:
            for index in range(len(self.kinds) - 1, -1, -1):
                if self.kinds[index] == "class":
                    return self.definitions.get((self.path, self.qualifiers[index] + "." + ".".join(components[1:])))
        for index in reversed(range(len(self.qualifiers))):
            prefix, kind = self.qualifiers[index], self.kinds[index]
            # Methods do not lexically capture their containing class namespace.
            if kind == "class" and self.kinds[-1] != "class":
                continue
            if head in self.blocked[index]:
                return None
            aliases = self.aliases[index]
            if head in aliases:
                module, symbol = aliases[head]
                tail = ([symbol] if symbol else []) + components[1:]
                # import pkg.tools; pkg.tools.function()
                for boundary in range(len(tail), -1, -1):
                    candidate = ".".join([module, *tail[:boundary]])
                    path = self.modules.get(candidate)
                    if path:
                        return self.definitions.get((path, ".".join(tail[boundary:])))
                return None
            qualifier = ".".join(part for part in (prefix, ".".join(components)) if part)
            if (self.path, qualifier) in self.definitions:
                return self.definitions[(self.path, qualifier)]
        return None

    def visit_Call(self, node: ast.Call) -> None:
        if not self.collect:
            target = self._resolve(node.func)
            if target:
                self.result.edges.append(GraphEdge(self.current, target, "calls"))
        self.generic_visit(node)


def _build_graph(trees: dict[str, ast.Module], result: Analysis) -> None:
    modules = {_module_name(path): path for path in trees}
    # A conventional src layout makes imports resolve without running sys.path.
    for module, path in list(modules.items()):
        if module.startswith("src."):
            modules.setdefault(module[4:], path)
    definitions: dict[tuple[str, str], str] = {
        (path, ""): _definition_id(path) for path in trees
    }
    for collect in (True, False):
        for path, tree in trees.items():
            _Graph(path, result, definitions, modules, collect).visit(tree)
