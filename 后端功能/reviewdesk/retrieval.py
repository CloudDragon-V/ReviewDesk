"""Explainable lexical retrieval helpers; no model or embedding service is used."""

from collections import Counter
import math
import re
import unicodedata


# Small, visible query expansions help a beginner connect Chinese concepts to
# Python identifiers. They are lexical hints, never a claim of semantic search.
QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "入口": ("main", "__main__", "run", "start"),
    "启动": ("main", "run", "start", "init"),
    "返回": ("return", "result", "yield"),
    "变量": ("value", "variable", "assign", "result"),
    "保存": ("save", "write", "store", "persist", "dump"),
    "读取": ("read", "load", "open", "fetch"),
    "加载": ("load", "read", "open"),
    "计算": ("calculate", "compute", "calc", "sum", "total"),
    "校验": ("validate", "check", "verify"),
    "错误": ("error", "exception", "raise", "except"),
    "异常": ("error", "exception", "raise", "except"),
    "调用": ("call", "invoke"),
    "循环": ("for", "while", "iterate"),
    "条件": ("if", "elif", "else", "condition"),
    "删除": ("delete", "remove", "clear"),
    "文件": ("file", "path", "open"),
    "金额": ("amount", "price", "total", "cost"),
    "订单": ("order", "orders"),
    "用户": ("user", "users", "account"),
    "版本": ("revision", "version", "snapshot"),
    "项目": ("project", "repository", "repo"),
    "代码": ("code", "source"),
    "源码": ("source", "code"),
    "检索": ("search", "query", "retrieve"),
    "搜索": ("search", "query", "find"),
    "函数": ("function", "method", "def"),
    "参数": ("parameter", "argument", "args", "kwargs"),
    "数据库": ("database", "sqlite", "connection", "sql"),
}

_WORDS = re.compile(r"[A-Za-z_][A-Za-z_0-9]*|\d+|[\u3400-\u9fff]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+")


def tokenize(text: str) -> list[str]:
    """Split snake/camel names and Chinese bigrams while preserving whole names."""
    tokens: list[str] = []
    for match in _WORDS.finditer(unicodedata.normalize("NFKC", text)):
        value = match.group()
        lowered = value.casefold()
        tokens.append(lowered)
        if "\u3400" <= value[0] <= "\u9fff":
            if len(value) > 2:
                tokens.extend(value[index:index + 2] for index in range(len(value) - 1))
        else:
            pieces = [item.casefold() for part in value.split("_")
                      for item in _CAMEL.findall(part)]
            tokens.extend(item for item in pieces if item != lowered)
    return [token for token in tokens if len(token) <= 100]


def query_terms(query: str) -> tuple[dict[str, float], dict[str, str]]:
    """Return original terms plus lower-weight, attributable Chinese expansions."""
    weights = dict.fromkeys(tokenize(query), 1.0)
    aliases: dict[str, str] = {}
    for chinese, alternatives in QUERY_ALIASES.items():
        if chinese in query:
            for term in alternatives:
                if term not in weights:
                    weights[term] = 0.45
                    aliases[term] = chinese
    # Parameterized queries remain bounded even for intentionally noisy input.
    return dict(list(weights.items())[:128]), aliases


def term_counts(text: str) -> Counter[str]:
    """Count a code unit's actual tokens for a persisted inverted index."""
    return Counter(tokenize(text))


def bm25(term_frequency: int, document_length: int, document_frequency: int,
         total_documents: int, average_length: float) -> float:
    """Calculate the standard positive-IDF BM25 term score (k1=1.2, b=0.75)."""
    inverse_frequency = math.log1p(
        (total_documents - document_frequency + 0.5) / (document_frequency + 0.5))
    denominator = term_frequency + 1.2 * (
        0.25 + 0.75 * document_length / max(average_length, 1.0))
    return inverse_frequency * term_frequency * 2.2 / denominator
