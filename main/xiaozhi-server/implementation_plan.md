# 延迟监控系统修复与完善

## 问题背景
当前延迟监控系统里，大量链路阶段被归类为“其他”，按轮次汇总结果也不稳定，已经影响排查价值。

本次文档基于现有代码复核后修正，不再沿用之前评审稿中的错误结论。同时新增一个需求：

- `turn_id` 改为“该轮对话的用户首条文本截断值 + 时间戳”

---

## 复核后的问题清单

### 问题1：`latency_trace.py` 中阶段打点被硬编码为“其他”模块

`begin_turn()` 和 `mark_stage()` 调用 `monitor.record_event()` 时，`module` 参数始终写死为 `"其他"`。

这会导致 `receiveAudioHandle.py`、`listenMessageHandler.py` 中的关键阶段，例如：

- `start_to_chat.enter`
- `prefilter.enter`
- `intent.analyze.start`
- `chat.submit`
- `listen.detect.text_ready`

全部被归类为“其他”，这是当前分类异常的主要原因之一。

---

### 问题2：`_parse_module()` 分类规则覆盖不足

`latency_monitor.py` 中的 `_parse_module()` 目前只覆盖以下大类：

- ASR
- LLM
- TTS
- 工具调用

但现有链路里已经存在更多明确阶段名，当前规则无法识别，例如：

- `intent` / `意图`
- `memory` / `记忆`
- `prefilter` / `前置`
- `chat` / `对话`
- `vad` / `VAD`

这会导致即使后续不再硬编码 `"其他"`，仍有相当一部分阶段会继续落入“其他”。

---

### 问题3：`end_timer()` 的栈匹配机制存在状态破坏风险

`end_timer()` 当前先 `pop()` 栈顶元素，再校验 `stage_name` 是否匹配。

如果不匹配，会直接返回 `0`，但已经弹出的计时记录不会恢复，导致：

- 嵌套计时状态被破坏
- 后续 `end_timer()` 可能持续错配
- 汇总数据出现漏记或串记

这属于监控基础设施层面的真实缺陷。

---

### 问题4：同一轮对话的 `turn_id` 可能被拆裂，导致汇总不可信

这是上一版文档漏掉的关键问题。

当前 `latency_trace._ensure_turn_id()` 会优先复用 `conn.sentence_id`，否则回退到 `_latency_turn_id`。但主链路后续又会在多个位置重新生成新的 `conn.sentence_id`，例如：

- `ConnectionHandler.chat()` 顶层对话开始时
- `handle_user_intent()` 处理意图命中后
- `prefilterHandler.py` 中的部分工具回路

结果是同一轮用户输入，可能出现：

- 前半段阶段打点使用旧 turn id
- 后半段 LLM / TTS / 工具调用使用新的 sentence id

这会直接破坏：

- 单轮链路时间线
- turn 级汇总
- 实时日志可读性

所以本次修复不能只处理“模块分类”，还必须统一 turn id 生命周期。

---

### 问题5：关键链路监控覆盖仍不完整

以下几处确实存在监控缺口或口径不完整：

| 缺口 | 位置 | 当前情况 |
|------|------|----------|
| 意图识别耗时 | `intentHandler.py` | `analyze_intent_with_llm()` 无独立计时 |
| 记忆查询耗时 | `connection.py` | `memory.query_memory()` 无独立计时 |
| 前置路由耗时 | `receiveAudioHandle.py` / `prefilterHandler.py` | 只有 `mark_stage()`，没有实际耗时 |
| 流式 ASR 时延口径 | `asr/*_stream.py` | 复用了 `ASR处理` 计时，但更偏向收尾处理，缺少“首字延迟 / 最终结果延迟”区分 |

---

### 问题6：`mark_stage()` 只有瞬时打点，没有耗时语义

`mark_stage()` 当前只记录一条 `elapsed_sec=0.0` 的事件。

这本身没有错，但它只能表达“某阶段发生了”，不能表达“某阶段持续了多久”。如果继续把它当作耗时监控使用，会造成设计语义混乱。

因此需要明确拆分：

- 瞬时事件：`mark_stage()`
- 区间计时：`start_stage()` / `end_stage()` 或等价封装

---

### 问题7：全局 `LatencyMonitor` 单例初始化不是线程安全的

