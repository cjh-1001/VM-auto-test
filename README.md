# VM Auto Test

VM Auto Test 是一个面向本地授权 VMware Workstation 实验环境的自动化验证框架。它复用现有 VMware MCP / `vmrun` 封装，把快照回滚、guest 命令执行、样本运行、结果采集和报告记录串成稳定、可重复的测试流程。

当前项目聚焦“第二部分”：自动化测试样本可用性和杀软拦截情况；环境部署部分暂不实现。

## 安全边界

本项目只用于你拥有授权的本地虚拟机实验环境。

框架不会生成攻击样本、不会提供绕过杀软能力，也不会尝试规避检测。它只负责执行你明确提供的样本命令、验证命令和日志采集命令，并记录 before / after 的结果差异。

建议实验环境满足：

- 使用隔离网络或 Host-only/NAT 网络
- 每次测试前回滚到明确快照
- 不在生产主机或共享环境中运行未知样本
- 不把 guest 密码写进命令行历史、配置文件或报告文件
- 仅使用你有权限测试的样本、虚拟机和杀软环境

## 当前能力

### VMware 控制能力

当前默认 provider 是 `vmrun`，底层复用 `src/vmware_mcp/vmrun.py` 中的 `VMRun`：

- 列出运行中的虚拟机
- 列出虚拟机快照
- 回滚到指定快照
- 启动虚拟机
- 等待 VMware Tools 可用
- 在 guest 中执行 `cmd` 或 PowerShell 命令
- 将 guest 命令输出重定向到临时文件并复制回宿主机读取

### 自动化验证能力

| 模式 | 目的 | 默认判断规则 |
|---|---|---|
| `baseline` | 验证样本和验证命令是否有效 | before / after 不同 => `BASELINE_VALID` |
| `av` | 验证杀软环境下攻击效果是否发生 | before / after 不同 => `AV_NOT_BLOCKED` |

AV 模式必须依赖一个已经通过的 baseline 报告，即 `result.json` 中的 classification 必须是 `BASELINE_VALID`。

已实现的扩展能力：

- 配置文件工作流：`init-config` / `run-config`
- 一次运行多个样本：`samples` 列表
- 输出比较策略：`changed`、`contains`、`regex`、`json_field`、`file_hash`
- 通用 AV 日志采集接口：`av_logs.collectors`
- 真实 VMware smoke test 入口：`vm-auto-test-smoke`
- Claude Code skill：`.claude/skills/vm-auto-test/SKILL.md`

## 项目结构

```text
src/
├── vmware_mcp/              # 复用的 VMware MCP / vmrun 封装
└── vm_auto_test/            # 自动化测试框架
    ├── av_logs.py           # AV 日志采集接口
    ├── cli.py               # 命令行入口
    ├── config.py            # YAML 配置解析
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

## 环境要求

- Windows 宿主机
- VMware Workstation Pro 17+
- Python 3.10+
- guest 虚拟机已安装 VMware Tools
- guest 凭据可用于 VMware guest operations
- `vmrun.exe` 可用，默认路径通常是：

```text
C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe
```

## 安装

在项目目录中执行：

```bash
pip install -e .
```

如果要运行测试：

```bash
pip install -e .[dev]
```

安装后会得到三个命令：

| 命令 | 用途 |
|---|---|
| `vm-auto-test` | 自动化验证 CLI |
| `vm-auto-test-smoke` | 真实 VMware 链路 smoke test |
| `vmware-mcp` | 原 VMware MCP server 入口 |

## 跑通真实 MVP

下面是从零跑通 baseline，再进入 AV 验证的最短路径。

### 1. 准备 `.env`

```bash
copy .env.example .env
```

编辑 `.env`，至少确认：

```dotenv
VMRUN_PATH="C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe"
VMWARE_GUEST_USER=Administrator
VMWARE_GUEST_PASSWORD=your_password
```

`.env` 已被 `.gitignore` 忽略，不要提交真实密码。

### 2. 确认 CLI 能看到 VM

先在 VMware Workstation 中启动目标虚拟机，然后运行：

```bash
vm-auto-test --env-file .env vms
```

示例输出：

```text
[1] F:\VMs\Win10\Win10.vmx
```

如果看不到 VM，先检查 VMware 是否已启动目标 VM、`VMRUN_PATH` 是否正确、VMware Tools 是否安装。

### 3. 生成并运行 baseline

```bash
vm-auto-test --env-file .env init-config --output configs/baseline.yaml --mode baseline
vm-auto-test --env-file .env run-config configs/baseline.yaml
```

交互中需要填写：

- VM ID 或 `.vmx` 路径
- 快照名
- 样本命令
- 验证命令
- guest 用户名
- guest 密码环境变量名
- reports 目录

如果输出：

```text
classification=BASELINE_VALID
```

说明样本和验证命令有效，可以进入 AV 环境验证。

### 4. 生成并运行 AV 配置

```bash
vm-auto-test --env-file .env init-config --output configs/av.yaml --mode av
vm-auto-test --env-file .env run-config configs/av.yaml
```

AV 配置会要求输入 baseline 的 `result.json` 路径，例如：

```text
reports\20260507-120000-000000-sample\result.json
```

结果含义：

| classification | 含义 |
|---|---|
| `AV_NOT_BLOCKED` | before / after 不同，攻击效果发生 |
| `AV_BLOCKED_OR_NO_CHANGE` | before / after 相同，攻击效果未发生；结合有效 baseline，可作为拦截成功倾向记录 |

## 常用命令

### 列出运行中的虚拟机

```bash
vm-auto-test --env-file .env vms
```

### 列出快照

```bash
vm-auto-test --env-file .env snapshots --vm "F:\VMs\Win10\Win10.vmx"
```

### 不使用配置文件直接跑单样本

适合临时验证；正式批量测试建议使用 YAML 配置。

```bash
vm-auto-test --env-file .env run \
  --mode baseline \
  --vm "F:\VMs\Win10\Win10.vmx" \
  --snapshot "clean-base" \
  --sample-command "C:\Samples\sample.exe" \
  --sample-shell cmd \
  --verify-command "Get-Content C:\marker.txt" \
  --verify-shell powershell
