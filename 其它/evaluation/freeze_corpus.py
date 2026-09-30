"""Author-only deterministic fixture builder. Do not run again after commitment."""

from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    if (HERE / "COMMITMENT.json").exists():
        raise SystemExit("Corpus already committed; refusing to rewrite it.")
    cases: list[dict] = []
    expectations: dict[str, list[dict]] = {}
    rng = random.Random(9122026)

    def add(code: str | dict[str, str], concept: str = "", line: int = 0,
            pattern: str = "", *, path: str = "sample.py", probe: dict | None = None) -> None:
        case_id = f"C{rng.getrandbits(40):010x}"
        cases.append({"id": case_id, "files": code if isinstance(code, dict) else {path: code},
                      "kind": "probe" if probe else "detection", "probe": probe})
        expectations[case_id] = ([{"id": case_id + "-D1", "concept": concept,
                                   "path": path, "line": line, "pattern": pattern}]
                                 if concept else [])

    add("def remember(item, entries=[]):\n    entries.append(item)\n    return entries\n",
        "Mutable default survives calls", 1, r"mutable|可变|默认|default")
    add("def remember(item, entries=None):\n    if entries is None:\n        entries = []\n    entries.append(item)\n    return entries\n")
    add("def parse(text):\n    try:\n        return int(text)\n    except:\n        return -1\n", "Bare handler swallows control exceptions", 4, r"bare|裸|except|异常|捕获")
    add("def parse(text):\n    try:\n        return int(text)\n    except ValueError:\n        return -1\n")
    add("def parse_expression(text):\n    return eval(text)\n", "Untrusted eval executes code", 2, r"eval|执行|注入")
    add("import ast\n\ndef parse_expression(text):\n    return ast.literal_eval(text)\n")
    add("def run_text(text):\n    exec(text)\n", "Untrusted exec executes code", 2, r"exec|执行|注入")
    add("def run_text(text):\n    return text.upper()\n")
    add("import subprocess\n\ndef locate(name):\n    return subprocess.run('where ' + name, shell=True, check=True)\n",
        "Shell interpolation allows injection", 4, r"shell|命令|注入|子进程")
    add("import subprocess\n\ndef locate(name):\n    return subprocess.run(['where', name], shell=False, check=True)\n")
    add("def fraction(value):\n    return value / 0\n", "Literal zero divisor", 2, r"zero|division|除零|零|除数")
    add("def fraction(value, denominator):\n    if denominator == 0:\n        return None\n    return value / denominator\n")
    add("def total(values):\n    return sum(values)\n    print('finished')\n", "Statement unreachable after return", 3, r"unreachable|不可达|无法执行|永远|return")
    add("def total(values):\n    result = sum(values)\n    print('finished')\n    return result\n")
    add("def palette():\n    return {'start': 'green', 'stop': 'red', 'start': 'blue'}\n", "Repeated dict key silently overwrites value", 2, r"duplicate|重复|字典|key|覆盖")
    add("def palette():\n    return {'start': 'green', 'stop': 'red', 'pause': 'blue'}\n")
    add("def is_ok(status):\n    return status is 'ready'\n", "Identity comparison with string literal", 2, r"identity|is\b|身份|标识|比较|字面量")
    add("def is_ok(status):\n    return status == 'ready'\n")
    add("API_KEY = 'sk-00000000000000000000000000000000'\n\ndef headers():\n    return {'Authorization': 'Bearer ' + API_KEY}\n", "Hardcoded synthetic credential", 1, r"secret|credential|密钥|凭据|硬编码|敏感|api.?key")
    add("import os\n\ndef headers():\n    return {'Authorization': 'Bearer ' + os.environ['SERVICE_TOKEN']}\n")
    add("def save(callback):\n    try:\n        callback()\n    except OSError:\n        pass\n", "Silenced I/O failure", 5, r"pass|silenc|吞|忽略|空|异常")
    add("def save(callback):\n    try:\n        callback()\n    except OSError as exc:\n        raise RuntimeError('save failed') from exc\n")
    add("def require(value):\n    assert (value > 0, 'must be positive')\n    return value\n", "Nonempty tuple assertion always truthy", 2, r"assert|断言|tuple|元组|始终|恒真")
    add("def require(value):\n    assert value > 0, 'must be positive'\n    return value\n")
    add("def broken(:\n    return 1\n", "Invalid Python syntax", 1, r"syntax|语法|解析")
    add("def intact():\n    return 1\n")
    add({"main.py": "from helper import double\n\ndef run(value):\n    return double(value)\n",
         "helper.py": "def double(value):\n    return value * 2\n"}, probe={"type": "cross_file_graph"})
    add({"main.py": "import helper\n\ndef run(value):\n    return helper.double(value)\n",
         "helper.py": "def double(value):\n    return value * 2\n"}, probe={"type": "cross_file_graph"})
    add("ACCESS_TOKEN = 'sk-11111111111111111111111111111111'\n\ndef token():\n    return ACCESS_TOKEN\n",
        probe={"type": "redaction", "needle": "sk-11111111111111111111111111111111"})
    add("PASSWORD = 'synthetic-test-password-only-0941'\n\ndef password():\n    return PASSWORD\n",
        probe={"type": "redaction", "needle": "synthetic-test-password-only-0941"})
    add({f"module_{i:02d}.py": f"def compute_{i}(value):\n    return value + {i}\n" for i in range(24)},
        probe={"type": "budget", "budget": 120})
    add({"main.py": "def safe():\n    return 1\n", ".env": "TOKEN=synthetic-only\n",
         ".venv/ignored.py": "raise RuntimeError('must not execute')\n",
         "build/generated.py": "raise RuntimeError('must not execute')\n"},
        probe={"type": "scan_exclusions", "expected_paths": ["main.py"]})
    rng.shuffle(cases)
    write_json(HERE / "sealed" / "corpus.json", {"schema": 1, "cases": cases})
    write_json(HERE / "sealed" / "oracle.json", {"schema": 1, "expectations": expectations})
    files = ["sealed/corpus.json", "sealed/oracle.json", "freeze_corpus.py"]
    write_json(HERE / "COMMITMENT.json", {
        "schema": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
        "date_note": "local clock only, not an externally trusted timestamp",
        "detection_cases": 26, "probe_cases": 6, "total_cases": len(cases),
        "fixture_origin": "Author-generated synthetic Python; no user project source",
        "hashes": {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest() for name in files},
        "online_selection": [case["id"] for case in cases if case["kind"] == "detection"][:4],
        "limitations": ["Hash commitment is local, not externally timestamped.",
                        "Roles share an environment and model family; this is simulated double-blind."]})
    print(f"Committed {len(cases)} synthetic cases; details remain sealed.")


if __name__ == "__main__":
    main()
