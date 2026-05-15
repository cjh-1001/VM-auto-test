# VM Auto Test

面向 VMware Workstation 本地实验环境的自动化验证框架。将「回滚快照 → 启动 VM → 执行样本 → 验证效果 → 采集日志 → 生成报告」串成可重复流程，用于授权实验室中的样本效果验证与防御性 AV 拦截对比。

**安全边界：只做自动化执行、观察、比对和报告；不生成样本、不提供绕过能力、不尝试规避检测。**

## 功能一览

| 能力 | 入口 |
|------|------|
| 单样本验证 | `run` 或交互菜单 `[1]` |
| 计划任务 | 交互菜单 `[5]` 添加单测/CSV 批量任务后按顺序执行 |
| 批量目录 | `run-dir` 扫描目录批量运行 |
| CSV 批量 | `run-csv` 每样本独立验证命令 |
| YAML 配置 | `init-config` / `config validate` / `run --config` |
| Baseline 模式 | 干净快照中确认样本效果是否可观测 |
| AV 模式 | AV 快照中观察效果是否仍发生；baseline 为可选参考 |
| 报告 | 单样本 JSON/text；批量 JSON+CSV+HTML 交互报告+每样本 artifacts；`report` 从已有 JSON 重新生成 |
| 截图 | `--capture-screenshot` 或在交互菜单开启；支持样本执行中延迟截图 |
| AV 检测 | AV 模式下自动检测已知 AV 进程（腾讯电脑管家/360/火绒）+ 可配置日志采集 |

## 安装

Windows 宿主机、VMware Workstation Pro 17+、Python 3.10+、`vmrun.exe` 可用。

```bash
pip install -e .          # 生产安装
pip install -e .[dev]     # 开发安装
pip uninstall vm-auto-test
```

## 快速开始

```bash
vm-auto-test --help              # 确认 CLI 可用
vm-auto-test                     # 交互菜单
vm-auto-test doctor              # 检查本地环境

# 推荐流程：YAML 配置驱动
vm-auto-test init-config --output configs/baseline.yaml --mode baseline
vm-auto-test doctor --config configs/baseline.yaml
vm-auto-test config validate --config configs/baseline.yaml
vm-auto-test run --config configs/baseline.yaml

# 从已有 JSON 生成独立 HTML 报告
vm-auto-test report --input reports/latest/result.json --output reports/latest/report.html
```

交互菜单中的 `[5] 计划任务` 是本次会话内的内存队列：可添加单样本测试或 CSV 多样本测试，设置重复执行次数，然后一键按加入顺序执行；不会创建后台定时器，也不会持久化任务。

环境未配置时首次运行会进入引导，也可手动创建 `.env`：

```bash
copy .env.example .env
# 编辑 .env：VMRUN_PATH=D:\VMware\vmrun.exe
```

## 常用命令

`--env-file` 是顶层参数：`vm-auto-test --env-file .env <command>`

| 命令 | 用途 |
|------|------|
| `vm-auto-test` | 交互菜单 |
| `vm-auto-test vms` | 列出运行中的 VM |
| `vm-auto-test snapshots --vm "<vmx>"` | 列出快照 |
| `vm-auto-test doctor [--config <yaml>] [--reports-dir <dir>]` | 检查本地 CLI 环境，不连 VM |
| `vm-auto-test run ...` | 单样本 baseline/AV 验证 |
| `vm-auto-test run --config <yaml>` | YAML 配置运行（推荐入口） |
| `vm-auto-test run-dir ...` | 扫描目录批量验证 |
| `vm-auto-test run-csv ...` | CSV 批量验证 |
| `vm-auto-test init-config ...` | 创建 YAML 配置模板 |
| `vm-auto-test config validate --config <yaml>` | 校验 YAML 配置 |
| `vm-auto-test run-config <yaml>` | YAML 配置运行（兼容旧入口） |
| `vm-auto-test report --input <json> --output <file> [--format html\|json]` | 从已有 JSON 生成独立报告 |
| `vm-auto-test-smoke` | 真实 VMware 连通性 smoke test |

## VM 前置准备

每台测试 VM 需完成：

1. 关闭访问控制加密（vmrun 无法操作加密 VM）
2. 安装 VMware Tools（否则无法 Guest 命令、检查系统状态、截图）
3. 使用本地管理员账户（非微软在线账户）
4. 用该账户登录桌面至少一次（创建用户配置目录）
5. 准备明确快照（如 `clean-snapshot`、`av-installed`）
6. 确认样本路径在 Guest 内有效
7. 验证命令应观察效果，不要输出密码、令牌

```cmd
:: 在 VM 内以管理员身份创建本地账户
net user testuser <password> /add
net localgroup Administrators testuser /add
net user testuser /active:yes
```

> 创建或启用账户后必须用该账户登录一次桌面。

