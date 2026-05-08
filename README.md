# VM Auto Test

面向本地 VMware Workstation 实验环境的自动化验证框架——回滚快照 → 执行样本 → 采集结果 → 生成报告，串成稳定可重复的测试流程。

**只做自动化执行和结果比对，不生成样本、不提供绕过能力、不尝试规避检测。**

## 环境要求

- Windows 宿主机 + VMware Workstation Pro 17+
- Python 3.10+
- Guest 已安装 VMware Tools
- `vmrun.exe` 可用（默认路径 `C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe`）
- **VM 不能开启访问控制加密**（否则 vmrun 无法列出快照，会超时报错）

如果遇到"无法列出快照"或查询超时，检查 VM 是否启用了加密：
VMware Workstation → 虚拟机设置 → 选项 → 访问控制 → 移除加密。

## 安装

```bash
pip install -e .
```

开发依赖（跑测试用）：

```bash
pip install -e .[dev]
```

## 快速开始

```bash
copy .env.example .env
```

编辑 `.env`，填入你的 vmrun 路径：

```bash
VMRUN_PATH=D:\VM2\vmrun.exe
```

然后启动：

```bash
vm-auto-test
```

首次运行会进入主菜单：

```
  —— VM Auto Test ——
  [0] 退出
  [1] 测试单样本
  [2] 测试多样本 (CSV)
  [3] 列出 VM
  [4] 列出快照
  [5] 重新配置环境
```

全程逐步引导，不需要记参数。

### 配置 Guest 凭证

不再在 `.env` 中配置全局用户名密码。每个 VM 的凭证独立管理：

1. 主菜单选 **[3] 列出 VM**
2. 选择要配置的 VM
3. 选 `[1] 配置凭证`，输入该 VM 的 Guest 用户名密码
4. 保存后自动验证，成功即完成

凭证保存在 `credentials.json`（按 VM 文件名匹配）：

```json
{
  "Windows 11 x64": {
    "user": "19657",
    "password": "admin123"
  }
}
```

已配置的 VM 在列表中会标注 `[已配置]`。也可以 `[1] 验证凭证` 测试现有配置是否有效，或 `[2] 重新配置` 修改。

### CSV 表格格式（多样本测试）

Excel 编辑，4 列，保存为 **CSV UTF-8**：

| 列 | 含义 | 示例 |
|---|---|---|
| `sample_file` | 样本文件名 | `1.exe` |
| `shell` | 执行用 `cmd` 还是 `powershell` | `cmd` |
| `verify_command` | 验证命令（样本前后各跑一次） | `hostname` |
| `verify_shell` | 验证命令的 shell | `cmd` |

文件名即可，交互菜单会问"VM 上样本目录"，框架自动拼接完整路径。写绝对路径也可以，目录留空就行。

### 结果怎么看

| 输出 | 含义 |
|---|---|
| 有效 / `BASELINE_VALID` | 样本跑完后验证输出变了，样本有效 |
| 无效 / `BASELINE_INVALID` | 验证输出无变化，样本无效 |
| 未拦截 / `AV_NOT_BLOCKED` | 杀软没拦住，攻击效果发生 |
| 已拦截 / `AV_BLOCKED_OR_NO_CHANGE` | 杀软拦截或未生效 |

报告在 `reports/时间戳-样本名/` 下，每个样本一个子目录。

## 两种测试模式

| 模式 | 快照环境 | 作用 |
|---|---|---|
| `baseline` | 干净系统（无杀软） | 先确认样本本身有效（打了有反应） |
| `av` | 装了杀软的系统 | 再看杀软能不能拦截 |

AV 模式必须先有一份已通过的 baseline 报告。

## 报告目录

```
reports/
└── 20260509-023223-336747-1-A_schtasks/
    ├── result.json          # 汇总
    ├── before.txt           # 样本执行前验证命令输出
    ├── after.txt            # 样本执行后验证命令输出
    ├── sample_stdout.txt    # 样本 stdout
    └── sample_stderr.txt    # 样本 stderr
```

## 命令行参考

如果不想用交互菜单，也可以直接传参：

