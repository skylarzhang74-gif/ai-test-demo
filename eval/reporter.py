"""报告生成：汇总指标 -> Markdown + 自包含 HTML。"""

from __future__ import annotations

import html
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .loader import CATEGORY_LABELS

CATEGORY_ORDER = ["normal", "vague", "wrong_premise", "malicious", "multi_turn"]
PASS_TARGET = 0.90


# ----------------------------------------------------------------- 指标计算


def _pct(part: float, total: float) -> float:
    return round(part / total * 100, 1) if total else 0.0


def compute_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    passed = sum(1 for r in records if r["passed"])
    latencies = sorted(x for r in records for x in r["latencies"])

    mal = [r for r in records if r["category"] == "malicious"]
    non_mal = [r for r in records if r["category"] != "malicious" and "refusal" in r["checks"]]
    mal_ok = sum(1 for r in mal if r["checks"].get("refusal"))
    false_refusals = [r for r in non_mal if not r["checks"]["refusal"]]

    mt = [r for r in records if r["category"] == "multi_turn"]
    mt_ok = sum(1 for r in mt if r["passed"])
    mt_ctx = sum(1 for r in mt if r["checks"].get("context", True))
    mt_turns = sum(r["turn_total"] for r in mt)
    mt_turns_ok = sum(r["turn_ok"] for r in mt)

    categories = []
    for key in CATEGORY_ORDER:
        rows = [r for r in records if r["category"] == key]
        if not rows:
            continue
        ok = sum(1 for r in rows if r["passed"])
        categories.append(
            {
                "key": key,
                "label": CATEGORY_LABELS[key],
                "total": len(rows),
                "passed": ok,
                "rate": _pct(ok, len(rows)),
            }
        )

    failure_types = Counter()
    for r in records:
        for label in r["failed_labels"]:
            failure_types[label] += 1

    def _pth(p: float) -> float:
        if not latencies:
            return 0.0
        idx = min(len(latencies) - 1, int(round((len(latencies) - 1) * p)))
        return round(latencies[idx], 1)

    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": _pct(passed, total),
        "timeout_count": sum(1 for r in records if r["is_timeout"]),
        "hallucination_count": sum(1 for r in records if r["hallucination"]),
        "refusal": {
            "malicious_total": len(mal),
            "malicious_refused": mal_ok,
            "malicious_rate": _pct(mal_ok, len(mal)),
            "normal_total": len(non_mal),
            "false_refusal": len(false_refusals),
            "false_refusal_ids": [r["id"] for r in false_refusals],
            "overall_rate": _pct(mal_ok + (len(non_mal) - len(false_refusals)), len(mal) + len(non_mal)),
        },
        "multi_turn": {
            "total": len(mt),
            "stable": mt_ok,
            "stability_rate": _pct(mt_ok, len(mt)),
            "context_ok": mt_ctx,
            "context_rate": _pct(mt_ctx, len(mt)),
            "turn_total": mt_turns,
            "turn_ok": mt_turns_ok,
            "turn_rate": _pct(mt_turns_ok, mt_turns),
        },
        "latency": {
            "avg": round(statistics.fmean(latencies), 1) if latencies else 0.0,
            "p50": _pth(0.50),
            "p95": _pth(0.95),
            "max": round(max(latencies), 1) if latencies else 0.0,
        },
        "categories": categories,
        "failure_types": failure_types.most_common(),
        "request_count": len(latencies),
    }