## Guest 凭证

通过交互菜单管理，不放在 `.env` 中：

1. `vm-auto-test` → `[3] 列出 VM` → 选择 VM → `[1] 配置凭证`
2. 输入 Guest 用户名密码，保存后自动验证
3. 已配置 VM 标注 `[已配置]`
4. `credentials.json` 为本地明文凭据文件，已在 `.gitignore` 忽略

## 环境诊断

`doctor` 只检查本地 CLI 环境，不连接 VM、不执行 Guest 命令。检查项：Python 版本、包版本、VMRUN_PATH、YAML 配置解析、报告目录可写性。失败返回 exit code `3`。

## 单样本测试

### Baseline（干净快照确认效果可观测）

```bash
vm-auto-test run \
  --vm "E:\VM-MCP\windows11\Windows 11 x64.vmx" \
  --mode baseline --snapshot "clean-snapshot" \
  --sample-command "C:\Samples\sample.exe" --sample-shell cmd \
  --verify-command "hostname" --verify-shell powershell \
  --capture-screenshot --reports-dir reports
```

> `hostname` 仅作 smoke 示例，真实测试应使用能观察目标效果的验证命令。

### AV（AV 快照中观察效果是否仍发生）

```bash
vm-auto-test run \
  --vm "E:\VM-MCP\windows11\Windows 11 x64.vmx" \
  --mode av --snapshot "av-installed" \
  --sample-command "C:\Samples\sample.exe" --sample-shell cmd \
  --verify-command "hostname" --verify-shell powershell \
  --baseline-result "reports/<timestamp>-sample/result.json" \
  --reports-dir reports
```

`--baseline-result` 为可选参考，非硬性前置条件。AV 模式会非阻塞检测已知 AV 进程（腾讯电脑管家 `QQPCTray.exe`、360 `360Tray.exe`、火绒 `HipsDaemon.exe`），检测失败不影响测试。

### 截图时机

开启 `--capture-screenshot` 后，框架在样本触发执行后延迟 10 秒自动截图，捕获样本运行中的 Guest 画面。截图与验证后截图互不覆盖。

## 批量测试

### 目录批量（`run-dir`）

适合多个样本共用同一验证命令。在宿主机扫描 `--dir`，将发现的路径作为 Guest 样本命令执行（仅在 Host 与 Guest 路径相同时适用）。

```bash
vm-auto-test run-dir --vm "<vmx>" --mode baseline --snapshot "clean-snapshot" \
  --dir "C:\Samples" --pattern "*.exe" \
  --verify-command "hostname" --verify-shell powershell --reports-dir reports
```

默认 pattern：`*.exe`、`*.bat`、`*.ps1`、`*.cmd`。PowerShell 脚本用 PowerShell 执行，其余用 cmd。

### CSV 批量（`run-csv`）

适合每样本独立验证命令。CSV 支持 UTF-8、UTF-8 BOM、GBK。

```bash
vm-auto-test run-csv --vm "<vmx>" --mode baseline --snapshot "clean-snapshot" \
  --csv samples.csv --samples-base-dir "C:\Samples" --reports-dir reports
```

| sample_file | verify_command | verify_shell |
|-------------|----------------|--------------|
| `sample.exe` | `hostname` | `cmd` |
| `test.bat` | `schtasks /query` | `powershell` |

第一列以 `sample` 开头时自动识别为表头。相对路径需配合 `--samples-base-dir`。

## 计划任务（交互菜单）

在 `vm-auto-test` 交互菜单选择 `[5] 计划任务`，可以先把多个测试加入本次会话的内存队列，再一键按顺序执行。

计划任务支持：

- 添加单样本测试：复用交互菜单 `[1]` 的参数收集流程。
- 添加 CSV 多样本测试：复用交互菜单 `[2]` 的参数收集流程。
- 为每个计划项设置重复执行次数，默认 `1`，最大 `100`。
- 查看、删除、清空当前队列。
- 一键顺序执行：单样本计划项调用单样本执行流程，多样本计划项调用批量执行流程。

注意：

- 计划任务不会持久化，退出当前交互会话后队列即消失。
- 计划任务不是后台定时器，也不会创建 cron/系统计划任务。
- 每个计划项仍按现有执行流程生成报告，并遵守快照回滚、Guest 凭据、安全边界和密码不打印规则。

## YAML 配置（推荐）

### 创建与运行

```bash
vm-auto-test init-config --output configs/baseline.yaml --mode baseline
vm-auto-test config validate --config configs/baseline.yaml
vm-auto-test run --config configs/baseline.yaml
```

> `run --config` 是推荐入口；`run-config <yaml>` 保留为兼容旧入口。`run --config` 不要与 `--vm`、`--mode`、`--sample-command` 等直接运行参数混用。

### 单样本配置

