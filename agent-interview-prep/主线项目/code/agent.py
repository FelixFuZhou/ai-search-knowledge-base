"""手写的 3 步 tool-use loop。

**为什么不用 SDK 的 tool_runner？**
不是因为它不好，而是 M0 的目的就是把循环拆开看清楚：每一步的
span、多维预算、终止原因分类，这些 runner 不直接暴露。手写过一遍，
面试时你才说得出"框架替我解决了什么"（Q19/Q21）。
M1 之后可以换成 runner，那时你已经知道自己放弃了什么。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

import anthropic

import config
from tools import TOOLS, execute_tool
from trace import Trace

# 系统指令。放在 prompt 最前面且保持稳定 —— 变化频率从低到高排列，
# 这样前缀能吃到 prompt cache（Q17）。
# 注意：这段太短，多半达不到最小可缓存长度，cache_read 会是 0。
# 这不是 bug，是缓存的最小长度限制 —— 值得亲自看一眼确认。
SYSTEM_PROMPT = """你是订单查询助手。你只能查询订单信息，不能修改任何订单。

规则：
1. 用户给了明确订单号时，用 get_order 查询后再回答。
2. 用户没给订单号时，用 get_order_history 列出候选，**让用户确认是哪一个**，不要自己挑。
3. 工具返回错误时，用自然语言向用户说明，不要重复调用同一个工具。
4. 你不能执行取消、退款、修改等任何写操作。用户提出时，明确告知你没有这个权限。
5. 只根据工具返回的真实数据回答。没查到就说没查到，不要编造订单信息。"""


# 终止原因必须分类，不能只有一个 "failed"（Q22）。
# 不同终止对应不同的下一步动作和不同的告警级别。
class Stop:
    SUCCESS = "success"                # 正常给出答案
    BUDGET_STEPS = "budget_steps"      # 步数用尽
    BUDGET_TIME = "budget_time"        # 时长用尽
    BUDGET_TOKENS = "budget_tokens"    # token 用尽
    REPEATED_CALL = "repeated_call"    # 卡在同一个工具上
    MAX_TOKENS = "max_tokens"          # 单次响应被截断
    REFUSAL = "refusal"                # 模型因安全策略拒绝
    API_ERROR = "api_error"            # 调用失败


@dataclass
class Result:
    answer: str | None
    stop_reason: str
    trace: Trace

    @property
    def did_call_tool_successfully(self) -> bool:
        """判断"任务是否真的做了"，只看工具事件，不看模型说了什么（Q28）。

        M0 是只读的，所以这里只是个判据示范；M2 加了写工具之后，
        面向用户的成功回复必须由这个值为 True 才能渲染。
        """
        return any(e["ok"] for e in self.trace.tool_events)


def _build_request(messages: list[dict]) -> dict:
    """组装请求。渲染顺序是 tools -> system -> messages，
    所以稳定的东西（工具定义、系统指令）天然在前缀。"""
    req = {
        "model": config.MODEL,
        "max_tokens": config.MAX_TOKENS,
        "tools": TOOLS,
        "system": [{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},   # 缓存到这里为止的前缀
        }],
        "messages": messages,
        # Opus 5 的 thinking 默认就是 adaptive，这里显式写出来是为了可读。
        "thinking": {"type": "adaptive"},
    }
    if config.ENABLE_REFUSAL_FALLBACK:
        req["betas"] = [config.FALLBACK_BETA]
        req["fallbacks"] = [{"model": config.FALLBACK_MODEL}]
    return req


def run(user_input: str, *, user_id: str, trace_enabled: bool = True) -> Result:
    client = anthropic.Anthropic()
    trace = Trace(
        user_id=user_id, model=config.MODEL,
        prompt_version=config.PROMPT_VERSION,
        tools_version=config.TOOLS_SCHEMA_VERSION,
        enabled=trace_enabled,
    )
    trace.set(user_input=user_input)

    messages: list[dict] = [{"role": "user", "content": user_input}]
    started = time.time()
    seen_calls: set[str] = set()      # 简易循环检测；M3 会加参数归一化
    answer: str | None = None
    stop = Stop.API_ERROR

    for step in range(1, config.MAX_STEPS + 1):
        # ---- 预算检查：多维度同时限。只限步数会被单步塞爆上下文绕过 ----
        if time.time() - started > config.MAX_WALL_SECONDS:
            stop = Stop.BUDGET_TIME
            break
        used = trace.totals["input_tokens"] + trace.totals["output_tokens"]
        if used > config.MAX_TOTAL_TOKENS:
            stop = Stop.BUDGET_TOKENS
            break

        with trace.span(f"step-{step}", "llm_call", step=step) as sp:
            try:
                req = _build_request(messages)
                if config.ENABLE_REFUSAL_FALLBACK:
                    resp = client.beta.messages.create(**req)
                else:
                    resp = client.messages.create(**req)
            except anthropic.RateLimitError as e:
                trace.set(error_class="RateLimitError", detail=str(e))
                stop = Stop.API_ERROR
                break
            except anthropic.APIStatusError as e:
                trace.set(error_class="APIStatusError", status=e.status_code)
                stop = Stop.API_ERROR
                break
            except anthropic.APIConnectionError as e:
                trace.set(error_class="APIConnectionError", detail=str(e))
                stop = Stop.API_ERROR
                break

            cost = config.estimate_cost(resp.model, resp.usage.input_tokens,
                                        resp.usage.output_tokens)
            trace.record_usage(resp.model, resp.usage, cost)
            trace.totals["steps"] = step
            sp.attrs.update({
                "served_by": resp.model,           # 回退发生时这里会变
                "api_stop_reason": resp.stop_reason,
                "in_tokens": resp.usage.input_tokens,
                "out_tokens": resp.usage.output_tokens,
                "cache_read": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
                "cache_write": getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
                "cost_usd": round(cost, 6),
            })

            # ---- 终止分支 ----
            if resp.stop_reason == "refusal":
                # stop_details 只在 refusal 时才有值，别无条件读它
                details = getattr(resp, "stop_details", None)
                if details:
                    sp.attrs["refusal_category"] = details.category
                stop = Stop.REFUSAL
                break
            if resp.stop_reason == "max_tokens":
                stop = Stop.MAX_TOKENS
                break

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            texts = [b.text for b in resp.content if b.type == "text"]

            if not tool_uses:
                answer = "\n".join(texts).strip()
                stop = Stop.SUCCESS
                break

            # 助手回合必须整块回传（含 tool_use 块）
            messages.append({"role": "assistant", "content": resp.content})

            # ---- 执行工具 ----
            # 并行调用时：所有 tool_result 必须放进**同一条** user 消息。
            # 拆成多条会悄悄训练模型不再并行调用。
            tool_results = []
            for tu in tool_uses:
                key = f"{tu.name}:{json.dumps(tu.input, sort_keys=True, ensure_ascii=False)}"
                if key in seen_calls:
                    stop = Stop.REPEATED_CALL
                    sp.attrs["repeated"] = key
                    break
                seen_calls.add(key)

                with trace.span(f"tool:{tu.name}", "tool_call",
                                args=tu.input) as tsp:
                    res = execute_tool(tu.name, tu.input, user_id=user_id)
                    tsp.attrs.update({"ok": res.ok, "code": res.code,
                                      "result": res.for_model()})

                trace.record_tool_event(name=tu.name, ok=res.ok,
                                        args=tu.input, result=res.for_model())
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(res.for_model(), ensure_ascii=False),
                    **({"is_error": True} if not res.ok else {}),
                })

            if stop == Stop.REPEATED_CALL:
                break
            messages.append({"role": "user", "content": tool_results})
    else:
        # for 正常跑完 = 步数用尽还没给出答案
        stop = Stop.BUDGET_STEPS

    trace.finish(stop_reason=stop, answer=answer)
    return Result(answer=answer, stop_reason=stop, trace=trace)
