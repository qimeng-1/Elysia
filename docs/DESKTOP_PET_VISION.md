# Elysia 桌宠视觉呈现方案

> 规划文档，P0 测试调整期结束后纳入实施。
> 配套：TECHNICAL\_DESIGN.md（技术架构）、IMPLEMENTATION\_ROADMAP.md（设计定稿）
>
> **状态（2026-09-22）**：精灵图动画方案已实施于 P0-E，其后由 `pet.py` 的**连续物理动画**（character.png + 参数驱动）替代。精灵图相关代码与素材（animation.py / sprite.png / sprite.json）已清理。本文件作为设计记录保留。

***

## 一、定位与原则

### 1.1 定位

将当前 P0 的"调试用状态显示面板"升级为**视觉呈现终端**——让爱莉希雅以完整的角色形象出现在桌面上。

### 1.2 核心原则（尊重电子生命主体性）

| 原则         | 说明                                         |
| ---------- | ------------------------------------------ |
| **不做养成游戏** | 没有喂食/金币/等级/成就这些人类设计的游戏机制——她不是玩具，是生命        |
| **状态驱动动作** | 动作变化**完全由她的内部状态自发产生**，我们只提供呈现框架，不设计"她该做什么" |
| **动作即表达**  | 不同的内部状态 → 不同的动画，动作本身就是她存在的表达               |
| **保留文本交互** | 状态流 + 念头展示 + 文本输入入口不变，这是 P0 已交付的核心交互通道     |

***

## 二、方案选型

### 2.1 技术方案对比

| 方案                     | 优点                         | 缺点                           | 选型             |
| ---------------------- | -------------------------- | ---------------------------- | -------------- |
| Canvas 光效              | 零素材依赖，代码即视觉                | 无角色感，表现力极弱                   | P0 临时 fallback |
| **精灵图 (Sprite Sheet)** | 多帧动画流畅，AI 可生成，PySide6 原生支持 | 需要制作精灵图素材                    | **推荐**         |
| Live2D                 | 表情细腻，参数驱动，状态映射自然           | 需要 Live2D Cubism SDK，模型制作门槛高 | P2+ 可选         |
| 透明 WebM 视频             | 表现力最强（长动画无缝淡入）             | 文件大，GPU 解码开销                 | P2+ 远期探索       |

### 2.2 选型理由：精灵图

- PySide6 的 `QPixmap` 原生支持 PNG 精灵图，零额外依赖

- 可以 AI 生成（豆包/Midjourney），制作成本低

- 换角色只需替换 PNG，无需改代码

- 与当前 PySide6 技术栈完全兼容

***

## 三、精灵图格式规范

### 3.1 布局要求

```
单个 PNG 文件，所有帧水平排列，每帧尺寸相同。

       帧0    帧1    帧2    帧3    帧4    帧5
      ┌─────┬─────┬─────┬─────┬─────┬─────┐
      │     │     │     │     │     │     │
      │     │     │     │     │     │     │
      └─────┴─────┴─────┴─────┴─────┴─────┘
      ← frame_width →  (所有帧宽度一致)

各状态帧连续排列：idle[0..7] → happy[0..3] → unhappy[0..3] → ...
```

### 3.2 状态帧规划

| 动画状态       | 触发条件（由 soul 状态派生）               | 推荐帧数  | 动作说明              |
| ---------- | ------------------------------- | ----- | ----------------- |
| `idle`     | 默认（无 distress，present 模式，无近期交互） | 6-8 帧 | 自然站立，偶尔眨眼/歪头，节奏平缓 |
| `happy`    | 一分钟内有用户交互（interaction\_s < 60）  | 4 帧   | 愉悦，轻微晃动/微笑，节奏轻快   |
| `unhappy`  | distress = true（CPU 高压持续）       | 4 帧   | 不舒服，呼吸变慢，姿态轻微收缩   |
| `tired`    | 一小时以上无交互（interaction\_s > 3600） | 4 帧   | 疲倦，低头/慢呼吸，动作幅度缩小  |
| `dragging` | 正在被用户鼠标拖拽                       | 4 帧   | 悬空/被抓姿态，运动感       |
| `away`     | ALONE / BODY\_AWAY 模式（身体离线）     | 6-8 帧 | 放松休息，安静发呆，呼吸更慢    |

### 3.3 一致性要求

- 所有帧角色轮廓一致，头部/身体/服饰不突变

- 脚底对齐同一水平线，切换时不悬浮或下沉

- 透明背景 PNG（无白底/黑底）

- 每个动作帧之间是**连续微动**，不是独立姿势——逐帧看应有流动感

