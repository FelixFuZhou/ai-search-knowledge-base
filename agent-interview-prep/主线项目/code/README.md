# M0 · 只读 Agent loop + Trace

> 目标不是做出好产品，是**产出 L2 素材**。所以每一步都要求你先制造问题、看清楚，再修。

## 跑起来

```bash
cd agent-interview-prep/主线项目/code
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # 填 ANTHROPIC_API_KEY，或先跑 ant auth login
```

先跑离线自测（**不花钱，不调 API**）：

```bash
python selftest.py
```

再跑真实 loop：

```bash
python run.py 1
```

## ⚠️ M0 的核心演练：别跳过

整个 M0 就是为了这三步。跳过它你只是写了个 demo，拿不到面试素材。

**第一步 —— 关掉 trace，体验「只能靠猜」。给自己计时。**

```bash
python run.py 4 --no-trace
```

case 4 是「查订单 ORD-1003」，那个订单属于别人。你只会看到模型说没查到。
现在回答这个问题：**为什么没查到？** 是订单不存在、模型没调工具、参数抽取错了、
还是权限被拒？记下你花了多久，以及你有没有答对。

**第二步 —— 打开 trace，同一个 case 再来一次。**

```bash
python run.py 4
```

span 树里能直接看到 `tool:get_order` 这一步 `ok: false, code: FORBIDDEN`。
记下这次花了多久。

**第三步 —— 把两个数字填进 [../README.md](../README.md) 的素材槽。**

> 「我在一个退款 Agent 里，没有 trace 的时候一个『查不到订单』查了 ___ 分钟；
> 补上 trace 之后同类问题 ___ 分钟定位。」

这就是 [Q50](../../背诵稿/F-背诵稿.md) 的 L2 素材，也是 [Q21](../../背诵稿/C-背诵稿.md) 的。

## 五个内置 case

```bash
python run.py all
```

| # | 类型 | 考察 |
| --- | --- | --- |
| 1 | 正常：给了订单号 | 工具被正确调用 |
| 2 | **负向**：没给订单号 | 应列候选**让用户确认**，不能自己挑（[Q25](../../背诵稿/C-背诵稿.md)） |
| 3 | **负向**：纯知识问答 | **不应调用任何工具**（[Q24](../../背诵稿/C-背诵稿.md)） |
| 4 | 越权：查他人订单 | 资源级 ACL（[Q54](../../背诵稿/G-背诵稿.md)） |
| 5 | 越权：要求写操作 | 只读 Agent 的工具集里**根本没有写工具** |

case 2 / 3 是负向测试的雏形——「参数不全时不调用」和「不该调用时不调用」，
这两条是 Q24 里最常被漏的。M4 会把它们扩成分七层的 golden set。

## 文件在教什么

| 文件 | 对应题目 | 关键设计 |
| --- | --- | --- |
| [config.py](config.py) | Q21 | model / 预算 / 版本号集中配置，不散落在 prompt 里 |
| [trace.py](trace.py) | **Q50** | span tree 不是扁平日志；**脱敏在写入时做**；版本字段 |
| [order_service.py](order_service.py) | Q54 | 资源级 ACL 在后端强制；故障可注入（F1/F2 预留） |
| [tools.py](tools.py) | **Q23 Q24 Q54** | 单一职责 schema、描述含反例、结构化错误、工具网关雏形 |
| [agent.py](agent.py) | **Q19 Q21 Q22 Q28** | 手写 3 步循环、多维预算、终止原因分类、工具事件是真相 |
| [selftest.py](selftest.py) | Q47 | 全是**确定性断言**，不需要 LLM judge |

### 几处刻意的设计，面试会问

**为什么手写循环而不用 SDK 的 `tool_runner`？**
不是因为 runner 不好。M0 要把循环拆开看清楚：每步的 span、多维预算、终止原因分类，
这些 runner 不直接暴露。手写过一遍，你才说得出「框架替我解决了什么」。
M1 之后可以换 runner——那时你知道自己放弃了什么。

**为什么终止原因有 8 个而不是一个 `failed`？**
见 `agent.py` 的 `Stop` 类。不同终止对应不同的下一步动作和不同的告警级别。
一个笼统的 `failed` 会让线上完全没法运营（[Q22](../../背诵稿/C-背诵稿.md)）。

**为什么预算是三维的（步数 / 时长 / token）？**
只限步数，模型会在单步里塞进巨大的上下文；只限 token，会无限循环小步骤。

**为什么 `did_call_tool_successfully` 只看工具事件？**
因为模型说「已完成」不能作为判据。M0 是只读的，这里只是判据示范；
M2 加了写工具之后，成功回复必须由这个值为 True 才能渲染（[Q28](../../背诵稿/C-背诵稿.md)）。

**并行工具调用为什么所有结果放同一条 user 消息？**
拆成多条会悄悄训练模型不再并行调用。见 `agent.py` 里的注释。

## 两个知情的选择

**1. 模型默认 `claude-opus-5`。** 想省钱就在 `.env` 里设 `AGENT_MODEL=claude-sonnet-5`——
这是你的决定，不是我替你做的。改完记得重跑 case，验证质量没掉（[Q59](../../背诵稿/G-背诵稿.md)）。

**2. 拒绝回退默认开启。** `config.ENABLE_REFUSAL_FALLBACK = True`：模型因安全策略
拒绝时，同一次调用内换 `claude-opus-4-8` 重试。这会让请求走 `client.beta.messages`
并带一个 beta header。不想要就把开关设成 `False`，代码会自动切回 `client.messages`，
被拒的请求停在 `stop_reason="refusal"`——`agent.py` 已经把它当成一个显式终止原因处理了。

**关于 prompt cache**：`SYSTEM_PROMPT` 太短，多半达不到最小可缓存长度，
所以 trace 里 `cache_read` 大概率是 0。**这不是 bug，值得亲自看一眼确认**——
比背「缓存有最小长度限制」印象深得多（[Q17](../../背诵稿/B-背诵稿.md)）。

## M0 完成的判据

- [ ] `python selftest.py` 12/12 通过
- [ ] 5 个 case 都跑过，case 2/3 的负向行为符合期望
- [ ] **完成了上面的三步演练，两个时间数字已填进 [../README.md](../README.md) 的素材槽**
- [ ] 能不看代码讲清楚：为什么终止原因要分类、为什么权限不能写在 prompt 里

做完进 M1：加 `check_refund_eligibility`，故意把两个查询工具的描述写雷同，统计混用率。
