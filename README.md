# VM Auto Test

面向本地 VMware Workstation 实验环境的自动化验证框架。它把“回滚快照 → 启动 VM → 执行样本 → 验证效果 → 采集日志 → 生成报告”串成可重复流程，用于授权实验室中的样本效果验证和防御性 AV 拦截对比。

**安全边界：只做自动化执行、观察、比对和报告；不生成样本、不提供绕过能力、不尝试规避检测。**

## 功能概览

| 能力 | 说明 |
|---|---|
| 单样本验证 | `run` 或交互菜单执行单个样本 |
| 批量目录验证 | `run-dir` 扫描目录并批量运行样本 |
| CSV 批量验证 | `run-csv` 支持每个样本独立验证命令 |
| YAML 配置 | `init-config` 创建配置，`config validate` 校验配置，`run --config` / `run-config` 复用配置 |
| Baseline 模式 | 在干净快照中确认样本效果是否可观测 |
| AV 模式 | 在装有 AV 的快照中观察效果是否仍发生；baseline 结果是可选参考 |
| 报告输出 | 单样本 JSON/text；批量 JSON + CSV + HTML + 每样本 artifacts；`report` 可从已有 JSON 生成独立 HTML/JSON |
| 可选截图 | `--capture-screenshot` 或交互菜单开启 |
| AV 上下文 | AV 模式下通过 `tasklist` 检测已知 AV 进程（腾讯电脑管家/360/火绒），并可采集显式配置的日志 |

## 安装

环境要求：Windows 宿主机、VMware Workstation Pro 17+、Python 3.10+、`vmrun.exe` 可用。

```bash
pip install -e .
```

开发安装：

```bash
pip install -e .[dev]
```

卸载：

```bash
pip uninstall vm-auto-test
```

## 快速开始

安装后确认 CLI 可用：

```bash
vm-auto-test --help
```

进入交互菜单：

```bash
vm-auto-test
```

使用 YAML 配置运行推荐流程：

```bash
vm-auto-test doctor --config configs/baseline.yaml
vm-auto-test config validate --config configs/baseline.yaml
vm-auto-test run --config configs/baseline.yaml
```

从已有结果 JSON 生成独立 HTML 报告：

```bash
vm-auto-test report --input reports/latest/result.json --output reports/latest/report.html
```

首次运行如果 `VMRUN_PATH` 未配置，会进入环境配置引导。也可以手动创建 `.env`：

```bash
copy .env.example .env
# 编辑 .env，填入 VMRUN_PATH=D:\VMware\vmrun.exe
```

交互菜单：

```text
—— VM Auto Test ——
[0] 退出
[1] 测试单样本
[2] 测试多样本 (CSV)
[3] 列出 VM
[4] 列出快照
[5] 重新配置环境
```

## VM 前置准备

每台测试 VM 需要先完成这些配置：

1. **关闭访问控制加密**：vmrun 通常无法操作加密 VM，表现为列快照失败、Guest 命令超时。
2. **安装 VMware Tools**：否则无法执行 Guest 命令、检查系统状态或截图。
3. **使用本地管理员账户**：不要使用微软在线账户；vmrun Guest 认证依赖本地账户。
4. **登录凭证用户一次**：首次登录会创建 `C:\Users\<用户名>\...` 用户配置目录。
5. **准备明确快照**：例如 `clean-snapshot`、`av-installed`。
6. **确认样本路径在 Guest 内有效**：Host 路径不一定等于 Guest 路径。
7. **设计安全验证命令**：验证命令应观察效果，不要输出密码、令牌等密钥。

本地管理员示例（在 VM 内以管理员身份执行）：

```cmd
net user testuser <password> /add
net localgroup Administrators testuser /add
net user testuser /active:yes
```

> 创建或启用账户后必须用该账户登录一次桌面，否则依赖用户目录的样本或验证命令可能失败。

## Guest 凭证

每个 VM 的凭证独立管理，不放在 `.env` 中：

1. 运行 `vm-auto-test`
2. 选择 `[3] 列出 VM`
3. 选择目标 VM
4. 选择 `[1] 配置凭证`
5. 输入 Guest 用户名和密码，保存后会自动验证

已配置的 VM 会标注 `[已配置]`。所有 Guest 命令（样本执行、验证命令、环境变量展开、日志采集）都以配置的凭证用户身份运行，不一定等于 VM 桌面当前登录用户。

`credentials.json` 是本地明文凭据文件，已在 `.gitignore` 中忽略；不要提交或分享。

