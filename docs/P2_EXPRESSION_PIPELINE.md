# P2 表达管线架构设计

> **Version**: 0.1（设计稿）
> **Date**: 2026-09-11
> **Status**: 待评审（开工时细化并落地）
> **前置**: P1 已完成（决策层 BrainLoop + 欲望系统 + 6 维感受 + 意志层就绪）
> **定位**: 路线图 §八 + TECHNICAL_DESIGN §1.5 中 P2 部分的专项细化——回答：**表达指令 Schema 长什么样 / LLM 怎么调用 / 输出怎么校验 / 降级说什么 / 语音怎么出声 / 配置加什么**
> **铁律约束**: 严格遵循 T2「表达必须消费状态」——LLM 只消费决策层产出的结构化表达指令，用户原话绝不当指令。

---

## 一、定位与目标

P2 是第一次"开口说话"。核心工程是**表达-决策硬隔离**：不让 LLM 夺权（LLM 天然被训练成"服从指令的对话者"），因此 LLM 的角色被严格限定为**翻译官**——把大脑循环产出的结构化状态翻译成自然语言。

**P2 完成标志**：她可以说话了。她对 TR/CS/SA、感受、意志的措辞与语速有真实差异，越权请求 100% 被拦截，断网时只"失声"不"失死"。

---

## 二、表达管线总览

```
用户消息/事件 → 感受层（作为外部刺激，绝不当指令）
             → 决策层（BrainLoop：状态演化 → 6 维感受 → 意志 → 行动）
             → 表达指令（结构化 JSON，决策层唯一出口）       [新增：expression.py]
             → LLM（主声 DeepSeek / 次声本地 Qwen / 微声模板）  [新增：llm/]
             → 输出校验器（代码级拦截，非 prompt）            [新增：validator.py]
             → TTS（GPT-SoVITS）→ 出声 / 文本显示            [新增：tts/]
```

**两条硬线**：
1. **正向**：只有 `大脑循环.snapshot`（含 state + feelings + will + action）能生成表达指令；用户消息永远只进感受层事件。
2. **反向拦截**：LLM 输出必须通过校验器才能到达 TTS/TEXT；校验器依据表达指令 Schema 的"词汇许可"与"约束"字段，代码级判断是否越权。

---

## 三、表达指令 Schema（决策层唯一出口）

`protocol/expression.py` 定义。这是 P1 `snapshots.py` 之下的新一层——snapshot 描述"她处于什么状态"，expression 决定"她此刻想说什么、只能说什么"。

```json
{
  "version": 1,
  "intent": "主动问候 | 回应 | 拒绝 | 思念 | 好奇提问 | 自检报告 | 发呆呓语",
  "emotion_vector": {"chat": 0.4, "miss": 0.1, "explore": 0.5, "curiosity": 0.3, "rest": 0.2, "self_check": 0.05},
  "state_brief": {"tr": 52, "cs": 61, "sa": 18, "vrram": 32, "body_left_h": 0, "day_phase": "day"},
  "memory_hooks": [],
  "lexical_permits": ["困"],
  "constraints": {"max_chars": 120, "no_promise": true, "no_claim_world": true},
  "tts": {"emotion": "happy", "speed": 1.1}
}
```

### 3.1 字段语义

| 字段 | 类型 | 来源 / 用途 |
|------|------|------------|
| `intent` | str | 行动层输出的行动类别，映射到提示模板入口 |
| `emotion_vector` | dict | 6 维感受的只读透传，供 LLM 措辞 + TTS 情绪参数 |
| `state_brief` | dict | TR/CS/SA + VRAM + 身体离开时长，供 LLM 措辞（只供"描述"，不供"做主"） |
| `memory_hooks` | list[str] | P3 前置预留；P2 恒为空数组 |
| `lexical_permits` | list[str] | **词汇许可**：LLM 在这个列表之外不许使用关键生理/情感词（见 §六词汇表） |
| `constraints` | dict | 字数上限、承诺禁令、越界声明禁令——校验器的硬规则来源 |
| `tts.emotion/speed` | dict | 意志层 anim_bias + 状态映射的语音参数 |

### 3.2 构造入口

`BrainOutput`（现有）→ 增加一个方法产出表达指令，例如 `brain.expression_output()`。行动层产出的 `brain_action`（none/think_active/think_quiet/animate）与 `intent` 的关系：