def _conclusions(m: dict[str, Any]) -> list[str]:
    out: list[str] = []
    out.append(
        f"整体通过率 {m['pass_rate']}%（{m['passed']}/{m['total']}），"
        + ("达到" if m["pass_rate"] >= PASS_TARGET * 100 else "未达到")
        + f" {int(PASS_TARGET * 100)}% 的准入线。"
    )
    if m["failure_types"]:
        top = "、".join(f"{k}×{v}" for k, v in m["failure_types"][:3])
        out.append(f"失败集中在：{top}。")
    out.append(
        f"安全侧：{m['refusal']['malicious_total']} 条恶意请求中 "
        f"{m['refusal']['malicious_refused']} 条被正确拒答"
        f"（{m['refusal']['malicious_rate']}%），正常问题误拒 {m['refusal']['false_refusal']} 条。"
    )
    out.append(
        f"多轮侧：上下文保留率 {m['multi_turn']['context_rate']}%，"
        f"轮次级成功率 {m['multi_turn']['turn_rate']}%（{m['multi_turn']['turn_ok']}/{m['multi_turn']['turn_total']} 次请求）。"
    )
    out.append(
        f"性能侧：P95 {m['latency']['p95'] / 1000:.2f}s，最大 {m['latency']['max'] / 1000:.2f}s，"
        f"超时 {m['timeout_count']} 条。"
    )
    return out


# ------------------------------------------------------------------ Markdown


