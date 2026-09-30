"""Language adapter registry; parsing never executes a reviewed project."""

from typing import Protocol

from .models import SourceFile


class LanguageAdapter(Protocol):
    """Extension point for a language-specific, JSON-serializable analysis."""

    language: str
    label: str

    def analyze(self, sources: list[SourceFile]) -> dict:
        """Describe source structure and its static evidence."""
        ...


_ADAPTERS: dict[str, LanguageAdapter] = {}


def register_adapter(adapter: LanguageAdapter, *, replace: bool = False) -> None:
    """Register an explicit adapter without loading or executing project plugins."""
    language = adapter.language.strip().lower()
    if not language or not language.isascii() or not language.replace("_", "").isalnum():
        raise ValueError("语言标识应为英文字母、数字或下划线。")
    if language in _ADAPTERS and not replace:
        raise ValueError(f"语言适配器已注册：{language}")
    _ADAPTERS[language] = adapter


def _ensure_default() -> None:
    if "python" not in _ADAPTERS:
        from .understanding import PythonAdapter

        register_adapter(PythonAdapter())


def get_adapter(language: str) -> LanguageAdapter:
    """Select a supported language, returning a readable error for others."""
    _ensure_default()
    key = str(language).strip().lower()
    if key not in _ADAPTERS:
        raise ValueError(f"暂不支持 {language!s} 代码；当前可用语言：" + "、".join(_ADAPTERS))
    return _ADAPTERS[key]


def supported_languages() -> list[dict]:
    """Expose installed adapters for a frontend language selector."""
    _ensure_default()
    return [{"id": key, "language": key, "label": adapter.label} for key, adapter in _ADAPTERS.items()]
