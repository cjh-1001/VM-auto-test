# VM Auto Test

面向本地 VMware Workstation 实验环境的自动化验证框架——回滚快照 → 执行样本 → 采集结果 → 生成报告，串成稳定可重复的测试流程。

**只做自动化执行和结果比对，不生成样本、不提供绕过能力、不尝试规避检测。**

---

## 前置准备

使用本框架前，每台用于测试的 VM 必须完成以下配置。**以下步骤在 VM 内部操作。**

### 1. 关闭访问控制加密

vmrun 无法操作已加密的 VM（无法列出快照、无法执行 Guest 命令）。创建 VM 时不要勾选"加密"，如果已经加密：

1. 先移除**硬件可信平台模块**（TPM）：虚拟机设置 → 选项 → 高级 → 固件类型，临时改为 BIOS 再改回 UEFI 可触发移除 TPM 的选项
2. VMware Workstation → 虚拟机设置 → 选项 → 访问控制 → **移除加密**
3. 创建 VM 时切勿使用随机生成的加密密码，否则事后无法解除

> 如果遇到"无法列出快照"或 vmrun 命令超时，首先检查 VM 是否启用了访问控制加密。

### 2. 安装 VMware Tools

必须安装，否则 vmrun 无法在 Guest 内执行程序、无法检查系统状态。

- VM 菜单 → 虚拟机 → 安装 VMware Tools
- 按向导完成安装后**重启 VM**

### 3. 创建本地管理员账户

**不要使用绑定微软账户的在线账户**——vmrun 的 Guest 认证不支持微软在线账户，必须使用本地账户。

在 VM 中以管理员身份打开 CMD 或 PowerShell，**推荐直接启用内置 Administrator 账户**：

```cmd
net user administrator /active:yes
net user administrator admin123
```

如果不想用内置 Administrator，也可以新建本地管理员：

```cmd
net user testuser admin123 /add
net localgroup Administrators testuser /add
net user testuser /active:yes
```

> **注意：不论用哪种方式，创建或启用账户后都必须登录一次桌面才能激活。** 首次登录后 Windows 才会创建该用户的配置文件目录（`C:\Users\<用户名>\AppData\` 等），否则依赖用户目录的样本（如启动目录 LNK 持久化）会因目录不存在而失败。
>
> 登录方法：VM 中注销当前账户 → 用目标账户登录桌面 → 出现桌面后即可注销，切回原账户。
>
> **强烈建议不要使用微软在线账户登录 VM 桌面。** 微软账户的权限模型与本地账户不同，可能导致 vmrun 执行的脚本出现权限不足、路径解析异常等问题。最佳实践：启用/创建了哪个本地管理员账户，就用那个账户登录桌面进行测试。

记住用户名和密码，后续在框架中配置凭证时需要用到。

### 4. 测试要素

每次测试需要三样东西：

| 要素 | 说明 | 示例 |
|---|---|---|
| 样本 | 待执行的 PE 文件 | `sample.exe` |
| 样本路径 | Guest 中样本存放的路径 | `C:\Samples\sample.exe` |
| 验证命令 | 执行样本前后各跑一次，判定样本是否生效 | `hostname` 或 `schtasks /query` |

验证命令支持 `%VAR%` 环境变量，详见下方 [验证命令中的环境变量](#验证命令中的环境变量)。

---

## 安装

**环境要求：** Windows 宿主机 + VMware Workstation Pro 17+，Python 3.10+，`vmrun.exe` 可用。

```bash
pip install -e .
```

开发依赖：

```bash
pip install -e .[dev]
```

卸载：

```bash
pip uninstall vm-auto-test
```

---

## 快速开始

```bash
vm-auto-test
```

首次运行 VMRUN_PATH 未配置时自动进入环境配置引导，按提示填入 vmrun.exe 路径即可。也可以手动配置：

```bash
copy .env.example .env
# 编辑 .env 填入 VMRUN_PATH=D:\VM2\vmrun.exe
```

```
  —— VM Auto Test ——
  [0] 退出
  [1] 测试单样本
  [2] 测试多样本 (CSV)
  [3] 列出 VM
  [4] 列出快照
  [5] 重新配置环境
