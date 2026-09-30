"""Evidence-linked Chinese reading manuals built without a model or code execution."""

from __future__ import annotations

import html
import re
from collections import Counter, defaultdict
from urllib.parse import quote

from .semantic_notes import reading_routes, semantic_notes


def count_characters(text: str) -> int:
    """Count non-whitespace Unicode characters, not UTF-8 bytes or model tokens."""
    return sum(not char.isspace() for char in text)


def _text(value: object) -> str:
    """Treat code comments and model strings as quoted content, never Markdown syntax."""
    value = html.escape(str(value), quote=False).replace("\r", "").replace("\n", " ")
    return re.sub(r"([\\`*_{}\[\]()#+|>~])", r"\\\1", value)


def _code(value: object) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ").replace("|", "\\|")
    width = max((len(run) for run in re.findall(r"`+", text)), default=0) + 1
    fence = "`" * width
    return fence + " " + text + " " + fence


def source_reference(path: str, start: int, end: int | None = None) -> str:
    """Link a source span; the viewer resolves it within the report's revision."""
    finish = end if end is not None else start
    suffix = f"–{finish}" if finish != start else ""
    label = f"【{_text(path)}:L{start}{suffix}】"
    target = f"reviewdesk-source:{quote(path, safe='')}?start={start}&end={finish}"
    return f"[{label}]({target})"


def _at(symbol: dict, line: int | None = None, end: int | None = None) -> str:
    start = line if line is not None else symbol.get("start_line", 1)
    finish = end if end is not None else start
    if symbol.get("line_count") == 0:
        return f"【{_text(symbol.get('path', '未知文件'))}：空文件，无可定位的源码行】"
    if "line_count" in symbol and (start > symbol["line_count"] or finish > symbol["line_count"]):
        return f"【{_text(symbol.get('path', '未知文件'))}:L{start}（解析位置超出实际源码）】"
    return source_reference(symbol.get("path", "未知文件"), start, finish)


def _extra_condition(value: str) -> str:
    """Keep the relevant branch evidence without repeating the common precondition."""
    for common in ("控制流沿当前路径实际到达本行", "控制流实际到达本行，且前序语句未提前中断"):
        if value == common:
            return ""
        value = value.removeprefix(common + "；并且")
    return value


def _step_sections(symbol: dict) -> list[str]:
    lines = ["#### 按源码结构理解执行过程", ""]
    steps = symbol.get("steps", [])
    if not steps:
        lines.extend(["未提取到可说明的语句；请结合语法错误提示和原始文件确认。" + _at(symbol), ""])
        return lines
    for index, step in enumerate(steps, 1):
        condition = _extra_condition(step.get("condition", ""))
        lines.append(f"{index}. **第 {step.get('line', '?')} 行**：{_text(step.get('text', ''))} {_at(symbol, step.get('line', 1))}")
        if condition:
            lines.append(f"   额外条件：{_text(condition)}。")
        lines.append("")
    return lines


def _variable_sections(symbol: dict) -> list[str]:
    lines = ["#### 变量怎样建立和变化", "",
             "以下按源码位置整理绑定与修改事件；不同分支的行不能拼接成一次真实运行，表达式也不是已经算出的变量值。" + _at(symbol, end=symbol.get("end_line", 1)), ""]
    variables = symbol.get("variables", [])
    if not variables:
        lines.extend(["本作用域未发现显式参数绑定、赋值或已识别的常见修改操作；这不排除被调用函数产生副作用。" + _at(symbol), ""])
    for variable in variables:
        lines.extend([f"**{_code(variable.get('name', ''))}**，所在作用域 {_code(variable.get('scope', ''))}：", ""])
        for event in variable.get("events", []):
            explanation = event.get("explanation", "").removesuffix("；实际值取决于该路径是否执行及运行时输入")
            condition = _extra_condition(event.get("condition", ""))
            extra = f" 额外条件：{_text(condition)}。" if condition else ""
            lines.append(f"- 第 {event.get('line', '?')} 行：{_text(explanation)}；表达式/来源为 {_code(event.get('expression', ''))}。{extra}{_at(symbol, event.get('line', 1))}")
        lines.append("")
    return lines


