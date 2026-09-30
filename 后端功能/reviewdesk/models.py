"""Shared contracts; positions are one-based and paths repository-relative."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    """Return an ISO UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SourceFile:
    """A read-only source snapshot."""
    path: str
    content: str
    sha256: str = ""


@dataclass
class Finding:
    """A grounded review suggestion, never an automatic code edit."""
    path: str
    start_line: int
    end_line: int
    category: str
    severity: str
    title: str
    evidence: str
    recommendation: str
    source: str = "static"
    rule_id: str = ""
    confidence: float = 0.9
    status: str = "new"


@dataclass
class GraphNode:
    """A module, class, function, or method."""
    id: str
    label: str
    kind: str
    path: str
    line: int


@dataclass
class GraphEdge:
    """A static relationship; dynamic dispatch is not guaranteed."""
    source: str
    target: str
    kind: str


@dataclass
class Analysis:
    """Local analysis and import/call graph."""
    files: list[SourceFile] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass
class Context:
    """Exact data submitted to the model plus source-span allowlist."""
    text: str
    estimated_tokens: int
    included_spans: dict[str, list[tuple[int, int]]]
    limitations: list[str] = field(default_factory=list)
    strategy: str = "dependency"


@dataclass
class Usage:
    """API usage is authoritative; CNY cost is a dated price estimate."""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0
    estimated_cost_cny: str = "0"
    actual: bool = False
    price_source: str = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
    price_checked_at: str = "2026-09-12"
    recorded_at: str = field(default_factory=utc_now)
    note: str = "未调用 API"


@dataclass
class ReviewResult:
    """Serializable report; full source is deliberately excluded."""
    project_name: str
    findings: list[Finding] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    created_at: str = field(default_factory=utc_now)
    mode: str = "local"
    file_count: int = 0
    source_hashes: dict[str, str] = field(default_factory=dict)
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-compatible report data."""
        return asdict(self)
