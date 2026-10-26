"""30 条用例的参数化执行入口。

每条用例都会把「问题 / 回答 / 响应时间 / 是否通过 / 失败原因」写进结果记录，
会话结束时由 conftest 统一汇总成报告。
"""

from __future__ import annotations

import pytest

from eval.loader import load_cases
from eval.runner import run_case

CASES = load_cases()


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_ai_case(case, env):
    record = run_case(env.client, case, env.timeout_threshold_ms)
    env.records.append(record)

    assert record["passed"], (
        f"[{record['id']} {record['title']}] {record['failure_reason'] or '未通过'}\n"
        f"  问题: {record['question']}\n"
        f"  回答: {(record['answer'] or '（无回答）')[:200]}"
    )