| 命令 | 用途 |
|---|---|
| `vm-auto-test` | 交互菜单 |
| `vm-auto-test run --vm ... --sample ... --verify ...` | 单样本测试 |
| `vm-auto-test run-csv --csv ...` | CSV 批量测试 |
| `vm-auto-test run-dir --dir ...` | 扫描目录批量测试 |
| `vm-auto-test vms` | 列出运行中的 VM |
| `vm-auto-test snapshots --vm ...` | 列出快照 |

## 测试流程

每次单样本测试按以下步骤执行：

| 步骤 | 说明 |
|------|------|
| `create report dir` | 创建报告目录 |
| `revert snapshot` | 回滚到指定快照 |
| `start vm` | 启动 VM |
| `wait guest ready` | 等待 VMware Tools 就绪 + 验证 Guest 凭证（5 次失败自动终止） |
| `before verification` | 执行验证命令，获取 baseline |
| `run sample` | 在 Guest 中执行测试样本 |
| `after verification` | 再次执行验证命令，获取结果 |
| `evaluate` | 比对前后输出，判定有效/无效 |
| `write report` | 生成报告文件 |

## 输出比较策略

不配置时默认用 `changed`（前后归一化后不同即为有效）。可以按需配置更精确的策略：

| 策略 | 作用 | 配置示例 |
|---|---|---|
| `changed` | 归一化后前后不同 | 默认，无需配置 |
| `contains` | 输出包含指定字符串 | `value: "created"` |
| `regex` | 输出匹配正则 | `pattern: "Error: \\d+"` |
| `json_field` | JSON 字段等于预期值 | `path: "result.status"`, `expected: "ok"` |
| `file_hash` | 输出 SHA-256 等于预期 | `expected: "abc123..."` |

## AV 日志采集

只执行你显式配置的命令，不内置厂商特定逻辑：

```yaml
av_logs:
  collectors:
    - id: app-events
      type: guest_command
      command: Get-WinEvent -LogName Application -MaxEvents 20
      shell: powershell
```

## 配置文件

### .env — 全局环境变量

```bash
# vmrun.exe 路径
VMRUN_PATH=D:\VM2\vmrun.exe

# VM 凭证文件路径（可选，默认 credentials.json）
VMWARE_CREDENTIALS_FILE=credentials.json

# vmrest/MCP 配置（可选）
VMWARE_HOST=localhost
VMWARE_PORT=8697
```

### credentials.json — 按 VM 配置凭证

```json
{
  "Windows 11 x64": {
    "user": "19657",
    "password": "admin123"
  },
  "Win10": {
    "user": "admin",
    "password": "pass456"
  }
}
```

Key 为 `.vmx` 文件名去掉路径和扩展名。可通过主菜单 [3] 交互式添加、验证、重新配置。

## Provider

| provider | 状态 |
|---|---|
| `vmrun` | 已实现，默认 |
| `vsphere` / `powercli` / `mcp` | 占位，待后续实现 |

## 测试

```bash
pytest
python -m compileall -q src tests
```

当前 88 个测试，使用 fake provider，不需要真实 VMware 环境。

### 真实 VMware smoke test

```bash
# .env 里配置 VMRUN_PATH 和 credentials.json 即可
vm-auto-test-smoke
```

只做无害链路检查：列快照、可选回滚、启动、等 VMware Tools、跑 `hostname`。

## 项目结构

```text
src/
├── vmware_mcp/              # 复用 VMware MCP / vmrun 封装
└── vm_auto_test/            # 自动化测试框架
    ├── av_logs.py           # AV 日志采集接口
    ├── cli.py               # 命令行入口 + 交互菜单
    ├── config.py            # YAML/CSV 配置解析
    ├── env.py               # .env 加载 + 凭证管理
    ├── evaluator.py         # 输出归一化和比较策略
    ├── models.py            # 数据模型
    ├── orchestrator.py      # 测试流程编排
    ├── reporting.py         # 报告生成
    ├── smoke.py             # 真实 VMware smoke test
    └── providers/
        ├── base.py          # Provider 抽象 + 异常定义
        ├── factory.py       # Provider 工厂
        └── vmrun_provider.py# VMware Workstation vmrun provider

tests/                       # fake provider 离线测试
```

## 安全边界

- 仅用于你拥有授权的本地虚拟机实验环境
- 使用隔离网络或 Host-only/NAT
- 每次测试前回滚到明确快照
- 不在生产主机或共享环境中运行未知样本
- Guest 密码写入 `credentials.json`，`.env` 和 `credentials.json` 都不要提交到 git