```yaml
vm_id: "E:\\VM-MCP\\windows11\\Windows 11 x64.vmx"
snapshot: "clean-snapshot"
mode: baseline
guest:
  user: testuser
  password_env: VMWARE_GUEST_PASSWORD
sample:
  command: "C:\\Samples\\sample.exe"
  shell: cmd
verification:
  command: "hostname"
  shell: powershell
reports_dir: reports
provider:
  type: vmrun
```

### 多样本配置

使用 `samples:` 替代 `sample:`（不能同时出现）。顶层 `verification` 为默认，每样本可用自己的 `verification` 覆盖：

```yaml
vm_id: "E:\\VM-MCP\\windows11\\Windows 11 x64.vmx"
snapshot: "clean-snapshot"
mode: baseline
guest:
  user: testuser
  password_env: VMWARE_GUEST_PASSWORD
verification:
  command: "hostname"
  shell: cmd
samples:
  - id: sample-a
    command: "C:\\Samples\\a.exe"
    shell: cmd
    verification:
      command: "type C:\\marker-a.txt"
      shell: cmd
  - id: sample-b
    command: "C:\\Samples\\b.ps1"
    shell: powershell
    verification:
      command: "Get-Content C:\\marker-b.txt"
      shell: powershell
reports_dir: reports
provider:
  type: vmrun
```

### AV 配置

```yaml
mode: av
baseline_result: "reports/<timestamp>-sample/result.json"  # 可选
```

优先使用 `guest.password_env`，避免 YAML 中写明文密码。

## 验证命令与比较策略

验证命令在样本执行前后各运行一次。默认策略为 `changed`：归一化后前后输出不同即视为效果发生。

| 策略 | 必要字段 | 效果 |
|------|----------|------|
| `changed` | — | 前后输出不同 |
| `contains` | `value` | 输出包含指定字符串 |
| `regex` | `pattern` | 输出匹配正则 |
| `json_field` | `path`, `expected` | JSON 字段等于预期值 |
| `file_hash` | `expected` | 输出 SHA-256 等于预期 |

示例：

```yaml
verification:
  command: "type C:\\marker.txt"
  shell: cmd
  comparisons:
    - type: contains
      target: after
      value: "created"
```

### 环境变量

交互式流程会预展开 `%VAR%`。非交互命令（`run`、`run-dir`、`run-csv`、`run-config`）不做预展开：
- `cmd`：使用 `%APPDATA%`
- PowerShell：使用 `$env:APPDATA`
- 避免硬编码 `C:\Users\<name>\...`

## Guest 命令执行原理

1. **生成脚本**：在宿主机临时目录创建用户脚本 + 包装脚本（重定向 stdout/stderr/exit code 到文件）
2. **复制入 Guest**：`vmrun CopyFileFromHostToGuest`
3. **执行**：`vmrun runProgramInGuest` 以凭证用户身份运行
4. **复制出 Guest**：`vmrun CopyFileFromGuestToHost`
5. **解码输出**：utf-8-sig → utf-8 → gbk → shift_jis 回退链
6. **清理**：`vmrun deleteFileInGuest` 删除临时文件

所有命令通过 `CommandResult` 统一返回（stdout、stderr、exit_code）。

## 结果判定

| 分类 | 控制台 | 含义 |
|------|--------|------|
| `BASELINE_VALID` | SUCCESS — 有效 | baseline 模式验证输出改变 |
| `BASELINE_INVALID` | FAILED — 无效 | baseline 模式验证输出未改变 |
| `AV_NOT_BLOCKED` | FAILED — 未拦截 | AV 模式效果仍发生 |
| `AV_BLOCKED_OR_NO_CHANGE` | SUCCESS — 已拦截 | AV 模式未观察到效果 |

## 报告目录

默认 `reports/`，可通过 `--reports-dir` 或 YAML `reports_dir` 覆盖。

### 单样本

```
reports/<timestamp>-<sample>/
  result.json          schema_version: 1
  before.txt / after.txt
  sample_stdout.txt / sample_stderr.txt
  test.log
  screenshot.png       # 可选（验证后；样本执行中截图也存此名）
  av_logs/             # 可选
```

### 批量

```
reports/<timestamp>-batch/
  result.json          schema_version: 2，批量汇总
  result.csv           UTF-8 BOM，Excel 友好，公式注入防护
  result.html          交互式 HTML（环形图/可排序表/复制按钮/响应式布局）
  test.log
  samples/<sample_id>/
    result.json        schema_version: 2
    before.txt / after.txt
    sample_stdout.txt / sample_stderr.txt
    screenshot.png
    av_logs/
```

### `report` 命令

从已有 `result.json` 重新生成独立 HTML 或格式化 JSON：

```bash
vm-auto-test report --input result.json --output report.html
vm-auto-test report --input result.json --output result.json --format json
```

默认 `--format html`。不重新执行 VM 测试，只读取输入 JSON 写输出文件。

