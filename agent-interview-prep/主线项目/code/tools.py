"""工具定义与执行。

面试点：
- Q23 单一职责：不做 `manage_order(action=...)` 万能工具。拆开才能做
  工具级权限控制，也才写得出"不该调用"的负向测试。
- Q24 描述里写**反例**："已知订单号时不要用 get_order_history"。
- Q23 结构化错误：{code, retryable, user_message}，不把堆栈丢给模型。
- Q54 只读默认：M0 的工具集里**根本没有写工具**，不是"有但不该用"。
"""
from __future__ import annotations

from typing import Any

from order_service import OrderServiceError, get_order, list_recent_orders

# 工具定义的顺序必须稳定 —— 它进 prompt 前缀，顺序一变缓存全失效（Q17）。
TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_order",
        "description": (
            "按订单号查询**单个**订单的当前状态、金额和物流信息。"
            "当用户提供了明确的订单号时使用。"
            "已知订单号时优先用这个，不要用 get_order_history。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "订单号，格式形如 ORD-1001",
                    "pattern": r"^ORD-\d{4}$",
                }
            },
            "required": ["order_id"],
            "additionalProperties": False,
        },
        # strict=True：让 SDK 保证参数一定符合 schema（Q10）。
        # 但它只保证结构合法，不保证 order_id 真实存在 —— 服务端仍要校验。
        "strict": True,
    },
    {
        "name": "get_order_history",
        "description": (
            "查询当前用户**最近的多个**订单摘要，用于用户没给订单号、"
            "需要列出候选让他确认的场景。"
            "**已知订单号时不要用这个**，用 get_order。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "返回条数，1-10",
                    "minimum": 1,
                    "maximum": 10,
                }
            },
            "required": ["limit"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}

# M0 是只读 Agent。这个 allowlist 在执行层强制，模型看不到也改不了。
READ_ONLY_ALLOWLIST = {"get_order", "get_order_history"}


class ToolResult:
    """工具执行结果。ok 决定了它是不是一次"成功的工具事件"。"""

    def __init__(self, *, ok: bool, payload: Any, user_message: str | None = None,
                 code: str | None = None, retryable: bool = False):
        self.ok = ok
        self.payload = payload
        self.user_message = user_message
        self.code = code
        self.retryable = retryable

    def for_model(self) -> Any:
        """返回给模型的内容。绝不含堆栈、主机名、SQL、密钥。"""
        if self.ok:
            return self.payload
        return {
            "error": True,
            "code": self.code,
            "retryable": self.retryable,      # 让 runtime 能判断要不要重试
            "message": self.user_message,     # 可行动的描述，不是 "HTTP 500"
        }


def execute_tool(name: str, args: dict, *, user_id: str) -> ToolResult:
    """统一的工具执行入口 —— 这就是"工具网关"的雏形。

    权限检查在这里，不在 prompt 里。M2 会在这里加审批网关。
    """
    # 第一层：工具级 allowlist。模型即使被注入说服要调写工具，也过不了这里。
    if name not in READ_ONLY_ALLOWLIST:
        return ToolResult(ok=False, payload=None, code="TOOL_NOT_ALLOWED",
                          user_message=f"工具 {name} 不在当前会话的允许列表内",
                          retryable=False)
    try:
        if name == "get_order":
            # 第三层：资源级 ACL 在 order_service 内部强制
            return ToolResult(ok=True, payload=get_order(args["order_id"], user_id=user_id))
        if name == "get_order_history":
            return ToolResult(ok=True, payload=list_recent_orders(user_id, args["limit"]))
    except OrderServiceError as e:
        return ToolResult(ok=False, payload=None, code=e.code,
                          user_message=e.message, retryable=e.retryable)
    except KeyError as e:
        # strict schema 应该挡住这种情况；挡不住说明 schema 写漏了
        return ToolResult(ok=False, payload=None, code="BAD_ARGS",
                          user_message=f"缺少必填参数 {e}", retryable=False)

    return ToolResult(ok=False, payload=None, code="UNKNOWN_TOOL",
                      user_message=f"未知工具 {name}", retryable=False)