`get_monitor()` 当前没有使用锁保护。

在多线程环境下，理论上可能出现多个线程同时发现 `_latency_monitor is None`，从而重复创建实例。虽然概率不一定高，但属于应修复的基础问题。

---

## 需要澄清的点

### `tts_one_sentence()` 不是“完全缺失监控”

上一版文档把“单句 TTS 耗时缺失”写成了明确缺陷，这个表述不准确。

实际情况是：

- `tts_one_sentence()` 本身只是把文本拆段放入队列
- 真正的 TTS 合成发生在 `to_tts_stream()` / `to_tts()` 内
- 这两个实际合成入口已经有 `start_timer("TTS合成")` / `end_timer("TTS合成")`

因此本次不应把 `tts_one_sentence()` 单独列为“缺失监控”，否则会误导实现方向。

---

### 流式 ASR 不是“完全没有监控”，而是监控口径不足

部分流式 ASR Provider 最终仍会走到 `ASRProviderBase.handle_voice_stop()`，因此并不是完全没有 `ASR处理` 计时。

真实问题在于：

- 目前只能得到一个偏“最终收尾”的总耗时
- 无法区分首字延迟、最终结果延迟、流式中间结果节奏

所以本次文档将其修正为“监控口径不完整”，而不是“完全缺失”。

---

## 新需求：`turn_id` 改为“首条用户文本 + 时间”

### 目标

让同一轮对话的日志更可读，同时保证：

- 同一轮链路共用同一个稳定 `turn_id`
- `turn_id` 可以肉眼识别对应的用户问题
- 不因后续 `sentence_id` 变化而拆裂

### 生成规则

建议统一格式：

```text
{normalized_user_text[:24]}_{YYYYMMDD-HHMMSS-fff}
```

示例：

```text
今天天气怎么样_20260328-221530-123
查询血糖结果_20260328-221812-447
```

### 文本规范化要求

为避免 turn id 过长、换行污染日志、包含异常字符，生成前需要做规范化：

- 去掉首尾空白
- 将换行和连续空白折叠为单个空格
- 截断到固定长度，建议 `24` 个字符
- 对明显不适合出现在标识符中的字符做替换或清洗
- 若最终为空，则回退为 `voice_input`

### 生命周期要求

- 该 turn id 只在“本轮用户输入开始”时生成一次
- 后续意图识别、前置路由、LLM、TTS、工具调用都必须复用它
- 不再允许由 `sentence_id` 反向决定 `turn_id`
- `sentence_id` 仍可保留给 TTS/音频流控使用，但不能再承担对话轮次主键职责

### 连锁影响

引入文本型 turn id 需要注意两个问题：

- 日志隐私：不能直接放完整用户原文，只能使用清洗后的固定长度截断值
- 可读性与唯一性平衡：文本前缀可能重复，因此必须拼接高精度时间戳

---

## 修复方案

### 1. 重构 `latency_trace.py`

核心改动：

- 为 `mark_stage()` 增加显式 `module` 参数，允许调用方直接传入
- 对未显式传入 `module` 的情况，按增强后的规则自动推断
- 增加 `start_stage()` / `end_stage()` 封装，专门用于区间耗时
- `begin_turn()` 改为只负责创建并绑定当前轮的稳定 `turn_id`
- 新增 `build_turn_id(user_text, now)` 能力，统一按“首条文本截断 + 时间”生成

### 2. 统一 turn id 生命周期

核心原则：

- turn id 只在该轮第一条用户输入进入主链路时生成
- 后续所有模块统一从连接上下文读取同一个 `turn_id`
- `sentence_id` 与 `turn_id` 解耦

建议连接对象新增明确字段，例如：

- `conn.turn_id`
- `conn.turn_text_preview`

并修改相关调用链，避免再通过 `sentence_id` 推断 turn。

### 3. 增强 `_parse_module()` 分类规则

增加以下关键词映射：

```text
intent / 意图 / detect             -> 意图识别
memory / 记忆 / query_memory       -> 记忆查询
prefilter / 前置 / route           -> 前置路由
chat / 对话 / start_to_chat        -> 对话流程
vad / VAD / voice                  -> 语音检测(VAD)
listen / detect.text_ready         -> 输入接收
```

### 4. 修复 `end_timer()` 栈匹配问题

不再直接 `pop()` 后校验。

