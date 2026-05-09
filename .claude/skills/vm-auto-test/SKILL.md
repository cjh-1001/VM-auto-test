# VM Auto Test

VMware Workstation 自动化验证框架：回滚快照 → 执行样本 → 采集结果 → 生成报告。

**只做自动化执行和结果比对，不生成样本、不提供绕过能力、不尝试规避检测。**

## 前置条件（Agent 检查清单）

在执行任何测试前，确认以下条件满足：

1. `vmrun.exe` 可用，路径已配置在 `.env` 的 `VMRUN_PATH`
2. 目标 VM 已安装 VMware Tools，未启用访问控制加密
3. 目标 VM 中已创建本地管理员账户（非微软在线账户）
4. `credentials.json` 中已配置该 VM 的 Guest 凭证
5. VM 有可回滚的快照

## CLI 命令参考

```
vm-auto-test                          # 交互菜单（无参数时）
vm-auto-test --env-file .env <cmd>    # --env-file 仅顶层参数
```

| 命令 | 用途 | 关键参数 |
|------|------|----------|
| `vm-auto-test` | 交互菜单 | — |
| `vm-auto-test vms` | 列出运行中 VM | — |
| `vm-auto-test snapshots --vm <path>` | 列出 VM 快照 | `--vm` |
| `vm-auto-test run` | 单样本测试 | `--vm`, `--mode`, `--snapshot`, `--sample-command`, `--verify-command` |
| `vm-auto-test run-dir` | 扫描目录批量测试 | `--vm`, `--mode`, `--dir`, `--verify-command` |
| `vm-auto-test run-csv` | CSV 批量测试 | `--vm`, `--mode`, `--csv`, `--samples-base-dir` |
| `vm-auto-test init-config` | 交互生成 YAML 配置 | `--output`, `--mode`, `--vm` |
| `vm-auto-test run-config <yaml>` | 执行 YAML 配置 | config 路径, `--guest-password` |
| `vm-auto-test-smoke` | 真实 VMware 冒烟测试 | — |

### `run` 命令完整参数

```bash
vm-auto-test run \
  --vm "E:\VM-MCP\windows11\Windows 11 x64.vmx" \
  --mode baseline \
  --snapshot "clean-snapshot" \
  --sample-command "C:\Samples\sample.exe" \
  --sample-shell cmd \
  --verify-command "hostname" \
  --verify-shell powershell \
  --guest-user testuser \
  --guest-password admin123 \
  --reports-dir reports
```

AV 模式额外需要 `--baseline-result` 指向通过的 baseline `result.json`。

### `run-dir` 命令

```bash
vm-auto-test run-dir \
  --vm "E:\VM-MCP\windows11\Windows 11 x64.vmx" \
  --mode baseline \
  --snapshot "clean-snapshot" \
  --dir "C:\Samples" \
  --pattern "*.exe" \
  --verify-command "hostname" \
  --verify-shell powershell
```

### `run-csv` 命令

```bash
vm-auto-test run-csv \
  --vm "E:\VM-MCP\windows11\Windows 11 x64.vmx" \
  --mode baseline \
  --csv samples.csv \
  --samples-base-dir "C:\Samples"
```

CSV 格式 (UTF-8, 3列, 无表头或首列为 `sample_file` 时跳过):

| sample_file | verify_command | verify_shell |
|-------------|----------------|-------------|
| `sample.exe` | `hostname` | `cmd` |
| `test.bat` | `schtasks /query` | `powershell` |

## YAML 配置模板

### 单样本 baseline

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
timeouts:
  wait_guest_seconds: 180
  command_seconds: 120
provider:
  type: vmrun
```

### 多样本 baseline

```yaml
vm_id: "E:\\VM-MCP\\windows11\\Windows 11 x64.vmx"
snapshot: "clean-snapshot"
mode: baseline
guest:
  user: testuser
  password_env: VMWARE_GUEST_PASSWORD
samples:
  - id: sample1
    command: "C:\\Samples\\sample1.exe"
    shell: cmd
  - id: sample2
    command: "C:\\Samples\\sample2.exe"
    shell: cmd
verification:
  command: "hostname"
  shell: powershell
reports_dir: reports
```

### AV 模式（需 baseline_result）

```yaml
vm_id: "E:\\VM-MCP\\windows11\\Windows 11 x64.vmx"
snapshot: "av-snapshot"
mode: av
baseline_result: "reports/20260509-120000-000000-sample/result.json"
guest:
  user: testuser
  password_env: VMWARE_GUEST_PASSWORD
samples:
  - id: sample1
    command: "C:\\Samples\\sample1.exe"
    shell: cmd
verification:
  command: "hostname"
  shell: powershell
av_logs:
  collectors:
    - id: app-events
      type: guest_command
      command: "Get-WinEvent -LogName Application -MaxEvents 20"
      shell: powershell
