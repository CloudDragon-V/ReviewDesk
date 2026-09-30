"""Small DeepSeek HTTP boundary, with explicit errors and dated CNY pricing."""

import json
import http.client
import socket
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .models import Usage

BASE_URL = "https://api.deepseek.com"
MODELS = ("deepseek-flash", "deepseek-v4-pro")
PRICE_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
PRICE_DATE = "2026-09-12"
# CNY / million: cache hit, cache miss, output. Peak prices are conservative
# preflight estimates; observed usage is priced at request start time.
PEAK_PRICES = {"deepseek-flash": ("0.04", "2", "8"), "deepseek-v4-pro": ("0.30", "9", "27")}


class ApiError(RuntimeError):
    """Safe public message; never contains server bodies or request headers."""

    def __init__(self, kind: str, message: str, usage: Usage | None = None):
        super().__init__(message)
        self.kind = kind
        self.usage = usage


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Authorization must never follow an unexpected redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def estimate_cost(model: str, prompt: int, completion: int, hit: int = 0,
                  at: datetime | None = None, peak: bool = False) -> Decimal:
    """Calculate a price-table estimate, not an account charge receipt."""
    if model not in PEAK_PRICES:
        raise ValueError("不支持的模型；请先更新并核验价格表。")
    hit_rate, miss_rate, output_rate = map(Decimal, PEAK_PRICES[model])
    local = (at or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=8)))
    busy = local.weekday() < 5 and (9 <= local.hour < 12 or 14 <= local.hour < 18)
    scale = Decimal(1) if peak or busy else Decimal("0.5")
    hit = min(max(0, hit), max(0, prompt))
    return ((hit * hit_rate + (prompt - hit) * miss_rate + completion * output_rate)
            * scale / Decimal(1_000_000)).quantize(Decimal("0.00000001"))


def parse_usage(data: Any, model: str, at: datetime) -> Usage:
    """Accept only coherent nonnegative integer usage from API metadata."""
    if not isinstance(data, dict):
        return Usage(model=model, note="API 未返回可验证的 usage；费用未知，不能按零费用理解。")
    prompt, completion = data.get("prompt_tokens"), data.get("completion_tokens")
    hit = data.get("prompt_cache_hit_tokens", 0)
    if any(type(n) is not int or not 0 <= n <= 2_000_000 for n in (prompt, completion, hit)) or hit > prompt:
        return Usage(model=model, note="API usage 格式不合法；真实用量和费用未知。")
    return Usage(model=model, prompt_tokens=prompt, completion_tokens=completion,
                 cache_hit_tokens=hit, actual=True,
                 estimated_cost_cny=str(estimate_cost(model, prompt, completion, hit, at)),
                 note="API 真实 token × 请求开始时段单价；估算人民币费用，以平台账单为准。")


class DeepSeekClient:
    """TLS requests only to the official host; no automatic paid retries."""

    def __init__(self, api_key: str, timeout: float = 45):
        if not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 512:
            raise ApiError("authentication", "请先在设置中填写 DeepSeek API Key。")
        self._key = api_key.strip()
        self.timeout = timeout

    def _request(self, path: str, payload: dict | None = None) -> dict:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(BASE_URL + path, data=body, headers={
            "Authorization": "Bearer " + self._key,
            "Content-Type": "application/json", "User-Agent": "ReviewDesk/0.1"})
        try:
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ApiError("format", "API 响应超过安全大小限制。")
            # Model output can echo input; remove the active credential before parsing.
            result = json.loads(raw.decode("utf-8"))
            if not isinstance(result, dict):
                raise ValueError("object required")
            return self._redact_tree(result)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code in (401, 403):
                raise ApiError("authentication", "DeepSeek 鉴权失败，请检查 API Key 和权限。") from None
            if code == 402:
                raise ApiError("balance", "DeepSeek 余额不足，请在平台检查余额。") from None
            if code == 429:
                raise ApiError("rate_limit", "DeepSeek 请求受限，请稍后手动重试。") from None
            if code >= 500:
                raise ApiError("server", "DeepSeek 服务暂时异常，请稍后手动重试。") from None
            raise ApiError("request", f"DeepSeek 拒绝请求（HTTP {code}），请核查模型配置。") from None
        except (TimeoutError, socket.timeout):
            raise ApiError("timeout", "DeepSeek 请求超时；服务器可能已经计费，请先检查用量再重试。") from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ApiError("timeout", "DeepSeek 请求超时；服务器可能已经计费，请检查账单后再重试。") from None
            raise ApiError("network", "无法连接 DeepSeek，请检查网络、代理或证书。") from None
        except http.client.HTTPException:
            raise ApiError("network", "DeepSeek 响应连接中断；用量未知，请检查平台账单。") from None
        except OSError:
            raise ApiError("network", "DeepSeek 连接被中断；用量未知，请检查网络与平台账单。") from None
        except (UnicodeError, ValueError, RecursionError):
            raise ApiError("format", "DeepSeek 返回的内容不是有效 JSON 对象。") from None

    def _redact_tree(self, value: Any) -> Any:
        """Redact after JSON decoding so Unicode escapes cannot bypass masking."""
        if isinstance(value, str):
            return value.replace(self._key, "[REDACTED]")
        if isinstance(value, list):
            return [self._redact_tree(item) for item in value]
        if isinstance(value, dict):
            return {self._redact_tree(key): self._redact_tree(item) for key, item in value.items()}
        return value

    def check_connection(self) -> dict:
        """Read balance without creating a charged completion."""
        return self._request("/user/balance")

    def complete(self, system_prompt: str, context: str, model: str = "deepseek-flash",
                 max_tokens: int = 2500, *, reasoning: bool = False) -> tuple[dict, Usage]:
        """Request constrained JSON and retain usage even if the output is invalid."""
        if model not in MODELS:
            raise ApiError("model", "请选择已核验价格的 DeepSeek 模型。")
        started = datetime.now(timezone.utc)
        request = {
            "model": model, "messages": [{"role": "system", "content": system_prompt},
                                        {"role": "user", "content": context}],
            "response_format": {"type": "json_object"}, "thinking": {"type": "enabled" if reasoning else "disabled"},
            "max_tokens": max_tokens, "stream": False}
        if reasoning:
            request["reasoning_effort"] = "high"
        data = self._request("/chat/completions", request)
        usage = parse_usage(data.get("usage"), model, started)
        try:
            choices = data.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ValueError("choices")
            choice = choices[0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("incomplete")
            if not isinstance(choice.get("message"), dict):
                raise ValueError("message")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty")
            result = self._redact_tree(json.loads(content))
            if not isinstance(result, dict) or not isinstance(result.get("findings"), list):
                raise ValueError("schema")
        except (KeyError, IndexError, TypeError, ValueError, RecursionError):
            raise ApiError("format", "模型结果为空、被截断或不符合报告格式；已保留返回的用量。", usage) from None
        return result, usage
