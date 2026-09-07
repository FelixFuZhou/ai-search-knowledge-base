"""集中配置。M0 的所有可调项都在这里，不散落在代码里。

面试点（Q21）：model / instructions / budget / policy 都是**显式配置**，
不是散落在 prompt 里的魔法字符串。版本号会进 trace（Q50）。
"""
import os

from dotenv import load_dotenv

load_dotenv()

# ---- model ----
MODEL = os.getenv("AGENT_MODEL", "claude-opus-5")
MAX_TOKENS = 4096

# 拒绝回退：模型因安全策略拒绝时，同一次调用内换模型重试。
# 关掉它，被拒的请求就直接停在 stop_reason="refusal"。
ENABLE_REFUSAL_FALLBACK = True
FALLBACK_MODEL = "claude-opus-4-8"
FALLBACK_BETA = "server-side-fallback-2026-06-01"

# ---- runtime 预算（Q22：多维度同时限，不能只限步数）----
MAX_STEPS = 3           # M0 的硬要求：最多 3 步
MAX_WALL_SECONDS = 30   # 总时长。重试不计入 step 但计入时长
MAX_TOTAL_TOKENS = 50_000

# ---- 版本号：改了 prompt 一定要改这里，否则 trace 无法归因 ----
PROMPT_VERSION = "v1"
TOOLS_SCHEMA_VERSION = "v1"

# ---- 成本（$/1M token，用于 trace 里的成本估算）----
PRICING = {
    "claude-opus-5": {"in": 5.00, "out": 25.00},
    "claude-opus-4-8": {"in": 5.00, "out": 25.00},
    "claude-sonnet-5": {"in": 2.00, "out": 10.00},
    "claude-haiku-4-5": {"in": 1.00, "out": 5.00},
}


def estimate_cost(model: str, in_tokens: int, out_tokens: int) -> float:
    p = PRICING.get(model)
    if not p:
        return 0.0
    return in_tokens / 1e6 * p["in"] + out_tokens / 1e6 * p["out"]