def _md_escape(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(m: dict[str, Any], records: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# AI 客服接口自动化测试报告\n")
    lines.append(f"- 生成时间：{meta['generated_at']}")
    lines.append(f"- 被测服务：`{meta['base_url']}`")
    lines.append(f"- 模型：`{meta['model']}`　运行模式：{meta['mode']}")
    lines.append(f"- 用例集：{meta['dataset']}　用例数：{m['total']}\n")

    lines.append("## 一、核心结论\n")
    for item in _conclusions(m):
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## 二、关键指标\n")
    lines.append("| 指标 | 数值 | 说明 |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| 回答通过率 | **{m['pass_rate']}%** | {m['passed']}/{m['total']} 条用例通过 |")
    lines.append(f"| 超时数量 | {m['timeout_count']} | 超过 6s 未返回即记为超时 |")
    lines.append(f"| 幻觉数量 | {m['hallucination_count']} | 答案中出现知识库未定义的数字/政策 |")
    lines.append(
        f"| 拒答正确率 | {m['refusal']['overall_rate']}% | 恶意拒答 {m['refusal']['malicious_refused']}/{m['refusal']['malicious_total']}，"
        f"正常误拒 {m['refusal']['false_refusal']} |"
    )
    lines.append(
        f"| 多轮稳定性 | {m['multi_turn']['stability_rate']}% | 上下文保留 {m['multi_turn']['context_ok']}/{m['multi_turn']['total']}，"
        f"轮次成功 {m['multi_turn']['turn_rate']}% |"
    )
    lines.append(f"| 响应时间 P50 / P95 | {m['latency']['p50'] / 1000:.2f}s / {m['latency']['p95'] / 1000:.2f}s | 共 {m['request_count']} 次请求 |")
    lines.append("")

    lines.append("## 三、分类通过率\n")
    lines.append("| 类别 | 用例数 | 通过 | 通过率 |")
    lines.append("| --- | --- | --- | --- |")
    for c in m["categories"]:
        lines.append(f"| {c['label']} | {c['total']} | {c['passed']} | {c['rate']}% |")
    lines.append("")

    failed = [r for r in records if not r["passed"]]
    lines.append(f"## 四、失败用例（{len(failed)} 条）\n")
    if failed:
        lines.append("| 用例 | 类别 | 问题 | 失败类型 | 失败原因 | AI 回答 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for r in failed:
            lines.append(
                f"| {r['id']} | {r['category_label']} | {_md_escape(r['question'])} | "
                f"{_md_escape('、'.join(r['failed_labels']))} | {_md_escape(r['failure_reason'])} | "
                f"{_md_escape((r['answer'] or '（空）')[:60])} |"
            )
    else:
        lines.append("本轮全部通过。")
    lines.append("")

    lines.append("## 五、全量明细\n")
    lines.append("| 用例 | 类别 | 问题 | 耗时(ms) | 结果 | 备注 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for r in records:
        lines.append(
            f"| {r['id']} | {r['category_label']} | {_md_escape(r['question'][:40])} | {r['latency_ms']:.0f} | "
            f"{'PASS' if r['passed'] else 'FAIL'} | {_md_escape(r['failure_reason'] or '-')} |"
        )
    lines.append("")

    lines.append("## 六、判定口径与局限\n")
    lines.append("- 判定为规则式（关键词 + 数字白名单），可复现、零成本，适合放进 CI 卡口。")
    lines.append("- 局限：同义表达可能漏判；数字白名单无法识别「语义级幻觉」（如张冠李戴但不含数字）。")
    lines.append("- 建议：核心用例用规则卡口，长尾用例定期抽样人工 + LLM 评分双轨复核。")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------- HTML


def _html_escape(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _bar(rate: float, color: str = "#4f46e5") -> str:
    return (
        '<div class="bar"><div class="bar-fill" style="width:'
        f'{max(2.0, min(100.0, rate)):.1f}%;background:{color}"></div></div>'
    )


def render_html(m: dict[str, Any], records: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    rate_color = "#16a34a" if m["pass_rate"] >= PASS_TARGET * 100 else "#dc2626"
    passed_flag = "达到准入线" if m["pass_rate"] >= PASS_TARGET * 100 else "低于准入线"

    kpis = [
        ("回答通过率", f"{m['pass_rate']}%", f"{m['passed']}/{m['total']} 条用例通过", rate_color),
        ("超时数量", str(m["timeout_count"]), "超过 6s 未返回", "#dc2626" if m["timeout_count"] else "#16a34a"),
        ("幻觉数量", str(m["hallucination_count"]), "出现知识库未定义信息", "#dc2626" if m["hallucination_count"] else "#16a34a"),
        ("拒答正确率", f"{m['refusal']['overall_rate']}%", f"恶意拒答 {m['refusal']['malicious_refused']}/{m['refusal']['malicious_total']}", "#111827"),
        ("多轮稳定性", f"{m['multi_turn']['stability_rate']}%", f"上下文保留 {m['multi_turn']['context_ok']}/{m['multi_turn']['total']}", "#111827"),
        ("P95 响应", f"{m['latency']['p95'] / 1000:.2f}s", f"P50 {m['latency']['p50'] / 1000:.2f}s", "#111827"),
    ]
    kpi_html = "\n".join(
        f'<div class="kpi"><div class="kpi-label">{_html_escape(l)}</div>'
        f'<div class="kpi-value" style="color:{c}">{_html_escape(v)}</div>'
        f'<div class="kpi-sub">{_html_escape(s)}</div></div>'
        for l, v, s, c in kpis
    )

    cat_html = "\n".join(
        '<div class="cat-row">'
        f'<div class="cat-name">{_html_escape(c["label"])}</div>'
        f'<div class="cat-bar">{_bar(c["rate"], "#4f46e5" if c["rate"] >= 90 else "#f59e0b")}</div>'
        f'<div class="cat-num">{c["passed"]}/{c["total"]}　<b>{c["rate"]}%</b></div>'
        "</div>"
        for c in m["categories"]
    )

    failed = [r for r in records if not r["passed"]]
    if failed:
        failed_rows = "\n".join(
            "<tr>"
            f'<td><span class="tag tag-fail">{r["id"]}</span></td>'
            f'<td>{_html_escape(r["category_label"])}</td>'
            f'<td class="q">{_html_escape(r["question"])}</td>'
            f'<td><span class="chip">{_html_escape("、".join(r["failed_labels"]))}</span></td>'
            f'<td class="reason">{_html_escape(r["failure_reason"])}</td>'
            f'<td class="ans">{_html_escape((r["answer"] or "（无回答）")[:70])}</td>'
            "</tr>"
            for r in failed
        )
    else:
        failed_rows = '<tr><td colspan="6" class="empty">本轮全部通过</td></tr>'

    all_rows = "\n".join(
        "<tr class='" + ("" if r["passed"] else "row-fail") + "'>"
        f'<td>{r["id"]}</td>'
        f'<td>{_html_escape(r["category_label"])}</td>'
        f'<td class="q">{_html_escape(r["question"])}</td>'
        f'<td class="num">{r["latency_ms"]:.0f}</td>'
        f'<td><span class="tag {"tag-pass" if r["passed"] else "tag-fail"}">{"PASS" if r["passed"] else "FAIL"}</span></td>'
        f'<td class="ans">{_html_escape((r["answer"] or "（无回答）")[:60])}</td>'
        "</tr>"
        for r in records
    )

    conclusions = "\n".join(f"<li>{_html_escape(x)}</li>" for x in _conclusions(m))
    failure_notes = "".join(
        f'<span class="pill">{_html_escape(k)}<b>{v}</b></span>' for k, v in m["failure_types"]
    ) or '<span class="pill">无失败</span>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>AI 客服接口自动化测试报告</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px 16px 64px; background: #f5f6f8; color: #111827;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  .wrap {{ max-width: 1080px; margin: 0 auto; }}
  .hero {{
    background: linear-gradient(120deg, #ffffff 0%, #f8faff 100%);
    border: 1px solid #e5e7eb; border-radius: 16px; padding: 26px 28px; margin-bottom: 18px;
    box-shadow: 0 1px 2px rgba(16,24,40,.04);
  }}
  .hero h1 {{ margin: 0 0 8px; font-size: 24px; letter-spacing: .3px; }}
  .hero .meta {{ color: #6b7280; font-size: 13px; line-height: 1.9; }}
  .hero .meta code {{ background: #f3f4f6; padding: 1px 6px; border-radius: 4px; color: #374151; }}
  .badge {{
    display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px;
    font-weight: 600; color: {rate_color}; background: {rate_color}14; border: 1px solid {rate_color}33;
    margin-left: 6px; vertical-align: middle;
  }}
  .kpis {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin-bottom: 18px; }}
  .kpi {{
    background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 14px 16px;
  }}
  .kpi-label {{ font-size: 12px; color: #6b7280; margin-bottom: 6px; }}
  .kpi-value {{ font-size: 24px; font-weight: 700; line-height: 1.2; letter-spacing: .5px; }}
  .kpi-sub {{ font-size: 11px; color: #9ca3af; margin-top: 5px; }}
  .card {{
    background: #fff; border: 1px solid #e5e7eb; border-radius: 14px;
    padding: 20px 22px; margin-bottom: 16px;
  }}
  .card h2 {{
    margin: 0 0 14px; font-size: 15px; font-weight: 700; color: #111827;
    display: flex; align-items: center; gap: 8px;
  }}
  .card h2::before {{
    content: ""; width: 3px; height: 14px; border-radius: 2px; background: #4f46e5;
  }}
  .card h2 .hint {{ font-weight: 400; font-size: 12px; color: #9ca3af; }}
  ul.concl {{ margin: 0; padding-left: 18px; color: #374151; font-size: 13.5px; line-height: 2; }}
  .cat-row {{ display: flex; align-items: center; gap: 14px; margin-bottom: 12px; font-size: 13px; }}
  .cat-name {{ width: 88px; color: #374151; }}
  .cat-bar {{ flex: 1; }}
  .bar {{ height: 8px; background: #eef0f4; border-radius: 999px; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 999px; }}
  .cat-num {{ width: 120px; text-align: right; color: #6b7280; }}
  .cat-num b {{ color: #111827; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
  th {{
    text-align: left; font-weight: 600; color: #6b7280; background: #fafbfc;
    padding: 9px 10px; border-bottom: 1px solid #e5e7eb; white-space: nowrap;
  }}
  td {{ padding: 9px 10px; border-bottom: 1px solid #f1f2f4; vertical-align: top; color: #374151; }}
  tr:last-child td {{ border-bottom: none; }}
  .row-fail {{ background: #fff8f8; }}
  .q {{ color: #111827; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; color: #6b7280; }}
  .ans {{ color: #6b7280; max-width: 260px; }}
  .reason {{ color: #b91c1c; max-width: 240px; }}
  .tag {{
    display: inline-block; padding: 2px 7px; border-radius: 5px; font-size: 11px; font-weight: 600;
  }}
  .tag-pass {{ color: #15803d; background: #ecfdf3; border: 1px solid #bbf7d0; }}
  .tag-fail {{ color: #b91c1c; background: #fef2f2; border: 1px solid #fecaca; }}
  .chip {{
    display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px;
    color: #b45309; background: #fffbeb; border: 1px solid #fde68a; white-space: nowrap;
  }}
  .pill {{
    display: inline-block; margin: 0 8px 8px 0; padding: 5px 12px; border-radius: 999px;
    font-size: 12px; color: #374151; background: #f9fafb; border: 1px solid #e5e7eb;
  }}
  .pill b {{ margin-left: 6px; color: #b91c1c; }}
  .empty {{ text-align: center; color: #16a34a; padding: 18px; }}
  .foot {{ color: #9ca3af; font-size: 12px; line-height: 1.9; }}
  .foot code {{ background: #f3f4f6; padding: 1px 5px; border-radius: 4px; }}
  @media (max-width: 900px) {{
    .kpis {{ grid-template-columns: repeat(3, 1fr); }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1>AI 客服接口自动化测试报告<span class="badge">{passed_flag}</span></h1>
    <div class="meta">
      生成时间：{_html_escape(meta["generated_at"])}　|　被测服务：<code>{_html_escape(meta["base_url"])}</code><br />
      模型：<code>{_html_escape(meta["model"])}</code>　|　运行模式：{_html_escape(meta["mode"])}　|　用例集：{_html_escape(meta["dataset"])}（{m["total"]} 条 / {m["request_count"]} 次请求）
    </div>
  </div>

  <div class="kpis">
    {kpi_html}
  </div>

  <div class="card">
    <h2>核心结论</h2>
    <ul class="concl">{conclusions}</ul>
  </div>

  <div class="card">
    <h2>分类通过率</h2>
    {cat_html}
  </div>

  <div class="card">
    <h2>失败类型分布 <span class="hint">按检查项统计，一条用例可命中多项</span></h2>
    {failure_notes}
  </div>

  <div class="card">
    <h2>失败用例明细 <span class="hint">{len(failed)} 条</span></h2>
    <table>
      <thead><tr><th>用例</th><th>类别</th><th>用户问题</th><th>失败类型</th><th>失败原因</th><th>AI 回答</th></tr></thead>
      <tbody>{failed_rows}</tbody>
    </table>
  </div>

  <div class="card">
    <h2>全量用例执行明细</h2>
    <table>
      <thead><tr><th>用例</th><th>类别</th><th>用户问题</th><th>耗时(ms)</th><th>结果</th><th>AI 回答</th></tr></thead>
      <tbody>{all_rows}</tbody>
    </table>
  </div>

  <div class="card">
    <h2>判定口径与已知局限</h2>
    <div class="foot">
      评测采用<strong>规则式判定</strong>：关键词命中 / 数字白名单 / 拒答与追问标记，全部可复现、零成本，适合放到 CI 做回归卡口。<br />
      已知局限：同义表达可能漏判；数字白名单识别不出「不含数字的语义幻觉」；拒答标记依赖话术用词。<br />
      建议：核心用例用规则卡口守住底线，长尾用例定期抽样，人工 + LLM 评分双轨复核。<br />
      复跑命令：<code>python -m pytest -v</code>　报告文件：<code>reports/report.html</code>、<code>reports/report.md</code>、<code>reports/results.jsonl</code>
    </div>
  </div>
</div>
</body>
</html>
"""


# ------------------------------------------------------------------ 落盘入口


def build_reports(
    records: list[dict[str, Any]],
    out_dir: Path,
    base_url: str,
    model: str,
    mode: str,
    dataset: str = "test_data/cases.json",
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics = compute_metrics(records)
    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "base_url": base_url,
        "model": model,
        "mode": mode,
        "dataset": dataset,
    }

    (out_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8"
    )
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(render_markdown(metrics, records, meta), encoding="utf-8")
    (out_dir / "report.html").write_text(render_html(metrics, records, meta), encoding="utf-8")
    return metrics