## 常用命令

`--env-file` 是顶层参数：

```bash
vm-auto-test --env-file .env <command>
```

| 命令 | 用途 |
|---|---|
| `vm-auto-test` | 交互菜单 |
| `vm-auto-test vms` | 列出运行中的 VM |
| `vm-auto-test snapshots --vm "<vmx path>"` | 列出快照 |
| `vm-auto-test doctor [--config <yaml>]` | 检查本地 CLI 环境、VMRUN_PATH、配置和报告目录 |
| `vm-auto-test run ...` | 单样本 baseline/AV 验证 |
| `vm-auto-test run --config <yaml>` | 从 YAML 配置运行，推荐入口 |
| `vm-auto-test run-dir ...` | 扫描目录批量验证 |
| `vm-auto-test run-csv ...` | CSV 批量验证 |
| `vm-auto-test init-config ...` | 创建 YAML 配置 |
| `vm-auto-test config validate --config <yaml>` | 校验 YAML 配置 |
| `vm-auto-test run-config <yaml>` | 从 YAML 配置运行，兼容旧入口 |
| `vm-auto-test report --input result.json --output report.html` | 从已有 JSON 生成独立报告 |
| `vm-auto-test-smoke` | 真实 VMware 连通性 smoke test，需明确确认后运行 |

## 环境诊断

`doctor` 只检查本地 CLI 环境，不连接真实 VM，也不会执行 Guest 命令：

```bash
vm-auto-test doctor
vm-auto-test doctor --config configs/baseline.yaml --reports-dir reports
```

检查项：

| 检查项 | 说明 |
|---|---|
| Python | 当前 Python 是否满足 3.10+ |
| Package | 当前包版本，未以包形式安装时显示 warning |
| VMRUN_PATH | 是否配置且指向存在的文件 |
| Config | 可选，校验 YAML 是否能被解析为测试配置 |
| Reports directory | 报告目录是否可创建、可写入 |

`doctor` 发现失败项时返回 exit code `3`。配置检查失败时只显示错误类型，不打印 YAML 中的密码或其他敏感字段。

## 单样本测试

### Baseline 模式

用于在干净快照中确认样本效果是否可观测：

```bash
vm-auto-test run \
  --vm "E:\VM-MCP\windows11\Windows 11 x64.vmx" \
  --mode baseline \
  --snapshot "clean-snapshot" \
  --sample-command "C:\Samples\sample.exe" \
  --sample-shell cmd \
  --verify-command "hostname" \
  --verify-shell powershell \
  --capture-screenshot \
  --reports-dir reports
```

`hostname` 只是无害 smoke 示例；真实测试应使用能观察目标效果的验证命令。

### AV 模式

用于在 AV 快照中观察效果是否仍发生。`baseline_result` 是可选参考，不是硬性前置条件：

```bash
vm-auto-test run \
  --vm "E:\VM-MCP\windows11\Windows 11 x64.vmx" \
  --mode av \
  --snapshot "av-installed" \
  --sample-command "C:\Samples\sample.exe" \
  --sample-shell cmd \
  --verify-command "hostname" \
  --verify-shell powershell \
  --baseline-result "reports/20260509-120000-000000-sample/result.json" \
  --reports-dir reports
```

AV 模式会进行非阻塞的已知 AV 进程检测。检测结果只是报告上下文，不用于绕过或规避。

**检测机制**：通过 PowerShell 执行 `tasklist` 并按进程名匹配已知 AV 标记：

| AV 产品 | 标记进程 |
|---|---|
| 腾讯电脑管家 | `QQPCTray.exe` |
| 360安全卫士 | `360Tray.exe`（同时检查 `ZhuDongFangYu.exe`） |
| 火绒安全软件 | `HipsDaemon.exe`（同时检查 `HipsMain.exe`、`HipsTrat.exe`） |

检测失败不会中断测试流程。

## 批量测试

### 目录批量

适合多个样本共用同一个验证命令：

```bash
vm-auto-test run-dir \
  --vm "E:\VM-MCP\windows11\Windows 11 x64.vmx" \
  --mode baseline \
  --snapshot "clean-snapshot" \
  --dir "C:\Samples" \
  --pattern "*.exe" \
  --verify-command "hostname" \
  --verify-shell powershell \
  --reports-dir reports
```

