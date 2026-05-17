# AV Analyze 图片识别 Agent — MVP 方案

## 目标

只在 `av_analyze` 里做最后一步确认：

- 日志有变化 → 直接判定拦截
- 日志无变化且截图差异明显 → 调用轻量 AI
- AI 只回答“这是不是杀软弹窗/拦截弹窗”
- 识别不明确 → 保守判定为 `NOT_BLOCKED`

## 非目标

- 不改现有 CLI 流程
- 不做独立图片识别 CLI
- 不引入多模型切换
- 不做截图裁剪、差异区域提取、样本库建设
- 不重构现有 `analysis.py` 的文本/截图分析流程

## 最小实现

### 1. 新增一个轻量识别入口

新增 `src/vm_auto_test/popup_classifier.py`：

- 输入：before/after 截图路径、差异摘要、可选提示词
- 输出：结构化结果
- 只负责“是不是弹窗、是不是杀软弹窗”

### 2. 在 `av_analyze` 图片分支接入

在 `src/vm_auto_test/orchestrator.py` 的图片差异分支里：

1. 先跑现有日志分析
2. 再跑现有像素差异判断
3. 只有当“日志无变化 + 截图差异明显”时，才调用图片识别
4. 识别结果明确是杀软弹窗 → `BLOCKED`
5. 其他情况 → `NOT_BLOCKED`

### 3. 仅在需要时加配置开关

如果必须控制开关，只加一个布尔字段：

- `image_popup_agent_enabled: bool = false`

不要先加模型名、温度、阈值组等额外配置。

## 文件范围

- `src/vm_auto_test/popup_classifier.py`：新增最小识别封装
- `src/vm_auto_test/orchestrator.py`：接入决策分支
- `src/vm_auto_test/config.py`：可选开关解析
- `src/vm_auto_test/models.py`：仅在现有结果结构不够用时补最小字段
- `tests/test_popup_classifier.py`：单测识别结果和兜底逻辑
- `tests/test_orchestrator.py`：覆盖“日志无变化 + 截图差异大”触发路径

## 推荐起步顺序

1. 先写 `popup_classifier.py` 的测试
2. 再实现最小识别封装
3. 再补 `orchestrator.py` 的触发测试
4. 最后接入主流程

## 主要风险

- AI 对普通窗口/系统通知误判成杀软弹窗
- 截图证据太多导致 token 浪费
- 兜底过于保守会把少数真实拦截判成未拦截

## 约束

- 优先自动化，AI 只做最后确认
- 结果不确定时，宁可漏报也不要误报
- 先保证能跑，再考虑优化提示词和证据裁剪

## 验收标准

- 日志有变化时不依赖 AI
- 日志无变化但截图差异明显时会触发图片识别
- AI 能区分“杀软弹窗”和“普通应用/系统弹窗”
- 不确定结果默认走 `NOT_BLOCKED`
