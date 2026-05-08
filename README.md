# VM Auto Test

面向本地授权 VMware Workstation 实验环境的自动化验证框架——回滚快照 → 执行样本 → 采集结果 → 生成报告，串成稳定可重复的测试流程。

**只做自动化执行和结果比对，不生成样本、不提供绕过能力、不尝试规避检测。**

## 环境要求

- Windows 宿主机 + VMware Workstation Pro 17+
- Python 3.10+
- Guest 已安装 VMware Tools
- `vmrun.exe` 可用（默认路径 `C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe`）

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
vm-auto-test --env-file .env
```

首次运行会自动进入环境配置向导，按提示填好 vmrun 路径和 guest 凭据即可。之后每次直接进主菜单：

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

报告在 `reports/时间戳-batch/` 下，每个样本一个子目录。

## 两种测试模式

| 模式 | 快照环境 | 作用 |
|---|---|---|
| `baseline` | 干净系统（无杀软） | 先确认样本本身有效（打了有反应） |
| `av` | 装了杀软的系统 | 再看杀软能不能拦截 |

AV 模式必须先有一份已通过的 baseline 报告。

## 报告目录

多样本运行：

```text
reports/
└── 20260508-120000-000000-batch/
    ├── result.json          # 汇总
    └── samples/
        ├── 1-A_schtasks/
        │   ├── result.json
        │   ├── before.txt
        │   ├── after.txt
        │   ├── sample_stdout.txt
        │   └── sample_stderr.txt
        └── 2-A_data/
            └── ...
```

## 命令行参考

如果不想用交互菜单，也可以直接传参：

| 命令 | 用途 |
|---|---|
| `vm-auto-test` | 交互菜单 |
| `vm-auto-test run-csv --csv ...` | CSV 批量测试 |
| `vm-auto-test run-dir --dir ...` | 扫描目录批量测试 |
| `vm-auto-test run ...` | 单样本临时验证 |
| `vm-auto-test vms` | 列出运行中的 VM |
| `vm-auto-test snapshots --vm ...` | 列出快照 |

## 输出比较策略

不配置时默认用 `changed`（前后归一化后不同即为有效）。可以按需配置更精确的策略：

| 策略 | 作用 | 配置示例 |
|---|---|---|
| `changed` | 归一化后前后不同 | 默认，无需配置 |
| `contains` | 输出包含指定字符串 | `value: "created"` |
| `regex` | 输出匹配正则 | `pattern: "Error: \d+"` |
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

当前 86 个测试，使用 fake provider，不需要真实 VMware 环境。

### 真实 VMware smoke test

```dotenv
# .env 里加上这些
VM_AUTO_TEST_SMOKE_VM_ID=F:\VMs\Win10\Win10.vmx
VM_AUTO_TEST_SMOKE_SNAPSHOT=clean-base
VMWARE_GUEST_USER=Administrator
VMWARE_GUEST_PASSWORD=your_password
```

```bash
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
    ├── env.py               # .env 加载
    ├── evaluator.py         # 输出归一化和比较策略
    ├── models.py            # 数据模型
    ├── orchestrator.py      # 测试流程编排
    ├── reporting.py         # 报告生成
    ├── smoke.py             # 真实 VMware smoke test
    └── providers/
        ├── base.py          # Provider 抽象
        ├── factory.py       # Provider 工厂
        └── vmrun_provider.py# VMware Workstation vmrun provider

tests/                       # fake provider 离线测试
```

## 安全边界

- 仅用于你拥有授权的本地虚拟机实验环境
- 使用隔离网络或 Host-only/NAT
- 每次测试前回滚到明确快照
- 不在生产主机或共享环境中运行未知样本
- Guest 密码写入 `.env` 文件，不要提交到 git