`run-dir` 在运行 CLI 的宿主机扫描 `--dir`，再把扫描到的路径作为 Guest 样本命令。只有当 Host 路径也能在 Guest 中访问时才适合使用，例如共享目录或镜像路径。否则请使用 CSV 明确写 Guest 路径。

未传 `--pattern` 时默认扫描：`*.exe`、`*.bat`、`*.ps1`、`*.cmd`。

### CSV 批量

适合每个样本有独立验证命令：

```bash
vm-auto-test run-csv \
  --vm "E:\VM-MCP\windows11\Windows 11 x64.vmx" \
  --mode baseline \
  --snapshot "clean-snapshot" \
  --csv samples.csv \
  --samples-base-dir "C:\Samples" \
  --reports-dir reports
```

CSV 支持 UTF-8、UTF-8 BOM、GBK。表头可选；第一列以 `sample` 开头时会识别为表头。

| sample_file | verify_command | verify_shell |
|---|---|---|
| `sample.exe` | `hostname` | `cmd` |
| `test.bat` | `schtasks /query` | `powershell` |

相对 `sample_file` 需要配合 `--samples-base-dir`。CSV 路径最终会成为 Guest 内执行的样本命令。

## YAML 配置

YAML 是推荐的可重复运行方式。配置文件可以先校验，再运行：

```bash
vm-auto-test config validate --config configs/baseline.yaml
vm-auto-test run --config configs/baseline.yaml
```

`run --config` 是推荐入口；`run-config <yaml>` 保留为兼容旧脚本的入口。使用 `run --config` 时不要再混用 `--vm`、`--mode`、`--sample-command`、`--reports-dir` 等直接运行参数，CLI 会直接拒绝这类歧义调用。

创建配置：

```bash
vm-auto-test init-config --output configs/baseline.yaml --mode baseline
vm-auto-test init-config --output configs/batch.yaml --mode baseline --vm "<vmx path>" --samples-dir "C:\Samples"
```

运行配置：

```bash
vm-auto-test run --config configs/baseline.yaml
# 兼容旧入口：vm-auto-test run-config configs/baseline.yaml
```

单样本配置：

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

多样本配置使用 `samples:`，不要同时写 `sample:`。当前解析器仍要求顶层 `verification`，样本可用自己的 `verification` 覆盖：

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

AV 配置可选写入 baseline 参考：

```yaml
mode: av
baseline_result: "reports/20260509-120000-000000-sample/result.json"  # optional
```

优先使用 `guest.password_env`，避免在 YAML 中写明文密码。

## 验证命令与环境变量

验证命令会在样本执行前后各运行一次。默认比较策略是 `changed`：归一化后的前后输出不同，即认为效果发生。

交互式单样本和交互式 CSV 流程会预展开 `%VAR%`：框架以凭证用户运行 `echo %VAR%`，并在安全时把错误用户目录改写为凭证用户目录。

非交互命令 `run`、`run-dir`、`run-csv`、`run-config` 不做这一步预展开：

- `cmd` 验证命令可以使用 `%APPDATA%`。
- PowerShell 验证命令应使用 `$env:APPDATA`。
- 尽量避免硬编码 `C:\Users\<name>\...`。

比较策略可在 YAML 中配置：

| 策略 | 必要字段 | 作用 |
|---|---|---|
| `changed` | 无 | 前后输出不同 |
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

## Guest 命令执行原理

所有 Guest 命令（样本执行、验证命令、环境变量展开、AV 检测、日志采集）通过同一套文件传输机制实现：

1. **生成脚本**：框架在宿主机临时目录创建用户脚本（`.bat` / `.ps1`）和包装脚本。包装脚本调用用户脚本并将 stdout/stderr 重定向到文件，同时将 `%ERRORLEVEL%` / `$LASTEXITCODE` 写入单独的 exit code 文件。
2. **复制入 Guest**：通过 `vmrun CopyFileFromHostToGuest` 将两个脚本传入 Guest 临时目录。
3. **执行包装脚本**：通过 `vmrun runProgramInGuest` 以配置的凭证用户身份运行包装脚本（`cmd.exe /c` 或 `powershell.exe -File`）。
4. **复制出 Guest**：通过 `vmrun CopyFileFromGuestToHost` 将输出文件和 exit code 文件取回宿主机。
5. **解码输出**：按 `utf-8-sig` → `utf-8` → `gbk` → `shift_jis` 回退链解码，容错 Windows 中文系统编码差异。
6. **清理**：通过 `vmrun deleteFileInGuest` 删除 Guest 上的脚本和临时文件。

