"""Trace：span tree，不是扁平日志。

面试点（Q50）：
- 扁平日志看不出因果和耗时归属，所以用 span tree
- **脱敏在写入时做，不是查询时做** —— trace 的访问面比业务数据宽得多
- 版本字段是最常被漏、排错时最有用的（"上周还好好的，改了什么？"）

自查标准：拿一条失败的 trace，只看它能不能还原出
"发生了什么 / 为什么 / 该改哪"。还原不了就说明字段不够。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------- 脱敏
_CARD = re.compile(r"\b\d{12,19}\b")
_PHONE = re.compile(r"\b1[3-9]\d{9}\b")
_SENSITIVE_KEYS = {"card", "card_no", "phone", "email", "address", "id_card"}


def redact(value: Any) -> Any:
    """写入 trace 前脱敏。真实项目里这里要按数据分类表来做。"""
    if isinstance(value, dict):
        return {
            k: ("***" if k in _SENSITIVE_KEYS else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _PHONE.sub("1**********", _CARD.sub("****", value))
    return value


# ---------------------------------------------------------------- Span
@dataclass
class Span:
    name: str
    kind: str                     # task | llm_call | tool_call | policy
    start: float
    end: float | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    children: list["Span"] = field(default_factory=list)
    error: str | None = None

    @property
    def ms(self) -> int:
        return int(((self.end or time.time()) - self.start) * 1000)

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "kind": self.kind,
            "ms": self.ms,
            **({"error": self.error} if self.error else {}),
            **self.attrs,
        }
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d


class Trace:
    """一次任务一个 Trace。enabled=False 时所有记录都是 no-op ——
    这是 M0 演练用的：先关掉跑一遍，体验"只能靠猜"。"""

    def __init__(self, *, user_id: str, model: str, prompt_version: str,
                 tools_version: str, enabled: bool = True):
        self.enabled = enabled
        self.trace_id = uuid.uuid4().hex[:12]
        self.task_id = uuid.uuid4().hex[:12]
        self.root = Span(name="task", kind="task", start=time.time(), attrs={
            # 标识
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "user_id": user_id,
            # 版本 —— 最常被漏，排错时最有用
            "model": model,
            "prompt_version": prompt_version,
            "tools_schema_version": tools_version,
        })
        self._stack: list[Span] = [self.root]
        # 预算累计
        self.totals = {
            "steps": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.0,
        }
        self.tool_events: list[dict] = []   # 工具事件 = 判定成功的唯一真相（Q28）

    # ---- 记录 ----
    @contextmanager
    def span(self, name: str, kind: str, **attrs):
        if not self.enabled:
            yield _NullSpan()
            return
        s = Span(name=name, kind=kind, start=time.time(), attrs=redact(attrs))
        self._stack[-1].children.append(s)
        self._stack.append(s)
        try:
            yield s
        except Exception as exc:                      # noqa: BLE001
            s.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            s.end = time.time()
            self._stack.pop()

    def set(self, **attrs) -> None:
        if self.enabled:
            self._stack[-1].attrs.update(redact(attrs))

    def record_usage(self, model: str, usage, cost: float) -> None:
        self.totals["input_tokens"] += usage.input_tokens
        self.totals["output_tokens"] += usage.output_tokens
        self.totals["cache_read_tokens"] += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.totals["cache_write_tokens"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.totals["cost_usd"] += cost

    def record_tool_event(self, *, name: str, ok: bool, args: dict, result: Any) -> None:
        """工具事件独立记一份。判断'任务是否真的做了'只看这里，不看模型说了什么。"""
        self.tool_events.append(redact(
            {"tool": name, "ok": ok, "args": args, "result": result}
        ))

    def finish(self, *, stop_reason: str, answer: str | None) -> None:
        self.root.end = time.time()
        self.root.attrs.update({
            "stop_reason": stop_reason,          # 终止原因必须显式（Q22）
            "answer": redact(answer or ""),
            "totals": {**self.totals, "cost_usd": round(self.totals["cost_usd"], 6)},
            "tool_events": self.tool_events,
        })

    # ---- 输出 ----
    def to_dict(self) -> dict:
        return self.root.to_dict()

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    def render(self) -> str:
        if not self.enabled:
            return "(trace 已关闭 —— 你现在只能靠猜。用 --trace 再跑一遍对比)"
        lines: list[str] = []

        def walk(s: Span, depth: int) -> None:
            pad = "  " * depth
            head = f"{pad}├─ {s.name} [{s.kind}] {s.ms}ms"
            if s.error:
                head += f"  ✗ {s.error}"
            lines.append(head)
            skip = {"children"}
            for k, v in s.attrs.items():
                if k in skip:
                    continue
                text = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                text = str(text)
                if len(text) > 300:
                    text = text[:300] + "…"
                lines.append(f"{pad}│    {k}: {text}")
            for c in s.children:
                walk(c, depth + 1)

        walk(self.root, 0)
        return "\n".join(lines)


class _NullSpan:
    """trace 关闭时的占位：attrs 是真 dict，写进去直接丢掉。"""

    def __init__(self) -> None:
        self.attrs: dict[str, Any] = {}
        self.error: str | None = None
