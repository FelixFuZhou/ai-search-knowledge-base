#!/usr/bin/env python3
"""M0 入口。

    python run.py 1              # 跑 case 1
    python run.py 4 --no-trace   # 关掉 trace 跑 —— 这是 M0 的核心演练
    python run.py all            # 全跑一遍
    python run.py 1 --save t.json

**M0 的演练顺序（别跳过）：**
  1. 先 `python run.py 4 --no-trace`，只看输出，试着回答"为什么失败了"。
     给自己计时。
  2. 再 `python run.py 4`，看 trace。同一个问题，这次用了多久。
  3. 把两个时间填进 主线项目/README.md 的素材槽 —— 那是 Q50 的 L2 素材。
"""
from __future__ import annotations

import argparse
import sys
import time

import agent
from cases import CASES


def run_one(key: str, *, trace_enabled: bool, save: str | None) -> None:
    case = CASES[key]
    print("=" * 72)
    print(f"case {key} · {case['name']}")
    print(f"user_id : {case['user_id']}")
    print(f"输入    : {case['input']}")
    print(f"期望    : {case['expect']}")
    print("-" * 72)

    t0 = time.time()
    result = agent.run(case["input"], user_id=case["user_id"],
                       trace_enabled=trace_enabled)
    elapsed = time.time() - t0

    print(f"\n【回答】\n{result.answer or '(无)'}")
    print(f"\n【终止原因】{result.stop_reason}")
    # 判断"是否真的做了事"只看工具事件，不看模型说了什么（Q28）
    print(f"【成功的工具事件】{result.did_call_tool_successfully}"
          f"  共 {len(result.trace.tool_events)} 次工具调用")
    print(f"【耗时】{elapsed:.2f}s"
          f"  【成本】${result.trace.totals['cost_usd']:.5f}"
          f"  【步数】{result.trace.totals['steps']}")

    print(f"\n【Trace】\n{result.trace.render()}")

    if save:
        result.trace.save(save)
        print(f"\ntrace 已写入 {save}")


def main() -> int:
    p = argparse.ArgumentParser(description="M0 只读 Agent loop")
    p.add_argument("case", help="case 编号，或 all")
    p.add_argument("--no-trace", action="store_true",
                   help="关掉 trace —— 体验『只能靠猜』，这是 M0 的演练")
    p.add_argument("--save", help="把 trace 存成 JSON")
    args = p.parse_args()

    keys = list(CASES) if args.case == "all" else [args.case]
    for k in keys:
        if k not in CASES:
            print(f"没有 case {k}。可选：{', '.join(CASES)} 或 all")
            return 1
        run_one(k, trace_enabled=not args.no_trace, save=args.save)
    return 0


if __name__ == "__main__":
    sys.exit(main())
