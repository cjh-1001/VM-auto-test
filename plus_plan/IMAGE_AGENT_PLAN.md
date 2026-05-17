# AV Analyze 图片识别 Agent — 最小可行方案

## 目标

在 `av_analyze` 的现有双轨判定里，加入一个专门识别截图中“是不是杀软弹窗”的轻量 Agent，提升结果可信度。

核心要求：

1. **不大改当前框架**
2. **日志优先**，日志有变化时仍然直接判定拦截
3. **只有在“日志无变化 + 截图有差异”时**，才启用图片识别 Agent
4. **Agent 只做证据判断，不直接替代整套流程**

---

## 现有判定链

```
日志分析 ──→ 有变化 → BLOCKED
  │
  └── 无变化 → 像素对比
                  │
                  ├── 差异 < 阈值 → NOT_BLOCKED
                  │
                  └── 差异 ≥ 阈值 → 图片识别 Agent
                                      │
                                      ├── 明确是杀软弹窗 → BLOCKED
                                      └── 不是杀软弹窗 / 证据不足 → NOT_BLOCKED
```

---

## 这次要优化的点

### 1. 结果判定要更保守

不要让 Agent 直接输出“最终结论”，而是先输出结构化证据，再由 orchestrator 做最终合并：

- `has_popup`: 是否明确看到弹窗
- `popup_kind`: 弹窗类型
- `popup_text`: 弹窗里的关键文本
- `confidence`: 置信度
- `reason`: 为什么这么判断

### 2. 不增加太多配置面

MVP 只加一个开关：

- `image_popup_agent_enabled: bool = false`

先不要加模型配置、阈值配置、供应商配置，避免把第一版做重。

### 3. 先做“能跑”的最小版本

第一版先做到：

- 能调用图片识别
- 能返回 JSON 结果
- 能和现有 `image_compare_result` 合并
- 能区分“杀软弹窗”与“普通窗口/通知/桌面变化”

---

## 建议的最终规则

### 规则 A：日志优先

- 日志有变化 → **直接 BLOCKED**
- 这条不改

### 规则 B：图片差异小，不进 Agent

- 像素差异低于阈值 → **NOT_BLOCKED**
- 这条不改

### 规则 C：图片差异大，才进 Agent

- 只有当日志无变化且图片差异大时，才调用 Agent
- Agent 如果明确识别到杀软弹窗 → **BLOCKED**
- 如果识别为普通窗口、系统通知、桌面变化，或置信度不够 → **NOT_BLOCKED**

### 规则 D：保守兜底

- Agent 超时、返回格式错误、图片打不开、识别不明确 → **NOT_BLOCKED**
- 同时把原因写进 `screenshot_analysis`，便于人工复核

这样做的目的是：**宁可少判拦截，也不要把普通窗口误判成杀软弹窗**。

---

## 推荐的 Agent 接口

新增一个独立模块：`src/vm_auto_test/image_agent.py`

### 函数

```python
async def analyze_popup_screenshots(
    before_path: Path,
    after_path: Path,
    diff_detail: str,
    api_key: str,
) -> PopupAnalysis:
    ...
```

### 返回结构

```python
@dataclass(frozen=True)
class PopupAnalysis:
    has_popup: bool
    popup_kind: str        # av_alert / windows_defender / other / no_popup
    popup_text: str
    confidence: float
    reason: str
```

### 返回原则

- `has_popup=true`：必须能说明“看到的是弹窗”，而不是只是“画面变了”
- `confidence` 太低时，视为不可靠结果
- 不把“未知变化”硬说成弹窗

---

## Prompt 重点

Agent 的提示词要更严格，重点问三件事：

1. 变化是不是弹窗
2. 如果是弹窗，里面有没有明显杀软/威胁/拦截语义
3. 如果不是弹窗，变化更像什么

输出必须是 JSON，不能夹带长篇解释。

---

## 实现范围

### 最小改动文件

| 文件 | 改动 |
|------|------|
| `src/vm_auto_test/image_agent.py` | 新增图片识别 Agent |
| `src/vm_auto_test/orchestrator.py` | 在 `av_analyze` 的图片差异分支里接入 Agent |
| `src/vm_auto_test/config.py` | 加一个开关字段 |
| `src/vm_auto_test/models.py` | 如有需要，补一个轻量结果结构；能复用现有结构就不新增 |
| `tests/test_image_agent.py` | 新增单测 |

### 尽量不动的部分

- CLI 参数结构
- HTML 报告布局
- 现有日志分析流程
- 现有 `reporting.py` 的大结构

---

## 建议的技术路线

### 路线 1：先做独立 Agent，再接 orchestrator

这是推荐路线。

1. 先实现 `image_agent.py`
2. 用 mock 图片和 mock API 响应把 Agent 单测跑通
3. 再在 orchestrator 里接入

优点：风险小，容易验证。

### 路线 2：直接塞进现有 analysis 流程

不推荐作为第一版。

问题是会把截图分析、日志分析、图片弹窗识别揉在一起，后面不好维护，也不好调试误判。

---

## 启动方式

### 第一阶段：先做离线验证

先不要接主流程，先验证这个问题：

> 给定一对 before / after 截图，Agent 能不能稳定回答“这里是不是杀软弹窗”。

建议先准备 3 组样本：

- 杀软弹窗
- 普通程序窗口
- 桌面轻微变化

先把这 3 组喂给 Agent，看 JSON 输出是否稳定。

### 第二阶段：做最小模块

然后再做：

1. `image_agent.py`
2. 单元测试
3. orchestrator 接入开关

### 第三阶段：接入主流程

只在下面条件同时满足时调用：

- `av_analyze` 模式
- 日志无变化
- 截图差异超过阈值
- `image_popup_agent_enabled=true`

---

## 我建议的第一版起步顺序

1. **先定 JSON 输出格式**
2. **先做 `image_agent.py` + 单测**
3. **再接 orchestrator**
4. **最后再考虑要不要加配置项和更多模型支持**

---

## 后续优化点

第一版先别做，后面再加：

- 裁剪差异区域后再送 Agent
- 记录 Agent 的证据截图片段
- 低置信度结果单独标记
- 支持多模型
- 给不同杀软做样式库

---

## 这版方案的好处

- 结果更保守
- 误报更少
- 改动面小
- 便于先验证，再扩展