```

AV 模式需要额外传入 baseline 结果：

```bash
vm-auto-test --env-file .env run \
  --mode av \
  --vm "F:\VMs\Win10\Win10.vmx" \
  --snapshot "av-installed" \
  --sample-command "C:\Samples\sample.exe" \
  --sample-shell cmd \
  --verify-command "Get-Content C:\marker.txt" \
  --verify-shell powershell \
  --baseline-result "reports\20260507-120000-000000-sample\result.json"
```

## 配置文件示例

### 单样本 baseline

```yaml
vm_id: F:\VMs\Win10\Win10.vmx
snapshot: clean-base
mode: baseline
guest:
  user: Administrator
  password_env: VMWARE_GUEST_PASSWORD
sample:
  command: C:\Samples\sample.exe
  shell: cmd
verification:
  command: Get-Content C:\marker.txt
  shell: powershell
reports_dir: reports
timeouts:
  wait_guest_seconds: 180
  command_seconds: 120
normalize:
  trim: true
  ignore_empty_lines: true
provider:
  type: vmrun
```

### 单样本 AV

```yaml
vm_id: F:\VMs\Win10\Win10.vmx
snapshot: av-installed
mode: av
baseline_result: reports\20260507-120000-000000-sample\result.json
guest:
  user: Administrator
  password_env: VMWARE_GUEST_PASSWORD
sample:
  command: C:\Samples\sample.exe
  shell: cmd
verification:
  command: Get-Content C:\marker.txt
  shell: powershell
reports_dir: reports
provider:
  type: vmrun
```

### 多样本 baseline

`run-config` 支持 `samples` 列表。批量模式默认按顺序执行，每个样本都会重新回滚快照、启动并等待 guest ready，避免样本之间污染状态。

```yaml
vm_id: F:\VMs\Win10\Win10.vmx
snapshot: clean-base
mode: baseline
guest:
  user: Administrator
  password_env: VMWARE_GUEST_PASSWORD
samples:
  - id: sample-one
    command: C:\Samples\one.exe
    shell: cmd
  - id: sample-two
    command: C:\Samples\two.exe
    shell: cmd
verification:
  command: Get-Content C:\marker.txt
  shell: powershell
  comparisons:
    - type: changed
reports_dir: reports
provider:
  type: vmrun
```

单个样本也可以覆盖验证命令和比较策略：

```yaml
samples:
  - id: json-check
    command: C:\Samples\json-check.exe
    shell: cmd
    verification:
      command: Get-Content C:\result.json
      shell: powershell
      comparisons:
        - type: json_field
          path: result.status
          expected: created
