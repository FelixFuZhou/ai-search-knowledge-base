#!/usr/bin/env python3
"""把 背诵稿/*.md + 题库/*.md 解析成 deck.json，供题卡工具使用。

单一真相来源：内容永远只在 markdown 里维护。改完背诵稿重跑这个脚本，
再重新发布 artifact 即可。

    python3 tool/build_deck.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "背诵稿"
BANK = ROOT / "题库"

MODULES = {
    "A": "LLM 基本功",
    "B": "推理与服务基础设施",
    "C": "Agent 边界、工具与可靠性",
    "D": "上下文、RAG 与记忆",
    "E": "编排、多 Agent 与协议",
    "F": "评测、可观测与排错",
    "G": "安全与上线",
}

# 背诵稿里的块标记 → 字段。按关键词判定，容忍标记文案的小变化。
def classify(marker: str) -> str | None:
    m = marker
    if "30 秒" in m and "逐字背" in m:
        return "l0"
    if "开场" in m and "逐字背" in m:
        return "l0"
    if "骨架" in m:
        return "skeleton"
    if "📖" in m or "术语" in m or "拆开讲" in m:
        return "terms"
    if "别说" in m:
        return "avoid"
    if "素材槽" in m:
        return "slot"
    if "降级话术" in m:
        return "fallback"
    if "L2" in m:
        return "l2"
    if "L3" in m:
        return "l3"
    if "必须覆盖" in m or "失败模式" in m or "验证" in m or "调查路径" in m \
            or "缓解措施" in m or "机制" in m or "Agent 场景" in m or "排错" in m:
        return "l1"
    if "被追问" in m or "分钟" in m or "秒" in m:
        return "l1"
    return None


def split_blocks(body: str) -> dict[str, str]:
    """按 **标记** 切块。标记独占一行（可能后接说明）。"""
    out: dict[str, list[str]] = {}
    current: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if current and buf:
            text = "\n".join(buf).strip()
            if text:
                out.setdefault(current, []).append(text)

    for line in body.splitlines():
        stripped = line.strip()
        # 标记形如 **⏱ 30 秒｜逐字背** 或 **🦴 骨架**：内容跟在同一行
        m = re.match(r"^\*\*(.+?)\*\*(（[^）]*）)?\s*[：:]?\s*(.*)$", stripped)
        if m and not stripped.startswith("**Why"):
            flush()
            kind = classify(m.group(1))
            if kind is None:
                # 不是块标记（正文里的加粗），当普通内容处理。
                # 注意：上面已经 flush 过了，这里必须重置 buf，否则内容会被写两次。
                buf = [line]
                continue
            current = kind
            trailing = m.group(3).strip()
            buf = [trailing] if trailing else []
            continue
        # 引用块开头的 ⚠️ 提示行不算标记
        buf.append(line)
    flush()
    return {k: "\n\n".join(v).strip() for k, v in out.items()}


def strip_md(text: str) -> str:
    """去掉引用符号和链接语法，保留可读的纯文本 + 换行。"""
    lines = []
    for ln in text.splitlines():
        ln = re.sub(r"^>\s?", "", ln)
        lines.append(ln)
    t = "\n".join(lines)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)      # 链接 → 文字
    t = re.sub(r"<a id=\"[^\"]+\"></a>", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"\n-{3,}\s*$", "", t)          # 去掉块尾的分隔线
    return t.strip()


def parse_scripts() -> dict[str, dict]:
    qs: dict[str, dict] = {}
    for path in sorted(SCRIPTS.glob("[A-G]-背诵稿.md")):
        mod = path.name[0]
        text = path.read_text(encoding="utf-8")
        # 按 ## Qxx 切
        parts = re.split(r"^## (Q\d\d) · (.+?)$", text, flags=re.M)
        for i in range(1, len(parts), 3):
            qid, title, body = parts[i], parts[i + 1].strip(), parts[i + 2]
            star = "⭐" in title
            title = title.replace("⭐必背", "").replace("⭐高频", "").replace("⭐", "")
            title = re.sub(r"\s*⭐.*$", "", title).strip()
            blocks = split_blocks(body)
            qs[qid] = {
                "id": qid,
                "module": mod,
                "moduleName": MODULES[mod],
                "title": title,
                "star": star,
                **{k: strip_md(v) for k, v in blocks.items()},
            }
    return qs


def parse_bank() -> dict[str, str]:
    """题库里的采分点原文 —— 评分时喂给模型当判卷标准。"""
    rubrics: dict[str, str] = {}
    for path in sorted(BANK.glob("[A-G]-*.md")):
        text = path.read_text(encoding="utf-8")
        parts = re.split(r"^## (Q\d\d) · .+?$", text, flags=re.M)
        for i in range(1, len(parts), 2):
            rubrics[parts[i]] = strip_md(parts[i + 1])
    return rubrics


def main() -> int:
    qs = parse_scripts()
    rubrics = parse_bank()
    missing_rubric = []
    for qid, q in qs.items():
        if qid in rubrics:
            q["rubric"] = rubrics[qid]
        else:
            missing_rubric.append(qid)

    ordered = [qs[k] for k in sorted(qs)]
    out = {"version": 1, "modules": MODULES, "questions": ordered}
    dest = ROOT / "tool" / "deck.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # 质检
    print(f"题目数：{len(ordered)}")
    print(f"缺采分点：{missing_rubric or '无'}")
    for field in ("l0", "skeleton", "terms", "l1", "l2", "l3", "avoid", "rubric"):
        n = sum(1 for q in ordered if q.get(field))
        print(f"  {field:9s} {n}/{len(ordered)}")
    empty_l0 = [q["id"] for q in ordered if not q.get("l0")]
    if empty_l0:
        print(f"⚠️  缺 L0 逐字稿：{empty_l0}")
    print(f"\n写入 {dest}  ({dest.stat().st_size // 1024} KB)")

    tpl = ROOT / "tool" / "deck.template.html"
    if tpl.exists():
        html = tpl.read_text(encoding="utf-8").replace(
            "/*__DECK_JSON__*/null",
            json.dumps(out, ensure_ascii=False, separators=(",", ":")),
        )
        page = ROOT / "tool" / "deck.html"
        page.write_text(html, encoding="utf-8")
        print(f"写入 {page}  ({page.stat().st_size // 1024} KB)")
    else:
        print("（模板 deck.template.html 还没建，跳过 HTML 渲染）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