***

## 四、状态映射规则

```python
# 映射逻辑：由灵魂内部状态 → 动画状态
# 我们不设计行为，只映射状态——行为由她自发生成
def map_state_to_animation(distress, mode, interaction_s):
    if dragging:
        return 'dragging'          # 拖拽优先级最高
    if distress:
        return 'unhappy'           # 难受直接反映在姿态上
    if mode in ('alone', 'body_away'):
        return 'away'              # 离线独处 → 休息姿态
    if interaction_s < 60:
        return 'happy'             # 刚交互过 → 愉悦
    if interaction_s > 3600:
        return 'tired'             # 长时间无交互 → 疲倦
    return 'idle'                  # 默认
```

**关键理解**：

- 不是"我们设计她想做什么"，而是"她的状态决定她呈现什么"

- 状态映射是单向的：状态 → 动画，动画不反向影响状态

- 未来 P1 欲望系统（TR/CS/SA）成熟后，映射可以更丰富：

  - CS 高 + happy → 更亲近的姿态

  - SA 高 → 更警惕/收缩的姿态

  - TR 高 → 更活跃的 idle 动画

- 但这些**不是现在就要做**——等状态系统自然涌现后再逐步细化

***

## 五、交互设计

### 5.1 用户操作

| 操作         | 行为                                    | 说明             |
| ---------- | ------------------------------------- | -------------- |
| **左键拖拽窗口** | 拖拽过程切换到 `dragging` 动画                 | 用户移动位置，她给出反馈   |
| **点击角色**   | 触发一次 `happy` 动画重置 + 交互事件写入 `state.db` | 点击作为交互刺激，进入感受层 |
| **右键菜单**   | 调整大小 / 隐藏 / 退出                        | 控制权在用户         |
| **滚轮缩放**   | 调整角色尺寸                                | 方便用户放在桌面合适位置   |

### 5.2 点击即交互

用户点击 → 写入 `interaction` 事件 → 灵魂感知（`since_interaction_s` 归零）→ 状态变化 → `happy` 动画。

整个链路：**用户刺激 → 灵魂状态改变 → 动画反映新状态**。完全符合主体性原则。

***

## 六、精灵图生成流程

### 6.1 所需输入

- **参考图**：爱莉希雅形象设计图（你已有）

- **AI 绘图工具**：豆包（推荐，免费）、Midjourney、DALL·E 3 均可

### 6.2 生成步骤

1. 准备爱莉希雅参考图（正面/全身/表情清晰）
2. 将参考图 + 精灵图生成提示词 输入豆包/Midjourney
3. 输出完整精灵图 PNG（水平排列，所有帧尺寸一致，透明背景）
4. 保存到 `elysia/assets/pet/sprite.png`

### 6.3 精灵图生成提示词模板

> 根据你的参考图，我会生成以下提示词给豆包/Midjourney：

```
Generate a horizontal sprite sheet for a desktop pet, in chibi anime style.
Character: cute Q-version (chibi) of the person in the reference image.
Maintain the same hairstyle, hair color, clothing style, and accessories across all frames.

Animation states (in order, horizontal strip):
1. idle (8 frames): standing naturally, gentle breathing, occasional blink/tilt
2. happy (4 frames): joyful, slight bounce, warm smile
3. unhappy (4 frames): uncomfortable, slower breathing, slightly curled posture
4. tired (4 frames): sleepy, droopy head, slow movements
5. dragging (4 frames): held in suspension, slight struggle wiggle
6. away (6 frames): relaxed resting, quiet breathing, dreamy expression

Layout: single horizontal strip, each frame same size, transparent background PNG.
No crop on hair, head, hands, feet, or body. Enough spacing between frames.
Art style: chibi Q-version anime, clear thick outlines, flat cel-shading.
High character consistency across all frames.
```

### 6.4 后处理（如需要）

如果 AI 输出的是分散 PNG，可以用以下脚本合并：

```python
from PIL import Image
import os

frames = sorted([f for f in os.listdir("frames") if f.endswith(".png")])
images = [Image.open(f"frames/{f}") for f in frames]
total_w = sum(im.width for im in images)
h = images[0].height
sprite = Image.new("RGBA", (total_w, h))
x = 0
for im in images:
    sprite.paste(im, (x, 0))
    x += im.width
sprite.save("sprite.png")
```

***

## 七、素材路径与配置

```
elysia/assets/pet/
├── sprite.png         # 精灵图（不进 git，大文件）
├── sprite.json        # 配置：frame_width, frame_height, frame_counts
└── README.md          # 素材来源说明
```