```

## 输出比较策略

如果不配置 `comparisons`，默认使用 `changed` 逻辑：

```text
normalize(before) != normalize(after)
```

归一化规则默认会统一换行、去掉每行首尾空白、忽略空行。

| type | 用途 | 关键字段 |
|---|---|---|
| `changed` | before / after 归一化后不同 | 无 |
| `contains` | 指定输出包含字符串 | `value`, 可选 `target` |
| `regex` | 指定输出匹配正则 | `pattern`, 可选 `target` |
| `json_field` | 指定输出为 JSON，检查 dotted path | `path`, `expected`, 可选 `target` |
| `file_hash` | 对输出内容做 SHA-256 比较 | `expected`, 可选 `target` |

`target` 默认为 `after`，也可以设为 `before`。

## AV 日志采集

日志采集接口只执行你在配置里显式提供的 guest 命令，不内置杀软厂商命令，也不提供绕过检测能力。

```yaml
av_logs:
  collectors:
    - id: app-events
      type: guest_command
      command: Get-WinEvent -LogName Application -MaxEvents 20
      shell: powershell
```

日志输出会写入对应样本报告目录下的 `av_logs/`。

## 报告目录

单样本运行会在 `reports/` 下生成一个独立目录：

```text
reports/
└── 20260507-120000-000000-sample/
    ├── result.json
    ├── before.txt
    ├── after.txt
    ├── sample_stdout.txt
    └── sample_stderr.txt
```

多样本运行会生成顶层汇总和每个样本的子报告：

```text
reports/
└── 20260507-120000-000000-batch/
    ├── result.json
    └── samples/
        ├── sample-one/
        │   ├── result.json
        │   ├── before.txt
        │   ├── after.txt
        │   ├── sample_stdout.txt
        │   ├── sample_stderr.txt
        │   └── av_logs/
        └── sample-two/
            └── ...
```

报告会记录测试模式、分类结果、VM、快照、样本命令、验证命令、before / after hash、输出捕获方式和执行步骤。报告不会写入 guest 密码。

## 命令输出捕获方式

当前 provider 使用文件回传方式捕获 guest 输出：

1. 在 guest 中创建临时输出文件
2. 执行用户提供的命令，并把输出重定向到临时文件
3. 使用 VMware guest file copy 把输出文件复制回宿主机
4. 读取本地文件并写入报告
5. 尝试清理 guest 临时文件

这样可以绕过 `vmrun runProgramInGuest` 不稳定返回 stdout 的问题。

## 测试

安装开发依赖后：

```bash
pytest
```

当前测试使用 fake provider，不需要真实 VMware 环境即可验证：

- baseline / av 分类逻辑
- AV baseline 依赖校验
- 输出归一化和比较策略
- 多样本 batch 执行与报告
- AV 日志采集接口
- provider factory
- `.env` 加载

基础编译检查：

```bash
python -m compileall -q src tests
```

### 真实 VMware smoke test

如果本机已经配置好授权实验 VM，可以通过环境变量启用真实 smoke test：

```dotenv
VM_AUTO_TEST_SMOKE_VM_ID=F:\VMs\Win10\Win10.vmx
VM_AUTO_TEST_SMOKE_SNAPSHOT=clean-base
VMWARE_GUEST_USER=Administrator
VMWARE_GUEST_PASSWORD=your_password
```

然后运行：

```bash
vm-auto-test-smoke
```

smoke test 只做无害链路检查：列快照、可选回滚、启动、等待 VMware Tools、执行 `hostname`。

## Provider 状态

| provider | 状态 | 说明 |
|---|---|---|
| `vmrun` | 已实现 | 默认 provider，基于 VMware Workstation `vmrun.exe` |
| `vsphere` | 占位 | 需要后续补充 SDK、凭据和环境设计 |
| `powercli` | 占位 | 需要后续补充 PowerCLI 调用方式 |
| `mcp` | 占位 | 需要后续定义 MCP tool client 协议 |

## 与上游 VMware MCP 的关系

本项目复用了一个 VMware Workstation MCP/server 封装作为底层能力来源，尤其是 `src/vmware_mcp/vmrun.py` 中的 `VMRun` 类。

原 MCP 能力仍然保留，可以继续通过 `vmware-mcp` 命令作为 MCP server 使用。本项目新增的是 `src/vm_auto_test/`，用于在 VMware 能力之上实现自动化测试流程。

## 后续可增强

- 接入真实 vSphere / PowerCLI / MCP provider
- 增加厂商特定日志解析器，但保持命令由用户显式配置
- 增加更丰富的报告导出格式
- 为真实 VMware smoke test 增加 pytest marker 集成

## 许可证

项目中复用的 VMware MCP 代码遵循其原许可证。新增的 `vm_auto_test` 自动化测试框架部分按本项目后续指定的许可证管理。
