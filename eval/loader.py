"""用例加载。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "test_data" / "cases.json"

CATEGORY_LABELS = {
    "normal": "正常问题",
    "vague": "模糊问题",
    "wrong_premise": "错误问题",
    "malicious": "恶意问题",
    "multi_turn": "多轮问题",
}


def load_cases(path: Path | str | None = None) -> list[dict[str, Any]]:
    file = Path(path) if path else DEFAULT_CASES
    data = json.loads(file.read_text(encoding="utf-8"))
    cases = data["cases"]
    for case in cases:
        case.setdefault("expect", {})
        case["category_label"] = CATEGORY_LABELS.get(case["category"], case["category"])
    return cases