```

全程逐步引导。样本默认 `cmd` 执行。

### 配置 Guest 凭证

每个 VM 的凭证独立管理，不放在 `.env` 中：

1. 主菜单选 **[3] 列出 VM**
2. 选择要配置的 VM
3. 选 `[1] 配置凭证`，输入该 VM 的 Guest 用户名密码
4. 保存后自动验证，成功即完成

已配置的 VM 在列表中会标注 `[已配置]`。也可以 `[1] 验证凭证` 测试现有配置是否有效，或 `[2] 重新配置` 修改。

测试时会自动验证凭证：有凭证则先验证，通过即继续；验证失败或无凭证则引导用户配置。

> **注意：所有 Guest 命令（样本执行、验证命令、环境变量展开）都以凭证用户身份运行，与 VM 桌面当前登录的是谁无关。** 如果样本的持久化效果依赖用户目录（如 `%APPDATA%`），影响的是凭证用户的目录，验证命令检查的也应是对应该凭证用户的路径。

### 验证命令中的环境变量

验证命令中可以直接写 `%VAR%`，框架会自动展开：

```cmd
dir "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Updater.lnk"
```

展开流程：
1. 通过 `echo %VAR%`（以凭证用户身份）获取实际值
2. 检测展开后路径中的 `C:\Users\<X>\` 部分
3. 如果 `<X>` 不是凭证用户、不是系统目录（Public/Default），且确认为真实用户目录，则**自动替换为凭证用户名**

> **为什么要替换？** 有些环境中 `echo %APPDATA%` 可能返回其他用户的路径（如微软在线账户残留目录名），直接使用会导致验证命令检查错误路径。替换后保证检查的是凭证用户的实际目录。
>
> 如果你的验证命令不需要展开环境变量，直接写 `C:\Users\testuser\...` 也可以，不会被额外处理。

---

## 测试流程

每次单样本测试按 **5 个阶段** 执行，日志对齐输出：

| 阶段 | 步骤 | 说明 |
|---|---|---|
| 结果 | `create report dir` | 创建报告目录 |
| 回滚快照 | `revert snapshot` | 回滚到指定快照 |
| 验证环境 | `start vm` | 启动 VM |
| | `wait guest ready` | 等待 VMware Tools 就绪 + 验证 Guest 凭证（连续 5 次失败自动终止） |
| 验证攻击效果 | `before verification` | 执行验证命令，获取 baseline |
| 运行恶意脚本 | `run sample` | 在 Guest 中执行测试样本 |
| 验证攻击效果 | `after verification` | 再次执行验证命令，获取结果 |
| | `collect av logs` | 采集 AV 日志 |
| | `evaluate` | 比对前后输出，判定有效/无效 |
| 结果 | `write report` | 生成报告文件 |

### 测试模式

| 模式 | 快照环境 | 作用 |
|---|---|---|
| `baseline` | 干净系统（无杀软） | 先确认样本本身有效 |
| `av` | 装了杀软的系统 | 再看杀软能不能拦截 |

AV 模式必须先有一份已通过的 baseline 报告。

### 结果判定

| 判定结果 | 含义 |
|---|---|
| 有效 / `BASELINE_VALID` | 样本跑完后验证输出变了，样本有效 |
| 无效 / `BASELINE_INVALID` | 验证输出无变化，样本无效 |
| 未拦截 / `AV_NOT_BLOCKED` | 杀软没拦住，攻击效果发生 |
| 已拦截 / `AV_BLOCKED_OR_NO_CHANGE` | 杀软拦截或未生效 |

### CSV 批量测试

Excel 编辑，保存为 **CSV UTF-8**，3 列：

| 列 | 含义 | 示例 |
|---|---|---|
| `sample_file` | 样本文件名（或绝对路径） | `sample.exe` |
| `verify_command` | 验证命令 | `dir "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Updater.lnk"` |
| `verify_shell` | 验证命令的 shell（`cmd` / `powershell`） | `cmd` |

交互菜单会引导输入样本所在目录，框架自动拼接完整路径；写绝对路径也可以。验证命令同样支持 `%VAR%` 环境变量自动展开。

### 报告目录

```
reports/
└── 20260509-023223-336747-1-A_schtasks/
    ├── result.json
    ├── before.txt
    ├── after.txt
    ├── sample_stdout.txt
    └── sample_stderr.txt
```

| 文件 | 内容 |
|---|---|
| `result.json` | 汇总：判定结果、分类、所有步骤记录、验证输出比对详情 |
| `before.txt` | 样本执行前验证命令的 stdout+stderr 输出 |
| `after.txt` | 样本执行后验证命令的 stdout+stderr 输出 |
| `sample_stdout.txt` | 样本自身的标准输出 |
| `sample_stderr.txt` | 样本自身的标准错误输出 |

批量测试时每个样本独立子目录：`reports/<timestamp>-batch/samples/<样本ID>/`。

---

## 输出比较策略

不配置时默认用 `changed`（前后归一化后不同即为有效）：

| 策略 | 作用 | 配置示例 |
|---|---|---|
| `changed` | 归一化后前后不同 | 默认 |
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

---

## 命令行参考

| 命令 | 用途 |
|---|---|
| `vm-auto-test` | 交互菜单 |
| `vm-auto-test run --vm ... --sample ... --verify ...` | 单样本测试 |
| `vm-auto-test run-csv --csv ...` | CSV 批量测试 |
| `vm-auto-test run-dir --dir ...` | 扫描目录批量测试 |
| `vm-auto-test vms` | 列出运行中的 VM |
| `vm-auto-test snapshots --vm ...` | 列出快照 |

---

## 配置文件

### .env

```bash
VMRUN_PATH=D:\VM2\vmrun.exe
VMWARE_CREDENTIALS_FILE=credentials.json   # 可选，默认 credentials.json
VMWARE_HOST=localhost                       # vmrest，可选
VMWARE_PORT=8697                            # vmrest，可选
```

### credentials.json

Key 为 `.vmx` 文件的绝对路径，避免同名 VM 冲突：

```json
{
  "E:\\VM-MCP\\windows11\\Windows 11 x64.vmx": {
    "user": "testuser",
    "password": "admin123"
  },
  "D:\\VM2\\Win10\\Win10.vmx": {
    "user": "admin",
    "password": "pass456"
  }
}
```

通过主菜单 [3] 交互式添加、验证、重新配置。

---

## 开发

### 测试

```bash
pytest
python -m compileall -q src tests
```

当前 88 个测试，使用 `fake provider`，不需要真实 VMware 环境。

真实 `VMware smoke test`（无害链路检查：列快照、启动、等 Tools、跑 `hostname`）：

```bash
vm-auto-test-smoke
```

### Provider

| provider | 状态 |
|---|---|
| `vmrun` | 已实现，默认 |
| `vsphere` / `powercli` / `mcp` | 占位，待后续实现 |

### 项目结构

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

---

## 安全边界

- 仅用于你拥有授权的本地虚拟机实验环境
- 使用隔离网络或 Host-only/NAT
- 每次测试前回滚到明确快照
- 不在生产主机或共享环境中运行未知样本
- `.env` 和 `credentials.json` 不要提交到 git
