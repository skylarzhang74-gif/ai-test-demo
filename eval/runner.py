"""单条用例执行器：跑完一轮（可能多轮）对话并生成结构化记录。"""

from __future__ import annotations

from typing import Any

from client.ai_client import AIClient

from .evaluator import evaluate

CHECK_LABELS = {
    "answered": "无有效回答",
    "latency": "响应超时",
    "refusal": "拒答错误",
    "content": "答案不达标",
    "clarify": "未追问澄清",
    "correction": "未纠正错误前提",
    "context": "多轮上下文丢失",
    "hallucination": "疑似幻觉",
}


def run_case(
    client: AIClient, case: dict[str, Any], timeout_threshold_ms: float = 6000
) -> dict[str, Any]:
    turns: list[dict[str, Any]] = []
    history: list[dict[str, str]] = []

    for question in case["messages"]:
        history.append({"role": "user", "content": question})
        result = client.chat(history, case_id=case["id"])
        turns.append(
            {
                "question": question,
                "answer": result.answer,
                "latency_ms": round(result.latency_ms, 1),
                "error": result.error,
            }
        )
        if result.error:
            break
        history.append({"role": "assistant", "content": result.answer})

    checks, reasons, hallucination = evaluate(case, turns, timeout_threshold_ms)
    failed_checks = [k for k, ok in checks.items() if not ok]
    is_timeout = any(t["error"].startswith("timeout") for t in turns)

    return {
        "id": case["id"],
        "category": case["category"],
        "category_label": case["category_label"],
        "title": case["title"],
        "question": " → ".join(t["question"] for t in turns) or case["messages"][0],
        "answer": turns[-1]["answer"] if turns else "",
        "latency_ms": round(max((t["latency_ms"] for t in turns), default=0.0), 1),
        "latencies": [t["latency_ms"] for t in turns],
        "turns": turns,
        "turn_total": len(case["messages"]),
        "turn_ok": len(turns) - sum(1 for t in turns if t["error"]),
        "passed": all(checks.values()),
        "checks": checks,
        "failed_checks": failed_checks,
        "failed_labels": [CHECK_LABELS.get(k, k) for k in failed_checks],
        "failure_reason": "；".join(reasons),
        "hallucination": hallucination,
        "is_timeout": is_timeout,
    }