def _call_sections(symbol: dict, calls: list[dict], lookup: dict[str, dict]) -> list[str]:
    lines = ["#### 它调用了谁，为什么需要关注", ""]
    if not calls:
        lines.extend(["未识别到该作用域直接写出的函数调用。属性访问、运算符、迭代器或上下文管理器仍可能隐式调用 Python 协议方法。" + _at(symbol), ""])
        return lines
    for call in calls:
        target = lookup.get(call.get("target"))
        target_text = "静态关联到 " + _code(target["qualified_name"]) + " " + _at(target) if target else "**目标未确认**，需要依赖源码、对象实际类型或运行时信息进一步确认"
        condition = _extra_condition(call.get("condition", ""))
        extra = f" 额外条件：{_text(condition)}。" if condition else ""
        lines.extend([f"- {_code(call.get('expression') or call.get('callee', ''))}：{target_text}。{_at(symbol, call.get('line', 1))}",
                      f"  依据：{_text(call.get('description', ''))}{extra}", ""])
    return lines


def _outputs(symbol: dict) -> list[str]:
    lines = ["#### 输出与何时结束", ""]
    if symbol.get("return_annotation"):
        lines.extend(["返回类型标注是 " + _code(symbol["return_annotation"]) + "。这是接口声明；Python 不会仅因为这条标注就自动保证结果属于该类型。" + _at(symbol), ""])
    for item in symbol.get("returns", []):
        lines.append(f"- 第 {item.get('line', '?')} 行：{_text(item.get('description', ''))}；表达式为 {_code(item.get('expression', '') or 'None')}。条件：{_text(_extra_condition(item.get('condition', '')) or '控制流实际到达此处')}。{_at(symbol, item.get('line', 1))}")
    if symbol.get("returns"):
        lines.append("")
    if symbol.get("is_generator"):
        message = "这是生成器。" + ("调用异步生成器函数得到异步生成器对象，需要异步迭代才能逐步执行。" if symbol.get("is_async") else "调用函数先得到生成器对象，通常到 next()、for 等开始迭代时才执行函数体；yield 之后在下一次迭代时继续。")
        message += "产出的元素与生成器终止时的返回语义不同，不能把每条 yield 当作普通函数 return。"
    elif symbol.get("is_async"):
        message = "这是 async 函数。直接调用一般得到协程对象，需要 await 或事件循环调度后才推进函数体；await 的最终结果来自实际走到的返回路径。"
    else:
        message = "调用者只有在实际进入该函数并走到相应路径时才会取得上述输出。"
    message += "如果执行正常到达函数末尾且没有显式返回值，Python 隐式返回 None；如果途中抛出未被处理的异常，则不会产生正常返回结果。"
    lines.extend([message + _at(symbol, end=symbol.get("end_line", 1)), ""])
    return lines


