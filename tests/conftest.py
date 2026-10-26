"""pytest 全局装置：启动被测服务 -> 收集用例结果 -> 会话结束自动出报告。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aisvc.mock_server import start_mock_server  # noqa: E402
from client.ai_client import AIClient, client_from_env  # noqa: E402
from eval.reporter import build_reports  # noqa: E402

REPORTS_DIR = ROOT / "reports"

# 会话级状态：fixture 收集结果，terminal summary 打印汇总
_STATE: dict[str, Any] = {"records": []}


class Env:
    def __init__(self, client: AIClient, base_url: str, mode: str, timeout_threshold_ms: float):
        self.client = client
        self.base_url = base_url
        self.mode = mode
        self.timeout_threshold_ms = timeout_threshold_ms
        self.records: list[dict] = []


@pytest.fixture(scope="session")
def env():
    profile = os.getenv("MOCK_PROFILE", "demo")
    real_base = os.getenv("AI_BASE_URL", "").strip()
    server = None

    if real_base:
        base_url, mode = real_base, "真实接口"
    else:
        server, base_url = start_mock_server(profile=profile)
        mode = f"本地 Mock（profile={profile}）"

    client = client_from_env(base_url)
    context = Env(
        client=client,
        base_url=base_url,
        mode=mode,
        timeout_threshold_ms=float(os.getenv("TIMEOUT_THRESHOLD_MS", "6000")),
    )
    _STATE.update({"records": context.records, "mode": mode})

    yield context

    # 无论用例通过与否，都产出完整报告
    _STATE["metrics"] = build_reports(
        records=context.records,
        out_dir=REPORTS_DIR,
        base_url=base_url,
        model=client.model,
        mode=mode,
    )

    if server is not None:
        server.shutdown()
        server.server_close()


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # noqa: ARG001
    metrics = _STATE.get("metrics")
    if not metrics:
        return

    line = "─" * 64
    w = terminalreporter.write_line
    w("")
    w(line)
    w("AI 客服接口测试 · 结果汇总")
    w(line)
    w(f"用例总数   : {metrics['total']}      请求次数: {metrics['request_count']}")
    w(
        f"回答通过率 : {metrics['pass_rate']}%  "
        f"({metrics['passed']}/{metrics['total']})"
    )
    w(f"超时数量   : {metrics['timeout_count']}      幻觉数量: {metrics['hallucination_count']}")
    w(
        f"拒答正确率 : {metrics['refusal']['overall_rate']}%  "
        f"(恶意请求拒答 {metrics['refusal']['malicious_refused']}/{metrics['refusal']['malicious_total']}，"
        f"正常问题误拒 {metrics['refusal']['false_refusal']})"
    )
    w(
        f"多轮稳定性 : {metrics['multi_turn']['stability_rate']}%  "
        f"(上下文保留 {metrics['multi_turn']['context_ok']}/{metrics['multi_turn']['total']}，"
        f"轮次成功 {metrics['multi_turn']['turn_rate']}%)"
    )
    w(
        f"响应时间   : P50 {metrics['latency']['p50'] / 1000:.2f}s  "
        f"P95 {metrics['latency']['p95'] / 1000:.2f}s  "
        f"Max {metrics['latency']['max'] / 1000:.2f}s"
    )
    if metrics["failure_types"]:
        w("失败类型   : " + "，".join(f"{k}×{v}" for k, v in metrics["failure_types"]))
    w(line)
    w("报告已生成 : reports/report.html | reports/report.md | reports/results.jsonl")
    w(line)