应改为：

- 从栈顶向下搜索匹配的 `stage`
- 找到后再删除对应项
- 未找到时记录警告日志，而不是破坏已有栈状态

### 5. 补充关键链路的独立计时

#### `intentHandler.py`

- 为 `analyze_intent_with_llm()` 增加意图识别计时

#### `connection.py`

- 为 `memory.query_memory()` 增加记忆查询计时

#### `receiveAudioHandle.py` / `prefilterHandler.py`

- 为 `try_prefilter_route()` 增加前置路由区间计时

#### `asr/*_stream.py`

- 保留现有 `ASR处理` 计时
- 视本次范围决定是否进一步拆分：
  - 首字延迟
  - 最终结果延迟

### 6. 明确瞬时打点与区间计时的职责边界

规范如下：

- `mark_stage()`：只记录阶段发生时刻
- `start_stage()` / `end_stage()`：只用于真实耗时

避免后续继续把零耗时事件当成真实耗时统计来源。

### 7. 线程安全初始化

使用 `threading.Lock` 或双重检查锁保护全局监控实例初始化。

---

## 修改文件清单

### [MODIFY] `main/xiaozhi-server/core/utils/latency_monitor.py`

- 增强 `_parse_module()` 分类规则
- 修复 `end_timer()` 栈匹配问题
- 线程安全单例初始化

### [MODIFY] `main/xiaozhi-server/core/utils/latency_trace.py`

- 新增 turn id 生成与绑定逻辑
- turn id 改为“首条文本截断 + 时间”
- `mark_stage()` 支持显式模块或自动推断
- 添加 `start_stage()` / `end_stage()`

### [MODIFY] `main/xiaozhi-server/core/connection.py`

- 明确区分 `turn_id` 与 `sentence_id`
- 为 `memory.query_memory()` 增加独立计时
- 主链路改为统一复用当前轮 `turn_id`

### [MODIFY] `main/xiaozhi-server/core/handle/receiveAudioHandle.py`

- 接入新的 turn id 生命周期
- 使用增强后的 trace API
- 补充前置路由区间计时

### [MODIFY] `main/xiaozhi-server/core/handle/textHandler/listenMessageHandler.py`

- 在文本首条输入时创建该轮 `turn_id`
- 使用增强后的 trace API

### [MODIFY] `main/xiaozhi-server/core/handle/intentHandler.py`

- 添加意图识别耗时监控
- 避免意图链路中错误重置 turn 关联

### [MODIFY] `main/xiaozhi-server/core/handle/prefilterHandler.py`

- 前置路由链路复用当前轮 `turn_id`
- 避免工具回路打断同轮归属

### [MODIFY] `main/xiaozhi-server/core/providers/asr/*_stream.py`

- 评估是否补充流式 ASR 分阶段监控
- 至少保证复用统一 turn id

---

## Open Questions

> [!IMPORTANT]
> 1. 流式 ASR 是否本次一并支持“首字延迟 / 最终结果延迟”拆分？如果不做，至少要先保证现有总耗时与 turn 归属正确。
> 2. `turn_id` 中的文本截断长度是否固定为 `24` 个字符？如果日志量较大，也可以调整为 `16`。
> 3. 是否需要在 `latency_realtime.log` 中增加更直观的单轮时间线展示？

---

## 验证计划

### 自动验证

- 检查修改文件是否通过语法校验，例如 `python -m py_compile`
- 搜索确认 `latency_trace.py` 中不再存在硬编码 `module="其他"`
- 搜索确认 turn 相关逻辑不再依赖 `sentence_id` 推断当前轮 id
- 针对 `build_turn_id()` 增加单元测试，覆盖：
  - 普通中文文本
  - 空文本
  - 包含换行和多空格
  - 超长文本截断
  - 特殊字符清洗

### 手动验证

- 发起一次文本对话，检查同一轮的阶段打点、LLM、TTS、工具调用是否共享同一个 turn id
- 发起一次语音对话，检查 `turn_id` 是否形如“文本截断 + 时间”
- 检查 `tmp/latency_realtime.log` 中各阶段模块分类是否正确
- 检查 `tmp/latency_summary.md` 中是否不再出现大量无意义的“其他”
- 验证在意图命中、前置路由命中、普通聊天三条路径下，turn 汇总均保持完整
