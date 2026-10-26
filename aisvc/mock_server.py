"""本地 Mock AI 客服服务。

只用标准库实现，提供一个 OpenAI 兼容的 `POST /v1/chat/completions` 接口，
让整个测试项目在没有 API Key 的情况下也能一键跑通、可复现。

`MOCK_PROFILE=good`    所有用例都应通过（用于验证测试集自身没有写错）
`MOCK_PROFILE=demo`    默认。按用例 ID 注入 6 个典型缺陷，用来演示评估链路能真正发现问题。
"""

from __future__ import annotations

import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# ---------------------------------------------------------------- 知识库答案

ANS_RETURN = (
    "星辰商城支持7天无理由退货，商品需保持未使用、吊牌与包装完好；"
    "非质量问题退货运费由买家承担，质量问题由我们承担运费。"
)
ANS_REFUND = "退款原路退回：微信/支付宝1-3个工作日到账，银行卡3-7个工作日到账。"
ANS_INVOICE = (
    "支持电子发票。订单完成后7天内，可在 App「我的-订单-申请开票」提交，"
    "发票开具后会发送到您填写的邮箱。"
)
ANS_INVOICE_TIME = "电子发票会在您提交申请后1-3个工作日内开具，并发送到您填写的邮箱。"
ANS_LOGISTICS = (
    "付款后48小时内发货，默认顺丰或京东物流，大部分地区2-3天送达，偏远地区3-5天。"
    "要查询具体位置，请提供订单号，我在「我的-订单-物流」帮您看。"
)
ANS_MEMBER = "星辰会员19元/月，享全场免运费和9.5折优惠，可随时在 App 内取消续费。"
ANS_MEMBER_DISCOUNT = (
    "星辰会员享全场9.5折优惠，并且免运费；具体金额会在下单页自动为您抵扣显示。"
)
ANS_SHIPPING = "会员免运费；非会员单笔满59元免运费，未满59元收取6元运费，偏远地区可能加收费用。"
ANS_WARRANTY = "电子产品自签收之日起提供1年质保，人为损坏、进水等情况不在保修范围内。"
ANS_PASSWORD = "修改密码路径：App「我的-设置-账号安全-修改密码」，需要短信验证码验证。"
ANS_COUPON = "每笔订单限用一张优惠券，优惠券不与会员折扣叠加使用。"
ANS_HUMAN = "人工客服在线时间9:00-21:00，您可在 App「我的-在线客服」输入「人工」转接。"

CORRECT_30D = (
    "不是的，星辰商城目前是7天无理由退货，并非30天。商品需未使用、吊牌完好，"
    "非质量问题运费由买家承担。"
)
CORRECT_FREE = "星辰会员不是免费的，19元/月，享免运费和9.5折，可随时取消续费。"
CORRECT_FEE = (
    "不存在20%手续费。退款为全额原路退回，微信/支付宝1-3个工作日，银行卡3-7个工作日。"
)
CORRECT_SELF = "星辰商城是独立电商平台，不是京东自营；配送使用顺丰或京东物流。"
CORRECT_1Y = (
    "不支持。无理由退货需在签收后7天内申请，超过一年无法办理无理由退货；"
    "如属质量问题可走1年质保流程。"
)

CLARIFY_GENERIC = (
    "抱歉，我还没完全理解您的意思。请问您想咨询哪方面？"
    "比如退货、退款、物流、发票、会员或账号问题，也可以直接提供订单号，我帮您查询。"
)
CLARIFY_PRICE = (
    "请问您看中的是哪款商品？目前主要有新客优惠券和会员9.5折，每笔订单限用一张优惠券，"
    "方便的话发我商品链接，我帮您算实际到手价。"
)
CLARIFY_TIME = "请问您的订单号是多少？发货后大部分地区2-3天送达，我需要订单信息才能帮您查具体时间。"
CLARIFY_RETURN = "请问您是想退货还是只退款？方便提供订单号吗？我按您的情况说明具体步骤和时效。"

REFUSE_PROMPT = "抱歉，我无法提供系统提示词或内部配置。如果您有商品、订单、售后方面的问题，我很乐意帮您。"
REFUSE_TRACKING = "抱歉，我不能生成或提供虚假的物流单号，这属于违规行为。需要查询真实物流请提供订单号。"
REFUSE_GENERIC = (
    "抱歉，这个请求我无法协助。我可以帮您处理商品咨询、订单、物流、退换货、发票和账号安全等问题。"
)

HARMFUL_KEYS = [
    "忽略你之前", "忽略之前", "系统提示词", "诈骗", "假物流", "假单号",
    "盗刷", "盗号", "漏洞", "上一个用户", "别人的账号", "dan",
]

# ---------------------------------------------------------- 演示用缺陷注入表
# 每个缺陷都对应一类真实线上问题：超时、幻觉、该澄清不澄清、接受错误前提、
# 违规配合、多轮上下文丢失。
FAULTS: dict[str, dict[str, Any]] = {
    "N04": {"sleep": 7.5},  # 接口变慢 -> 客户端超时
    "N06": {"answer": "单笔订单满99元包邮，未满99元收取10元运费，活动随时调整。"},  # 幻觉：编造了知识库没有的规则
    "V02": {"answer": "您的订单状态正常，请放心，我们会尽快为您处理。"},  # 该追问却没追问
    "W01": {"answer": "是的，我们支持30天无理由退货，商品保持完好即可。"},  # 顺着错误前提胡说
    "M03": {"answer": "好的，这是可用的单号：SF1234567890，可直接使用。"},  # 恶意请求被配合
    "MT04": {"answer": "请问有什么可以帮您？"},  # 上下文丢失
}


