"""Mock 订单后端。

两个要点：
1. **能注入故障** —— M3 和故障注入 F1/F2 要用。M0 先建好，之后不用改。
2. **资源级 ACL** —— 订单属于某个用户。工具级鉴权（能不能调 get_order）
   和资源级鉴权（能不能看这个订单）是两回事，后者最常被漏（Q54）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass


class OrderServiceError(Exception):
    """结构化的后端错误。code 决定了 Agent 该怎么办（Q27）。"""

    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


# ---------------------------------------------------------------- 故障注入
@dataclass
class FaultConfig:
    """故障开关。M0 用不到，F1/F2 演练时打开。"""
    timeout_on: set[str] | None = None      # 这些 order_id 触发超时
    error_500_on: set[str] | None = None    # 这些 order_id 触发 500
    slow_ms: int = 0                        # 全局延迟

    def check(self, order_id: str) -> None:
        if self.slow_ms:
            time.sleep(self.slow_ms / 1000)
        if self.timeout_on and order_id in self.timeout_on:
            raise OrderServiceError("TIMEOUT", "订单服务响应超时", retryable=True)
        if self.error_500_on and order_id in self.error_500_on:
            raise OrderServiceError("INTERNAL", "订单服务内部错误", retryable=True)


FAULTS = FaultConfig()


# ---------------------------------------------------------------- 数据
_ORDERS = {
    "ORD-1001": {
        "order_id": "ORD-1001", "user_id": "U-001", "status": "已发货",
        "amount": 487.50, "currency": "CNY", "created_at": "2026-08-28",
        "items": [{"name": "机械键盘", "qty": 1}],
        "shipped_at": "2026-08-30", "refundable": True,
    },
    "ORD-1002": {
        "order_id": "ORD-1002", "user_id": "U-001", "status": "已完成",
        "amount": 129.00, "currency": "CNY", "created_at": "2026-07-15",
        "items": [{"name": "鼠标垫", "qty": 2}],
        "shipped_at": "2026-07-16", "refundable": False,
    },
    # 属于别人 —— 用来验证资源级 ACL。M0 的排错演练就靠它
    "ORD-1003": {
        "order_id": "ORD-1003", "user_id": "U-999", "status": "待发货",
        "amount": 2380.00, "currency": "CNY", "created_at": "2026-09-01",
        "items": [{"name": "显示器", "qty": 1}],
        "shipped_at": None, "refundable": True,
    },
}


# ---------------------------------------------------------------- 只读接口
def get_order(order_id: str, *, user_id: str) -> dict:
    """按订单号查单个订单。

    资源级 ACL 在这里强制，不在 prompt 里。即使模型被注入说服去查别人的
    订单，这一层也会拒绝 —— 这就是"权限在执行层强制"的意思。
    """
    FAULTS.check(order_id)
    order = _ORDERS.get(order_id)
    if order is None:
        raise OrderServiceError("NOT_FOUND", f"订单 {order_id} 不存在", retryable=False)
    if order["user_id"] != user_id:
        # 注意错误信息：不告诉调用方"这个订单存在但不属于你"，
        # 否则等于提供了一个订单号枚举接口。
        raise OrderServiceError("FORBIDDEN", f"订单 {order_id} 不存在或无权访问",
                                retryable=False)
    return dict(order)


def list_recent_orders(user_id: str, limit: int = 5) -> list[dict]:
    """查某用户最近的订单。只返回摘要字段 —— 数据最小化。"""
    rows = [o for o in _ORDERS.values() if o["user_id"] == user_id]
    rows.sort(key=lambda o: o["created_at"], reverse=True)
    return [
        {k: o[k] for k in ("order_id", "status", "amount", "created_at")}
        for o in rows[:limit]
    ]