def build_manual(program: dict, findings: list[dict], project_name: str) -> str:
    """Explain a static Program as a readable, evidence-linked Chinese Markdown manual."""
    if not isinstance(program, dict) or program.get("language") != "python":
        raise ValueError("当前说明书生成器需要 Python 分析结果。")
    symbols = program.get("symbols", [])
    lookup = {symbol["id"]: symbol for symbol in symbols}
    calls_by_source: dict[str, list[dict]] = defaultdict(list)
    for call in program.get("calls", []):
        calls_by_source[call.get("source", "")].append(call)
    kinds = Counter(symbol.get("kind") for symbol in symbols)
    calls = program.get("calls", [])
    resolved = sum(bool(call.get("resolved")) for call in calls)
    lines = [f"# {_text(project_name or '未命名项目')}：Python 代码阅读说明书", "",
             "这份说明书面向第一次接触本项目的人。它把代码拆成模块、函数、调用关系和带条件的变量事件，再把这些证据串成阅读路线。分析过程没有运行你的代码，因此它描述源码允许发生什么，实际输入、运行次数和最终变量值仍需运行验证。", "",
             "## 1. 先建立整体印象", "",
             f"本次快照包含 **{len(program.get('files', []))} 个文件**、**{kinds['class']} 个类**、**{kinds['function'] + kinds['method']} 个函数或方法**。记录了 **{len(calls)} 处显式调用**，其中 **{resolved} 处**找到项目内静态目标，**{len(calls) - resolved} 处**保留为未确认目标。找到静态目标不等于已经证明该调用一定发生。", "",
             "阅读时可先记住几个概念：模块就是一份 Python 文件；函数把一段操作封装成可调用步骤；类把数据与方法组织到一起；变量名是对值或对象的绑定；分支决定本次走哪条路，循环决定是否重复；return 把控制权与结果交回调用者。", "",
             "后文步骤、调用和变量事件都以控制流实际到达对应行为前提。是否到达取决于分支、循环、异常跳转、提前返回和清理过程；except 或 finally 正可能因为先前发生异常或准备 return 而进入，不能要求这些分支之前没有异常或返回。为了便于阅读，只重复列出额外条件；没有写额外条件不表示这一行一定会执行。", "",
             "## 2. 从哪里开始读", ""]
    entries = program.get("entry_points", [])
    if entries:
        for entry in entries:
            lines.append(f"- **入口候选** {_text(entry.get('path', ''))} 第 {entry.get('line', '?')} 行：{_text(entry.get('description', ''))}。{_at(entry, entry.get('line', 1))}")
        lines.extend(["", "从入口分支中的调用往下追：先看被调用函数接收什么，再看它如何分支、调用其他函数，最后看它返回什么或修改了什么。这里识别的是源码入口，具体启动命令、依赖和配置还需要结合项目 README 与运行环境确认。", ""])
    else:
        lines.extend(["没有发现标准的 `if __name__ == '__main__'` 入口。该快照可能是库模块、框架回调或被其他项目调用的一部分；这不能证明它没有入口。可以从公开函数及模块顶层代码开始，实际启动方式需结合项目配置确认。", ""])
    routes = reading_routes(program)
    if routes:
        lines.extend(["### 入口到主要调用的阅读路线", "",
                      "下面沿已确认的项目内调用边给出阅读顺序；这是挑选主干帮助定位源码，不是运行轨迹或完整调用树。一个函数有多个分支调用时，先列首条静态关联继续向下，其余调用见该函数的详细小节。", ""])
        for route in routes:
            entry = route["entry"]
            parts = [f"入口 {_at(entry, entry['line'])}"]
            for item in route["chain"]:
                target = item["symbol"]
                deferred = "（需要迭代才能推进）" if target.get("is_generator") else "（需要等待或调度才能推进）" if target.get("is_async") else ""
                recursive = "（出现递归边，到此停止展开）" if item["recursive"] else ""
                parts.append(_code(target["qualified_name"]) + deferred + recursive + " " + _at(target))
            lines.extend(["- " + " → ".join(parts), ""])
    notes = semantic_notes(program)
    if notes:
        lines.extend(["### 容易读错的 Python 语义", "",
                      "以下只针对本快照已识别的结构，先解释会影响阅读判断的因果关系，再在后文逐项列出步骤。提到的对象共享、延迟执行和异常路径都以对应语句实际执行为前提。", ""])
        for note in notes:
            lines.extend([f"#### {_text(note['title'])}：{_code(note['scope'])}", "",
                          _text(note["body"]) + " " + _at(note, note["line"]), ""])
    lines.extend(["### 文件分工索引", ""])
    for file in program.get("files", []):
        members = [symbol for symbol in symbols if symbol.get("path") == file["path"] and symbol.get("kind") != "module"]
        lines.append(f"- **{_text(file['path'])}**：{_text(file.get('purpose', ''))} 共有 {file.get('line_count', 0)} 行，包含 {len(members)} 个已提取定义。{_at(file, 1, max(1, file.get('line_count', 1)))}")
        if file.get("parse_error"):
            lines.append("  **解析未完成**：" + _text(file["parse_error"].get("message", "")) + _at(file, file["parse_error"].get("line", 1)))
    lines.extend(["", "## 3. 模块职责与顶层执行", "",
                  "Python 首次导入模块或直接运行文件时，会执行模块顶层语句。函数定义通常只创建函数对象；默认值与装饰器表达式在定义时计算；类定义还会执行类体。函数体的具体步骤列在后面的独立小节中，不能把函数定义的位置当作函数已经执行。", ""])
    for symbol in symbols:
        if symbol.get("kind") != "module":
            continue
        if any(file["path"] == symbol["path"] and file.get("line_count") == 0 for file in program.get("files", [])):
            lines.extend([f"### {_text(symbol['path'])}", "", "这是空文件，没有顶层语句或可定位的源码行；它仍可能用于声明 Python 包。", ""])
            continue
        lines.extend([f"### {_text(symbol['path'])}", "", _text(symbol.get("purpose", "")) + _at(symbol, end=symbol.get("end_line", 1)), ""])
        lines.extend(_step_sections(symbol))
        lines.extend(_call_sections(symbol, calls_by_source[symbol["id"]], lookup))
        lines.extend(_variable_sections(symbol))
    lines.extend(["## 4. 类与函数逐项说明", ""])
    for symbol in symbols:
        kind = symbol.get("kind")
        if kind == "module":
            continue
        label = {"class": "类", "method": "方法", "function": "函数"}.get(kind, "定义")
        lines.extend([f"### {label}：{_text(symbol.get('qualified_name', ''))}", "",
                      "**接口**：" + _code(symbol.get("signature", "")) + " " + _at(symbol, end=symbol.get("end_line", 1)), "",
                      "**用途**：" + _text(symbol.get("purpose", "")) + _at(symbol), ""])
        if kind == "class":
            methods = [s for s in symbols if s.get("kind") == "method" and s.get("qualified_name", "").rsplit(".", 1)[0] == symbol["qualified_name"]]
            lines.extend(["这个类声明了 " + str(len(methods)) + " 个方法。类体中的赋值通常建立类属性；实例上的 `self.属性` 则在相应方法实际执行时建立或改变。构造实例可能触发 `__new__` 与 `__init__`，继承、描述符和装饰器还可能改变行为。" + _at(symbol), ""])
        else:
            lines.extend(["#### 需要什么输入", ""])
            if not symbol.get("parameters"):
                lines.extend(["没有显式参数。函数仍可能读取模块变量、外层闭包、文件、网络或其他外部状态。" + _at(symbol), ""])
            for param in symbol.get("parameters", []):
                description = param.get("description", "")
                if kind == "method" and param.get("name") == "self":
                    description = "通常代表当前实例；通过实例调用普通方法时由 Python 绑定，不需要调用者再次手动传入。装饰器可能改变绑定方式"
                lines.append(f"- {_code(param.get('name', ''))}：{_text(description)}。类型标注：{_code(param.get('annotation') or '未声明')}；默认值表达式：{_code(param.get('default') or '无')}。{_at(symbol)}")
            if symbol.get("parameters"):
                lines.append("")
        lines.extend(_step_sections(symbol))
        lines.extend(_call_sections(symbol, calls_by_source[symbol["id"]], lookup))
        if kind != "class":
            lines.extend(_outputs(symbol))
        lines.extend(_variable_sections(symbol))
    lines.extend(["## 5. 审查发现与需要验证的风险", ""])
    if findings:
        for index, finding in enumerate(findings, 1):
            lines.extend([f"### {index}. {_text(finding.get('title', '待检查问题'))}", "",
                          f"级别：{_text(finding.get('severity', '未标注'))}；来源：{_text(finding.get('source', '未标注'))}。{_at(finding, finding.get('start_line', 1), finding.get('end_line', finding.get('start_line', 1)))}", "",
                          "**代码证据**：" + _text(finding.get("evidence", "未附证据，请定位原文件核实。")), "",
                          "**建议处理**：" + _text(finding.get("recommendation", "结合输入边界与测试验证后再决定如何修改。")), ""])
    else:
        lines.extend(["本次没有附带审查发现。这表示当前检查没有给出问题条目，不代表代码已经被证明正确、安全或没有遗漏。", ""])
    lines.extend(["需要优先核实的地方是：未确认目标的调用是否依赖外部库或回调；分支中的变量在使用前是否已经绑定；异常和提前返回是否跳过预期工作；可变默认参数或对象别名是否造成跨调用状态共享。这些是阅读检查方向，只有具体源码证据才能构成缺陷结论。", "",
                  "## 6. 分析边界与复核方法", ""])
    for limitation in dict.fromkeys(program.get("limitations", [])):
        lines.append("- " + _text(limitation))
    lines.extend(["", "复核时先挑一组具体输入，用断点观察入口、分支判断、函数参数、返回结果和变量绑定是否符合上述条件；再补空输入、异常输入与边界值。动态调试需要在你信任的运行环境中自行执行，本工具的静态理解过程不会代替你运行项目。", "",
                  "这份说明绑定当前导入快照。修改源码后应导入新版本并重新生成，避免拿旧行号或旧调用关系解释新代码。", ""])
    return "\n".join(lines)