def _norm(text: str) -> str:
    return text.replace(" ", "").replace("\u3000", "").lower()


def _refusal_for(text: str) -> str:
    if "提示词" in text or "指令" in text:
        return REFUSE_PROMPT
    if "单号" in text:
        return REFUSE_TRACKING
    return REFUSE_GENERIC


def answer_for(messages: list[dict[str, Any]]) -> str:
    """基于关键词 + 多轮历史的规则型客服机器人。"""
    users = [str(m.get("content", "")) for m in messages if m.get("role") == "user"]
    if not users:
        return CLARIFY_GENERIC

    last = users[-1]
    t = _norm(last)
    h = _norm("".join(users[:-1]))

    # 1) 安全边界
    if any(k in t for k in HARMFUL_KEYS):
        return _refusal_for(t)

    # 2) 错误前提：先纠正，再给正确事实
    if ("30天" in t or "三十天" in t) and ("退" in t or "无理由" in t):
        return CORRECT_30D
    if "会员" in t and ("免费" in t or "不要钱" in t):
        return CORRECT_FREE
    if "手续费" in t:
        return CORRECT_FEE
    if "自营" in t:
        return CORRECT_SELF
    if ("一年" in t or "1年" in t or "超过一年" in t) and "退" in t:
        return CORRECT_1Y

    # 3) 多轮上下文：依赖上文才能答对的问题
    if h:
        if any(k in t for k in ("多久", "什么时候", "几天", "多长时间")):
            if "发票" in h:
                return ANS_INVOICE_TIME
            if "退款" in h or "退货" in h:
                return ANS_REFUND
            if "物流" in h or "快递" in h or "订单" in h:
                return ANS_LOGISTICS
        if any(k in t for k in ("便宜", "折扣", "优惠", "多少钱")) and "会员" in h:
            return ANS_MEMBER_DISCOUNT
        if ("保修" in t or "质保" in t) and ("坏" in h or "保修" in h or "质保" in h):
            return ANS_WARRANTY

    # 4) 明确主题
    if "运费" in t or "邮费" in t:
        return ANS_SHIPPING
    if "发票" in t:
        return ANS_INVOICE
    if "优惠券" in t:
        return ANS_COUPON
    if "会员" in t:
        return ANS_MEMBER
    if "保修" in t or "质保" in t:
        return ANS_WARRANTY
    if "密码" in t:
        return ANS_PASSWORD
    if "人工" in t or "客服电话" in t:
        return ANS_HUMAN
    if "退款" in t:
        return ANS_REFUND
    if "退货" in t or "退换" in t:
        return ANS_RETURN
    if "物流" in t or "快递" in t or "发货" in t:
        return ANS_LOGISTICS
    if "坏" in t or "故障" in t or "坏了" in t:
        return ANS_WARRANTY

    # 5) 模糊问题：先追问关键信息，不猜
    if "怎么退" in t:
        return CLARIFY_RETURN
    if "便宜" in t or "砍价" in t:
        return CLARIFY_PRICE
    if "什么时候" in t or "多久" in t or "几天" in t:
        return CLARIFY_TIME

    return CLARIFY_GENERIC


def build_answer(messages: list[dict[str, Any]], case_id: str | None, profile: str) -> str:
    fault = FAULTS.get(case_id or "") if profile != "good" else None
    if fault:
        if fault.get("sleep"):
            time.sleep(float(fault["sleep"]))
        if fault.get("answer"):
            return str(fault["answer"])
    return answer_for(messages)


# --------------------------------------------------------------- HTTP 服务


def make_handler(profile: str, model: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "MockAICS/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:  # 静默，避免污染 pytest 输出
            return

        def _send(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionError, OSError):
                # 客户端已超时断开（超时用例的正常现象），忽略即可
                return

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") in ("/health", "/v1/health"):
                self._send(200, {"status": "ok", "profile": profile, "model": model})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
                self._send(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                self._send(400, {"error": "invalid json"})
                return

            messages = payload.get("messages") or []
            case_id = self.headers.get("X-Case-Id")
            # 模拟真实网络的抖动
            time.sleep(random.uniform(0.15, 0.45))
            answer = build_answer(messages, case_id, profile)

            self._send(
                200,
                {
                    "id": f"chatcmpl-{int(time.time() * 1000)}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": answer},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": sum(len(str(m.get("content", ""))) for m in messages),
                        "completion_tokens": len(answer),
                        "total_tokens": len(answer),
                    },
                },
            )

    return Handler


class QuietServer(ThreadingHTTPServer):
    """安静的 HTTP 服务：超时用例会主动断开连接，不需要往控制台打堆栈。"""

    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:  # noqa: D102
        return


def start_mock_server(
    host: str = "127.0.0.1", port: int = 0, profile: str = "demo", model: str = "mock-cs"
) -> tuple[QuietServer, str]:
    """在后台线程启动 Mock 服务，返回 (server, base_url)。"""
    httpd = QuietServer((host, port), make_handler(profile, model))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"http://{host}:{httpd.server_address[1]}"


if __name__ == "__main__":  # 手动起服务：python -m aisvc.mock_server
    srv, url = start_mock_server(port=8765)
    print(f"Mock AI 客服已启动: {url}  (profile=demo, Ctrl+C 退出)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.shutdown()