所有命令的超时、认证失败、非零退出码均通过 `CommandResult` 统一返回，包含 `stdout`、`stderr`、`exit_code` 和 `capture_method`（固定为 `"redirected_file"`）。

## 结果判定

| 分类 | 控制台含义 | 解释 |
|---|---|---|
| `BASELINE_VALID` | SUCCESS — 有效 | baseline 模式下验证输出改变，样本效果可观测 |
| `BASELINE_INVALID` | FAILED — 无效 | baseline 模式下验证输出未改变 |
| `AV_NOT_BLOCKED` | FAILED — 未拦截 | AV 模式下效果仍发生 |
| `AV_BLOCKED_OR_NO_CHANGE` | SUCCESS — 已拦截 | AV 模式下未观察到效果，可能是拦截或样本未生效 |

## 报告目录

默认写入 `reports/`，可通过 `--reports-dir` 或 YAML `reports_dir` 覆盖。

单样本报告：

```text
reports/<timestamp>-<sample>/
  result.json            # schema_version: 1
  before.txt             # 验证命令执行前 stdout+stderr
  after.txt              # 验证命令执行后 stdout+stderr
  sample_stdout.txt      # 样本 stdout
  sample_stderr.txt      # 样本 stderr
  test.log
  screenshot.png         # 可选
  av_logs/               # 可选
```

批量报告：

```text
reports/<timestamp>-batch/
  result.json            # schema_version: 2，批量汇总
  result.csv             # UTF-8 BOM，一行一个样本，适合 Excel
  result.html            # 交互式 HTML 汇总报告（环形图/可排序表/复制按钮/自适应布局）
  test.log
  samples/<sample_id>/
    result.json          # schema_version: 2，单样本结果
    before.txt
    after.txt
    sample_stdout.txt
    sample_stderr.txt
    screenshot.png       # 可选
    av_logs/             # 可选
```

`result.csv` 使用 `utf-8-sig` 编码，Excel 可直接打开。对以 `=`、`+`、`-`、`@` 开头的单元格会前置 `'` 防止公式注入，并在检测前缀前先剥离空格和 Unicode 控制/格式字符。

也可以从已有 `result.json` 重新生成一个独立 HTML 或格式化 JSON 文件：

```bash
vm-auto-test report --input reports/<timestamp>-batch/result.json --output reports/summary.html
vm-auto-test report --input reports/<timestamp>-batch/result.json --output reports/summary.json --format json
```

`report` 命令不会重新执行 VM 测试，只读取输入 JSON 并写入指定输出文件。默认 `--format html`，当前支持 `html` 和 `json`。它生成的是简版独立 HTML（JSON 视图），适合快速分享或归档已有结果；不会重建批量报告里的图表、排序表或下载栏。

批量运行自动生成的 `result.html` 是完整的单文件企业级安全测试报告，特性包括：

- **深蓝顶栏**：统一展示模式、快照、生成时间
- **快速统计卡片**：样本总数、通过/拦截数、未通过/未拦截数、通过率
- **环形进度图**：绿色（通过/拦截）与红色（未通过/未拦截）占比可视化
- **判定分布面板**：每类结果的数量统计，带颜色状态点
- **可排序样本表**：点击表头按样本 ID / 判定结果 / 效果 / 命令排序
- **状态标签**：绿色/红色徽标区分 pass/fail 判定
- **复制按钮**：样本命令和验证命令旁的复制到剪贴板功能
- **产出文件链接**：每样本链接到 `result.json`、`before.txt`、`after.txt`、stdout、stderr，截图存在时显示截图链接
- **底部下载栏**：带图标的 `result.json` / `result.csv` 下载按钮
- **响应式布局**：适配桌面、平板、手机屏幕
- **所有动态内容 HTML 转义**：命令、路径、ID 等均使用 `html.escape()` 处理

报告会原样保存验证命令输出、样本 stdout/stderr 和配置的 AV 日志输出。请把报告目录当作敏感本地证据处理，避免让验证或日志命令打印密码、令牌等密钥。

## AV 日志采集

只执行你显式配置的安全命令，不内置厂商特定采集逻辑：

```yaml
av_logs:
  collectors:
    - id: app-events
      type: guest_command
      command: "Get-WinEvent -LogName Application -MaxEvents 20"
      shell: powershell
```

AV 模式的进程检测机制见上方 AV 模式命令说明。识别失败不影响测试流程。

## 配置文件