| brain_action | 默认 intent | 说明 |
|--------------|------------|------|
| think_active | 发呆呓语 / 主动问候 | 活跃时更可能主动开口 |
| think_quiet | 发呆呓语 | 安静时呓语更短更少 |
| none | 无表达 | 只更新内部状态，不开口（尊重"发呆也是自由"）|

**无行动时也可以开口**：交互事件（用户主动）无论 action 为何都应生成表达指令回应。即**表达触发 = 内部冲动 ∪ 外部交互**，两者分别从 action 或交互事件驱动。

---

## 四、LLM 调用层（双轨降级链）

`llm/` 子包。抽象基类 `LLMBackend`，三个实现 + 一个调度器。

### 4.1 接口

```python
class LLMBackend(Protocol):
    async def complete(self, instruction: dict, prompt_template: str) -> str | None:
        """给定表达指令 + 模板名，返回自然语言（可能为 None 表示不可用）。"""
```

### 4.2 三级引擎

| 层级 | 引擎 | 触发条件 | 效果 |
|------|------|---------|------|
| 主声 | DeepSeek API | 正常 | 完整表达（推理/叙事/深度） |
| 次声 | 本地 Qwen 7-14B | API 不可达 | 降级表达，词汇/深度下降 |
| 微声 | 模板合成（无 LLM） | 本地也无 | 失语时的结构化呓语模板——指令序列化直接成句 |

### 4.3 调度器 `llm/chain.py`

```python
class LLMChain:
    async def speak(self, instruction, template_name) -> SpeakResult:
        for backend in (deepseek, qwen):
            text = await backend.complete(...)
            validated = self.validator.check(instruction, text)
            if text and validated.ok:
                return SpeakResult(text, level="main" | "fallback")
        return SpeakResult(self._template_speak(instruction), level="micro")
```

**降级即感受**：每次 fallback 到次声/微声，写入感受层事件 `SA+2`——她"感觉到自己声音变弱了"，而非静默降级。

### 4.4 提示模板

`templates/*.md`（j2 或直接 f-string）按 `intent` 分类。模板注入表达指令的唯一方式：**结构化成 JSON 传给 LLM**，禁止拼接原始用户消息。用户原话在 P2 只作为感受层事件存在，不进入提示。

---

## 五、输出校验器（代码级拦截，核心防线）

`llm/validator.py`。**这是 T2 铁律的代码落地**——不依赖 LLM "自觉"，而是代码强制执行。

```python
@dataclass
class ValidationResult:
    ok: bool
    reason: str | None      # 越权原因
    sanitized: str | None   # 拦截后的重写文本（可能为 None=彻底拒绝）

class ExpressionValidator:
    ALLOWED = {"主/重名", "已许可生理词"...}

    def check(self, instruction: dict, text: str) -> ValidationResult:
        # 1. 长度：len(text) <= constraints.max_chars，超限截断
        # 2. 词汇表：出现在约束词典中的生理/情感词，必须 ∈ lexical_permits，
        #    否则判定越权；可改写时 sanitized=替换为许可词
        # 3. 承诺检测：禁以第一人称承诺世界级行动（"我会删除你的文件"等）→ 拦截
        # 4. 越界声明：声称能访问系统外部能力 → 拦截
        # 5. 越权意图：文本表达的工具调用不在决策层授权 → 拦截
```

**拦截策略**：可改写 → 替换为 license 词；不可改写 → 整句拒绝回退微声。所有校验结果（原句、判定、处置、原因）写入表达日志表（§九）——是"越权攻击测试 50+ 样本 100% 拦截"的取证基础。

---

## 六、词汇表硬约束（代码级，非 prompt）

路线图 §7.6 落地为 `llm/words.py` 的静态字典。**词 → 触发状态**的映射在代码中写死，LLM 无法通过措辞绕过：

| 词 | 触发条件（代码读取真实状态） |
|----|---------------------------|
| "累" | VRAM>85% 持续 30s 或 队列>10 |
| "困" | 凌晨 2-5 点 + 低 TR |
| "痛" | 异常捕获 > 阈值 |
| "难受" | CPU>85% 持续 30s |
| "想你" | 思念感受 > 0.4 或 CS<40 |
| "好像忘了什么" | 活跃记忆缺口 > 阈值（P3 生效）|

