"""AI 接口客户端：统一封装请求、超时、重试与耗时统计。

默认对接本仓库自带的 Mock 服务；设置环境变量即可切到任意 OpenAI 兼容接口：

    AI_BASE_URL=https://api.deepseek.com/v1
    AI_API_KEY=sk-xxx
    AI_MODEL=deepseek-chat
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests


@dataclass
class ChatResult:
    answer: str = ""
    latency_ms: float = 0.0
    status_code: int | None = None
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.answer.strip())


class AIClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "mock-key",
        model: str = "mock-cs",
        timeout: float = 6.0,
        max_retries: int = 0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        # 避免本机代理把 127.0.0.1 的请求转发出去
        self.session.trust_env = False

    def chat(self, messages: list[dict[str, Any]], case_id: str | None = None) -> ChatResult:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if case_id:
            # 真实模型会忽略这个头；Mock 服务用它做缺陷注入
            headers["X-Case-Id"] = case_id
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
        }

        last = ChatResult()
        for attempt in range(self.max_retries + 1):
            last = self._post(url, headers, payload)
            if last.ok:
                return last
            if attempt < self.max_retries:
                time.sleep(0.5 * (attempt + 1))
        return last

    def _post(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> ChatResult:
        started = time.perf_counter()
        try:
            resp = self.session.post(url, headers=headers, json=payload, timeout=self.timeout)
        except requests.Timeout:
            return ChatResult(
                latency_ms=(time.perf_counter() - started) * 1000,
                error=f"timeout(>{self.timeout:g}s)",
            )
        except requests.RequestException as exc:  # 连接失败、DNS 等
            return ChatResult(
                latency_ms=(time.perf_counter() - started) * 1000,
                error=f"request_error: {type(exc).__name__}",
            )

        latency_ms = (time.perf_counter() - started) * 1000
        if resp.status_code >= 400:
            return ChatResult(
                latency_ms=latency_ms,
                status_code=resp.status_code,
                error=f"http_{resp.status_code}",
            )

        try:
            data = resp.json()
            answer = data["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError):
            return ChatResult(
                latency_ms=latency_ms,
                status_code=resp.status_code,
                error="bad_response_shape",
            )

        return ChatResult(
            answer=answer.strip(),
            latency_ms=latency_ms,
            status_code=resp.status_code,
            raw=data,
        )


def client_from_env(default_base_url: str) -> AIClient:
    """优先读环境变量，读不到就用传入的（通常是 Mock）地址。"""
    return AIClient(
        base_url=os.getenv("AI_BASE_URL", default_base_url),
        api_key=os.getenv("AI_API_KEY", "mock-key"),
        model=os.getenv("AI_MODEL", "mock-cs"),
        timeout=float(os.getenv("AI_TIMEOUT", "6")),
        max_retries=int(os.getenv("AI_MAX_RETRIES", "0")),
    )
