#!/usr/bin/env python3
"""离线自测：不调用 API，验证工具层、权限、脱敏、trace 开关。

    python selftest.py

这些断言就是 M4 golden set 的雏形 —— 注意它们全是**确定性评分**
（工具是否被调用、权限是否被拦截、错误码对不对），不需要 LLM judge。
Q47 说的"能写出精确断言的一律用确定性"，指的就是这类。
"""
import sys

import order_service
from tools import execute_tool
from trace import Trace, redact


def main() -> int:
    checks: list[tuple[str, bool]] = []

    def check(name: str, cond: bool) -> None:
        checks.append((name, bool(cond)))

    # ---- 工具层 ----
    r = execute_tool("get_order", {"order_id": "ORD-1001"}, user_id="U-001")
    check("正常查询返回真实状态", r.ok and r.payload["status"] == "已发货")

    r = execute_tool("get_order", {"order_id": "ORD-1003"}, user_id="U-001")
    check("资源级 ACL：查他人订单被拒", not r.ok and r.code == "FORBIDDEN")
    check("越权错误信息不泄露订单存在性",
          "不存在或无权访问" in (r.user_message or ""))

    r = execute_tool("cancel_order", {"order_id": "ORD-1001"}, user_id="U-001")
    check("工具级 allowlist：写工具被挡在执行层",
          not r.ok and r.code == "TOOL_NOT_ALLOWED")

    r = execute_tool("get_order", {"order_id": "ORD-9999"}, user_id="U-001")
    check("不存在的订单：不可重试", not r.ok and r.retryable is False)

    r = execute_tool("get_order_history", {"limit": 5}, user_id="U-001")
    check("history 只返回摘要字段（数据最小化）",
          r.ok and set(r.payload[0]) == {"order_id", "status", "amount", "created_at"})

    # ---- 故障注入（F1/F2 演练）----
    order_service.FAULTS.timeout_on = {"ORD-1001"}
    r = execute_tool("get_order", {"order_id": "ORD-1001"}, user_id="U-001")
    check("超时被分类为可重试", not r.ok and r.code == "TIMEOUT" and r.retryable)
    check("给模型的错误不含堆栈", "Traceback" not in str(r.for_model()))
    order_service.FAULTS.timeout_on = None

    # ---- 脱敏两条路径 ----
    check("敏感 key 整字段屏蔽", redact({"phone": "13800138000"}) == {"phone": "***"})
    check("自由文本里的手机号被打码",
          redact({"note": "联系 13800138000"})["note"] == "联系 1**********")

    # ---- trace 开关 ----
    t = Trace(user_id="U-001", model="m", prompt_version="v1", tools_version="v1")
    with t.span("step-1", "llm_call", step=1) as sp:
        sp.attrs.update({"in_tokens": 10})
    t.finish(stop_reason="success", answer="ok")
    check("trace 开启时渲染出 span 树", "step-1" in t.render())

    t2 = Trace(user_id="U-001", model="m", prompt_version="v1",
               tools_version="v1", enabled=False)
    with t2.span("step-1", "llm_call") as sp:
        sp.attrs.update({"x": 1})          # 关闭时写 attrs 也不能崩
    t2.finish(stop_reason="success", answer="ok")
    check("trace 关闭时不崩且给出提示", "只能靠猜" in t2.render())

    # ---- 输出 ----
    failed = [n for n, ok in checks if not ok]
    for name, ok in checks:
        print(f"  {'✅' if ok else '❌'} {name}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