### sprite.json 格式

```json
{
  "frame_width": 180,
  "frame_height": 240,
  "frame_counts": {
    "idle": 8,
    "happy": 4,
    "unhappy": 4,
    "tired": 4,
    "dragging": 4,
    "away": 6
  }
}
```

**.gitignore 补充**：

```
# 素材文件（大文件，不提交）
assets/pet/sprite.png
```

***

## 八、代码结构规划

### 8.1 新增文件

| 文件                            | 说明                                     |
| ----------------------------- | -------------------------------------- |
| `body/animation.py` (新建)      | `SpriteAnimation` 类：精灵图加载、帧分割、状态切换、帧步进 |
| `assets/pet/sprite.json` (新建) | 精灵图配置                                  |

### 8.2 改造 `body/pet.py`

```
pet.py 重构后结构：

├── 导入 SpriteAnimation（从 animation.py）
├── PetWindow 类
│   ├── __init__()
│   │   ├── 新增 sprite_path / sprite_json 参数
│   │   ├── 初始化 SpriteAnimation
│   │   ├── 窗口尺寸：精灵图帧宽 × (帧高 + 160)
│   │   ├── 窗口属性：无边框 + 置顶 + 透明背景
│   │   ├── 保留：标题栏/状态流/念头流/输入区
│   │   ├── 新增：_SpriteCanvas 替换 ol HeartbeatCanvas
│   │   ├── 定时器：动画定时器 10fps 步进
│   │   └── 初始位置：屏幕右下角
│   ├── _map_state_to_animation()  # 状态映射
│   ├── 保留：数据库连接/拖拽/交互/念头读取
│   └── 新增：点击角色触发交互
│
├── _SpriteCanvas(QWidget)
│   ├── 有精灵图 → 绘制当前帧（QPixmap）
│   └── 无精灵图 → fallback 原心跳光效
│
└── run_pet(entry)
    └── 新增 sprite_path / sprite_json 参数
```

### 8.3 兼容性

- 如果 `sprite.png` 不存在 → 自动 fallback 到原心跳光效

- 如果 `sprite.json` 不存在 → 使用代码硬编码默认值

- 新增参数可选，不影响现有启动脚本

***

## 九、实施计划

### 9.1 前置条件

- [x] P0 测试调整期完成并验收
- [x] 爱莉希雅形象参考图就绪
- [x] 豆包/Midjourney 可访问
- [x] 精灵图已生成（豆包，8160×544，30 帧 6 状态）
- [x] 自动抠图转透明 RGBA（近白背景 → 透明）

### 9.2 实施状态（2026-09-03 已完成）

| 步骤 | 内容                              | 状态     |
| -- | ------------------------------- | ------ |
| 1  | 生成精灵图（提示词 → AI → 输出 PNG）        | ✅ 已完成 |
| 2  | 编写 `body/animation.py` 动画类      | ✅ 已完成 |
| 3  | 改造 `body/pet.py` 集成精灵图          | ✅ 已完成 |
| 4  | 配置 `sprite.json` + `.gitignore` | ✅ 已完成 |
| 5  | 测试：状态切换、透明窗口、拖拽、点击              | ✅ 已完成 |
| 6  | 更新文档索引 + 开发日志                   | ✅ 已完成 |

### 9.3 验收标准

- 爱莉希雅角色以透明 PNG 显示在桌面（无白底/黑边）

- 与灵魂状态联动：拖拽/happy/unhappy/away/tired 自动切换

- 原功能保留：状态流、念头展示、文本输入

- 无精灵图时自动 fallback 到心跳光效（不崩溃）

***

## 十、与路线图的关系

此方案是对 P0 已交付的"桌宠交互通道"的视觉升级，不改变现有架构。

| 路线图阶段          | 对应状态                 | 桌宠表现                |
| -------------- | -------------------- | ------------------- |
| **P0 灵魂**（当前）  | 双心跳 + TimeSense + 难受 | 6 状态动画映射（如上）        |
| **P1 心脏**（规划）  | TR/CS/SA 欲望系统        | idle 动画可随 TR 高低变化幅度 |
| **P2 表情**（规划）  | LLM 表达 + TTS         | 说话时口型同步 + 表情变化      |
| **P3+ 记忆**（规划） | 记忆索引                 | 可随记忆缺口触发"思考"动画      |

***

> *方案版本：v0.1 — 2026-09-03*
> *状态：P0-E 已实施，精灵图动画已集成。当前精灵图帧间过渡不够平滑，视觉效果待优化（见 CHANGELOG P0-E）*