批量运行自动生成的 `result.html` 包含：深蓝顶栏、统计卡片、环形进度图、判定分布面板、可排序样本表、状态标签、复制按钮、产出文件链接、下载栏、响应式布局，所有动态内容 HTML 转义。

## AV 日志采集

仅执行显式配置的安全命令：

```yaml
av_logs:
  collectors:
    - id: app-events
      type: guest_command
      command: "Get-WinEvent -LogName Application -MaxEvents 20"
      shell: powershell
```

## 配置文件

### `.env`

```bash
VMRUN_PATH=D:\VMware\vmrun.exe
VMWARE_CREDENTIALS_FILE=credentials.json   # 可选，默认 credentials.json
VMWARE_HOST=localhost                      # vmrest 可选
VMWARE_PORT=8697                           # vmrest 可选
```

### `credentials.json`

Key 为 `.vmx` 绝对路径：

```json
{
  "E:\\VM-MCP\\windows11\\Windows 11 x64.vmx": {
    "user": "testuser",
    "password": "<local-vm-password>"
  }
}
```

通过交互菜单添加、验证、重新配置。已在 `.gitignore` 忽略。

## Exit Codes

| Code | 含义 |
|------|------|
| `0` | 成功 |
| `2` | 参数/配置/输入文件/报告生成错误 |
| `3` | `doctor` 发现本地检查失败 |

## Troubleshooting

| 现象 | 常见原因 | 处理 |
|------|----------|------|
| 无法列出快照或命令超时 | VM 加密或 `.vmx` 路径错误 | 关闭加密，确认路径 |
| `VmToolsNotReadyError` | VMware Tools 未安装/未启动 | 安装/重启 VMware Tools |
| Guest 认证连续失败 | 用户名密码错误、在线账户、用户未登录 | 使用本地管理员账户，登录一次后重新配置 |
| `BASELINE_INVALID` | 样本路径不对、权限不足、验证命令无效 | 换验证命令并确认作用于凭证用户上下文 |
| AV 结果不好解释 | 没有可比 baseline | 可选先跑 baseline 并传入 `--baseline-result` |
| CSV 解析失败 | 编码/列数/相对路径/文件不存在 | 使用 UTF-8/GBK，列为 `sample_file,verify_command,verify_shell` |
| PowerShell 中 `%APPDATA%` 不生效 | `%VAR%` 是 cmd 语法 | PowerShell 用 `$env:APPDATA`，或改用 `cmd` |
| `run-dir` 找到文件但 Guest 执行失败 | Host 路径在 Guest 中不存在 | 使用共享/镜像路径，或改用 CSV |
| 截图缺失 | 未开启或截图步骤失败 | 使用 `--capture-screenshot`，检查 `result.json` steps |
| `run` 提示缺少参数 | 未传 `--config` 也未传直接运行参数 | 用 `run --config <yaml>`，或补齐所有必需参数 |
| `run cannot combine --config` | `run --config` 与直接运行参数混用 | 二选一 |
| `report` 输入不存在或 JSON 无效 | `--input` 路径错误 | 确认 `result.json` 路径和内容 |
| `doctor` 返回失败 | VMRUN_PATH 未设置/不存在、配置无效或目录不可写 | 修正 `.env` 或配置文件 |

## 开发

```bash
pytest
python -m compileall -q src tests
```

当前 116 个测试，使用 fake provider，不需真实 VMware 环境。`vm-auto-test-smoke` 仅确认需要时运行。

Provider 状态：`vmrun` 已实现（默认）；`vsphere`/`powercli`/`mcp` 占位待实现。

```
src/
├── vmware_mcp/              # vmrun/vmcli/VMware REST API 封装
└── vm_auto_test/            # 自动化测试框架
    ├── cli.py               # CLI 入口 + 交互菜单
    ├── config.py            # YAML/CSV 配置解析
    ├── env.py               # .env 加载 + 凭证管理
    ├── models.py            # 数据模型
    ├── orchestrator.py      # 测试编排（含样本执行中截图）
    ├── evaluator.py         # 输出归一化和比较策略
    ├── reporting.py         # 报告生成（JSON/CSV/HTML）
    ├── av_detection.py      # AV 进程检测
    ├── av_logs.py           # AV 日志采集
    ├── smoke.py             # 真实 VMware smoke test
    └── providers/
        ├── base.py / factory.py / vmrun_provider.py
```

## 安全边界

- 仅用于你拥有授权的本地虚拟机实验环境
- 使用隔离网络（Host-only 或 NAT）
- 每次测试前回滚到明确快照
- 不在生产主机或共享环境中运行未知样本
- 不提交 `.env`、`credentials.json`、报告目录或真实样本
- 不提供绕过、规避、隐蔽、持久化、提权、横向移动或 payload 生成建议