### `.env`

```bash
VMRUN_PATH=D:\VMware\vmrun.exe
VMWARE_CREDENTIALS_FILE=credentials.json   # 可选，默认 credentials.json
VMWARE_HOST=localhost                       # vmrest 可选
VMWARE_PORT=8697                            # vmrest 可选
```

### `credentials.json`

Key 为 `.vmx` 文件绝对路径，避免同名 VM 冲突：

```json
{
  "E:\\VM-MCP\\windows11\\Windows 11 x64.vmx": {
    "user": "testuser",
    "password": "<local-vm-password>"
  }
}
```

通过交互菜单 `[3] 列出 VM` 添加、验证、重新配置。该文件为本地明文凭据文件，已在 `.gitignore` 中忽略。

## Troubleshooting

| 现象 | 常见原因 | 处理 |
|---|---|---|
| 无法列出快照或命令超时 | VM 开了访问控制加密，或 `.vmx` 路径错误 | 关闭加密并确认 VM 路径 |
| `VmToolsNotReadyError` | VMware Tools 未安装、未启动或系统未就绪 | 安装/重启 VMware Tools 后重试 |
| Guest 认证连续失败 | 用户名密码错误、在线账户、用户未登录初始化 | 使用本地管理员账户，登录一次后重新配置 |
| `BASELINE_INVALID` | 样本路径不对、权限不足、验证命令观察不到效果 | 换验证命令并确认作用于凭证用户上下文 |
| AV 结果不好解释 | 没有可比 baseline | 可选先跑 baseline 并传入 `--baseline-result` |
| CSV 解析失败 | 编码、列数、shell、相对路径或文件不存在 | 使用 UTF-8/GBK，列为 `sample_file,verify_command,verify_shell` |
| PowerShell 中 `%APPDATA%` 不生效 | `%VAR%` 是 cmd 语法 | PowerShell 用 `$env:APPDATA`，或改用 `cmd` |
| `run-dir` 找到文件但 Guest 执行失败 | Host 路径在 Guest 中不存在 | 使用共享/镜像路径，或改用 CSV 明确 Guest 路径 |
| 截图缺失 | 未开启截图或截图步骤失败 | 使用 `--capture-screenshot`，检查 `result.json` steps |
| `run` 提示缺少参数 | 未传 `--config`，也未传完整直接运行参数 | 用 `run --config <yaml>`，或补齐 `--vm`、`--mode`、`--sample-command`、`--verify-command` |
| `run cannot combine --config` | `run --config` 和直接运行参数混用 | 二选一：要么只使用配置文件，要么完全使用命令行参数 |
| `report` 输入不存在或 JSON 无效 | `--input` 路径错误，或文件不是合法 JSON | 确认 `result.json` 路径，并检查文件内容 |
| `doctor` 返回失败 | `VMRUN_PATH` 未设置/不存在、配置无效或报告目录不可写 | 修正 `.env`、配置文件或目录权限后重试 |

## Exit Codes

| Code | 含义 |
|---|---|
| `0` | 命令成功 |
| `2` | 参数、配置、输入文件或报告生成错误 |
| `3` | `doctor` 发现本地依赖、配置或目录检查失败 |

交互取消通常返回 `0`。

## 开发

运行测试：

```bash
pytest
python -m compileall -q src tests
```

当前 116 个测试，使用 fake provider，不需要真实 VMware 环境。

真实 VMware smoke test 会触碰本机 VMware 环境，只在确认需要时运行：

```bash
vm-auto-test-smoke
```

Provider 状态：

| provider | 状态 |
|---|---|
| `vmrun` | 已实现，默认 |
| `vsphere` / `powercli` / `mcp` | 占位，待后续实现 |

项目结构：

```text
src/
├── vmware_mcp/              # 复用 VMware MCP / vmrun 封装
└── vm_auto_test/            # 自动化测试框架
    ├── av_detection.py      # 杀软环境自动识别
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
        ├── base.py
        ├── factory.py
        └── vmrun_provider.py

tests/                       # fake provider 离线测试
```

## 安全边界

- 仅用于你拥有授权的本地虚拟机实验环境。
- 使用隔离网络、Host-only 或 NAT。
- 每次测试前回滚到明确快照。
- 不在生产主机或共享环境中运行未知样本。
- 不提交 `.env`、`credentials.json`、报告目录或真实样本。
- 不提供绕过、规避、隐蔽、持久化、提权、横向移动或 payload 生成建议。