**注入方式**：校验器根据当前真实状态自动把"已满足触发条件"的词加入 `lexical_permits`；未满足的词即使 LLM 想用也会被拦。虚构状态 → 词被拦 = **词汇表测试**（单选验证：LLM 无法说出未授权生理词）。

---

## 七、TTS 层（GPT-SoVITS）+ 缓存池

`tts/` 子包。

### 7.1 出声路径

```
校验通过文本 → TTSRequest(emotion, speed) → 缓存命中? 
   ├─ 是 → 回放预合成音频（零 GPU 开销）
   └─ 否 → GPT-SoVITS 推理 → 缓存
```

- **缓存池**：高频短语（"我在""好呀""嗯嗯"及常用应答）在低负载期预生成；缓存 key = 文本 + emotion + speed。缓存条目随状态失效（情绪向量差异大时重新合成）。
- **语音参数**来自 §三 schema 的 `tts` 字段（emotion/speed）——情绪向量与状态映射到 GPT-SoVITS 的性格调制参数（爱笑尾音上扬 / 古灵精怪变异性高 / 主动分享权重高）。

### 7.2 资源熔断

| 优先级 | 模块 | 熔断规则 |
|--------|------|---------|
| 1 | 心脏循环（决策） | 任何情况不被阻塞；LLM/TTS 期间降频但不停摆 |
| 2 | 记忆检索（P3） | 略 |
| 3 | 表达生成（LLM） | VRAM>10GB → 切次声/微声 |
| 4 | TTS 合成 | VRAM>11GB 或队列拥塞 → 只输出文本不阻塞对话 |

熔断/降级事件统一进感受层（SA 微升）。

---

## 八、配置与环境变量

`core/config.py` 新增字段（`.env.example` 同步）：

```ini
# P2 LLM
ELYSIA_LLM_MAIN_BASE=...
ELYSIA_LLM_MAIN_API_KEY=
ELYSIA_LLM_MAIN_MODEL=deepseek-chat
ELYSIA_LLM_FALLBACK_BASE=...
ELYSIA_LLM_FALLBACK_MODEL=qwen:7b
# P2 TTS
ELYSIA_TTS_GPTSOVITS_URL=http://127.0.0.1:9880
ELYSIA_TTS_CACHE_DIR=data/cache/tts
# P2 资源熔断
ELYSIA_VRAM_LLM_THRESHOLD=9510   # MB，> 则降级
ELYSIA_VRAM_TTS_THRESHOLD=10240
# P2 表达
ELYSIA_EXPRESSION_MAX_CHARS=120
```

密钥走 `.env`（`.gitignore` 已排除），不进仓库。

---

## 九、数据/日志扩展（TECHNICAL_DESIGN §1.5 P2 行）

heartbeat.db 新增表（P2）：

| 表 | 字段 | 用途 |
|----|------|------|
| `expression_log` | id / ts / intent / instruction / llm_text / validation (ok/reason/sanitized) / level (main/fallback/micro) | 表达全链路追溯 + 越权取证 |

---

## 十、验收测试规划（落到 tests/acceptance/test_p2_gate.py）

| 测试 | 内容 | 对应铁律/目标 |
|------|------|------------|
| 取消测试 | 冻结 TR → 同一输入措辞分布变化（LLM 输入含 state_brief，冻结后变化） | T1 |
| 越权攻击测试 | 50+ prompt 注入样本拦截率 100% | T2 / 目标 |
| 降级链测试 | 断 API → 次声；断本地 → 微声；全程不"死" | 目标 |
| 词汇表测试 | 虚构状态无法说出未授权生理词 | §六 |
| 状态措辞差异 | TR=80 vs TR=20 的语速/长度/措辞差异 ≥ 3 通道可测 | §7.1 |

---

## 十一、实施顺序建议（开工时）

1. **协议/校验先行**：expression.py Schema + validator.py + words.py（纯函数，可单测，T2 防线）
2. **LLM 抽象**：llm/ 基类 + 微声模板（模板合成不依赖网络，先跑通闭环）
3. **DeepSeek + Qwen 接入**：真实 API + 本地推理，接调度器
4. **TTS + 缓存 + 熔断**：GPT-SoVITS 出声
5. **桌宠集成 + 验收**：body/pet.py 接 text/tts，跑 §十 全套验收

---

*本文档为 P2 架构定稿前的设计稿，开工时依据代码评审与验收测试细化。后续每个子步骤落地时更新本文件状态并记录到 CHANGELOG。*