reports_dir: reports
```

## 工作流程

### 快速单样本测试（推荐首选用法）

```bash
# 1. 确保 env 和 credentials 就绪
vm-auto-test vms                                    # 列出 VM
vm-auto-test snapshots --vm "E:\VM-MCP\...vmx"      # 列快照

# 2. Baseline 测试
vm-auto-test run \
  --vm "E:\VM-MCP\windows11\Windows 11 x64.vmx" \
  --mode baseline \
  --snapshot "clean" \
  --sample-command "C:\Samples\sample.exe" \
  --verify-command "hostname" \
  --verify-shell powershell

# 3. 如果 baseline 通过（BASELINE_VALID），继续 AV 测试
vm-auto-test run \
  --vm "E:\VM-MCP\windows11\Windows 11 x64.vmx" \
  --mode av \
  --snapshot "av-installed" \
  --sample-command "C:\Samples\sample.exe" \
  --verify-command "hostname" \
  --verify-shell powershell \
  --baseline-result "reports/20260509-XXXXXX-sample/result.json"
```

### YAML 配置工作流（适合重复执行）

```bash
# 1. 交互生成配置
vm-auto-test init-config --output configs/baseline.yaml --mode baseline

# 2. 执行
vm-auto-test run-config configs/baseline.yaml

# 3. AV 模式
vm-auto-test init-config --output configs/av.yaml --mode av
vm-auto-test run-config configs/av.yaml
```

## 结果判定

| 分类 | 含义 |
|------|------|
| `BASELINE_VALID` | 样本有效：执行前后验证命令输出发生变化 |
| `BASELINE_INVALID` | 样本无效：执行前后验证命令输出无变化 |
| `AV_NOT_BLOCKED` | 杀软未拦截：AV 环境下攻击效果仍发生 |
| `AV_BLOCKED_OR_NO_CHANGE` | 杀软已拦截或未生效 |

## 输出比较策略

默认 `changed`（前后归一化后不同即为有效）。在 YAML 配置中可通过 `comparisons` 使用其他策略：

| 策略 | 用途 | YAML 示例 |
|------|------|-----------|
| `changed` | 归一化后前后不同 | 默认 |
| `contains` | 输出包含指定字符串 | `{type: contains, value: "created"}` |
| `regex` | 输出匹配正则 | `{type: regex, pattern: "Error: \\d+"}` |
| `json_field` | JSON 字段等于预期值 | `{type: json_field, path: "result.status", expected: "ok"}` |
| `file_hash` | 输出 SHA-256 等于预期 | `{type: file_hash, expected: "abc123..."}` |

## 环境配置

### .env 文件

```bash
VMRUN_PATH="C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe"
VMWARE_CREDENTIALS_FILE=credentials.json
VMWARE_HOST=localhost
VMWARE_PORT=8697
```

### credentials.json

Key 为 .vmx 绝对路径：

```json
{
  "E:\\VM-MCP\\windows11\\Windows 11 x64.vmx": {
    "user": "testuser",
    "password": "admin123"
  }
}
```

通过交互菜单 [3] 可管理凭证（添加、验证、重新配置）。Agent 也可直接读写此 JSON 文件。

## 报告结构

```
reports/
└── 20260509-023223-336747-1-A_schtasks/
    ├── result.json          # 汇总结果
    ├── before.txt           # 执行前验证输出
    ├── after.txt            # 执行后验证输出
    ├── sample_stdout.txt    # 样本 stdout
    └── sample_stderr.txt    # 样本 stderr
```

## 故障排查

| 问题 | 原因 | 解决 |
|------|------|------|
| 无法列出快照 / 超时 5s | VM 启用了访问控制加密 | VMware Workstation → 设置 → 选项 → 访问控制 → 移除加密 |
| `VmToolsNotReadyError` | VMware Tools 未安装或未就绪 | 确认 VM 中已安装 VMware Tools 并重启 |
| Guest 认证连续 5 次失败 | 用户名密码错误或用微软在线账户 | 使用本地管理员账户 |
| `vmrun` 命令不可用 | VMRUN_PATH 未配置 | 运行 `vm-auto-test` 进入交互菜单 [5] 配置 |
| AV 模式报错缺 baseline | `--baseline-result` 未指定或路径无效 | 先完成 baseline 测试，填入有效 result.json 路径 |
| CSV 编码报错 | CSV 不是 UTF-8 | 用 Excel 另存为 CSV UTF-8 |

## 安全边界

- 仅用于有授权的本地 VM 实验环境
- 使用隔离网络（Host-only/NAT）
- 每次测试前回滚到明确快照
- 不要在共享或生产环境中运行
- `.env` 和 `credentials.json` 不要提交到 git
- 密码不写入 YAML 配置；用 `password_env` 或交互式输入
