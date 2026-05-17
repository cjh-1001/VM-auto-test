# VM Auto Test — 接口文档

## 项目概览

该项目是一个 VMware 虚拟机自动化测试框架，用于在隔离的 Windows 虚拟机中执行恶意样本并判定其攻击效果。项目包含两个核心包：

| 包 | 用途 |
|---|---|
| `vm_auto_test` | 样本验证测试框架（核心业务逻辑） |
| `vmware_mcp` | MCP Server，封装 vmrun/vmcli/VMware REST API |

---

## 一、CLI 入口 (`vm-auto-test`)

入口文件: `src/vm_auto_test/cli.py:1184`

```bash
vm-auto-test [--env-file .env] [command] [options]
```

### 1.1 子命令

| 命令 | 说明 |
|---|---|
| `vms` | 列出运行中的 VM |
| `snapshots --vm <id>` | 列出 VM 快照 |
| `run` | 运行单样本测试 |
| `run-dir` | 批量测试目录下所有样本 |
| `run-csv` | 从 CSV 批量测试 |
| `run-config` | 从 YAML 配置运行 |
| `init-config` | 交互式创建 YAML 配置 |
| `report` | 从已有 JSON 重新生成 HTML/JSON 报告 |
| `doctor` | 检查本地环境配置 |

### 1.2 `run` 命令参数

```
vm-auto-test run
  --vm <vmx路径>              (必填) VM ID 或 .vmx 路径
  --mode <baseline|av|av-analyze> (必填) 测试模式
  --snapshot <快照名>         快照名称
  --sample-command <命令>     (必填) 执行样本的 guest 命令
  --sample-shell <cmd|powershell>   (默认: cmd)
  --verify-command <命令>     (必填) 验证命令 (样本前后各执行一次)
  --verify-shell <cmd|powershell>   (默认: powershell)
  --guest-user <用户名>       Guest 凭据
  --guest-password <密码>     Guest 密码
  --baseline-result <路径>    基准报告路径
  --capture-screenshot        截图标志
  --reports-dir <目录>        (默认: reports)
```

### 1.3 `run-dir` / `run-csv` / `run-config` 额外参数

| 命令 | 额外参数 |
|---|---|
| `run-dir` | `--dir` 样本目录, `--pattern` 文件匹配 (默认 `*.exe,*.bat,*.ps1,*.cmd`) |
| `run-csv` | `--csv` CSV文件路径, `--samples-base-dir` VM上样本基础目录 |
| `run-config` | 位置参数: YAML 配置文件路径, `--guest-password` |

---

## 二、数据模型 (Models)

文件: `src/vm_auto_test/models.py`

### 2.1 枚举

```python
class TestMode(str, Enum):
    BASELINE = "baseline"       # 干净快照，验证样本是否有效
    AV = "av"                   # 带杀软快照，验证杀软能否拦截
    AV_ANALYZE = "av_analyze"   # 截图+日志+AI分析判定杀软拦截

class Classification(str, Enum):
    BASELINE_VALID              = "BASELINE_VALID"              # 样本有效
    BASELINE_INVALID            = "BASELINE_INVALID"             # 样本无效
    AV_NOT_BLOCKED              = "AV_NOT_BLOCKED"               # 杀软未拦截
    AV_BLOCKED_OR_NO_CHANGE     = "AV_BLOCKED_OR_NO_CHANGE"     # 杀软已拦截
    AV_ANALYZE_BLOCKED          = "AV_ANALYZE_BLOCKED"          # 日志/截图有变化，已拦截
    AV_ANALYZE_NOT_BLOCKED      = "AV_ANALYZE_NOT_BLOCKED"      # 日志/截图无变化，未拦截

class Shell(str, Enum):
    CMD = "cmd"
    POWERSHELL = "powershell"

class ComparisonKind(str, Enum):
    CHANGED     = "changed"       # 前后输出是否变化
    CONTAINS    = "contains"      # 输出是否包含指定字符串
    REGEX       = "regex"         # 输出是否匹配正则
    JSON_FIELD  = "json_field"    # JSON 字段值比较
    FILE_HASH   = "file_hash"     # 文件哈希比较
```

### 2.2 核心数据结构 (均为 frozen dataclass)

```python
@dataclass(frozen=True)
class GuestCredentials:
    user: str
    password: str

@dataclass(frozen=True)
class CommandResult:
    command: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    capture_method: str = "direct"
    # 属性: combined_output -> str  (stdout + stderr 拼接)

@dataclass(frozen=True)
class ComparisonSpec:
    kind: ComparisonKind
    target: Literal["before", "after"] = "after"
    value: str | None = None        # CONTAINS 模式的目标字符串
    pattern: str | None = None      # REGEX 模式的正则表达式
    path: str | None = None         # JSON_FIELD 模式的字段路径
    expected: Any | None = None     # JSON_FIELD / FILE_HASH 期望值

@dataclass(frozen=True)
class ComparisonResult:
    kind: ComparisonKind
    passed: bool
    detail: str = ""
    before_value: str | None = None
    after_value: str | None = None

@dataclass(frozen=True)
class EvaluationResult:
    changed: bool                                    # before/after 输出是否不同
    effect_observed: bool                            # 所有 comparison 是否通过
    comparisons: tuple[ComparisonResult, ...] = ()

@dataclass(frozen=True)
class VerificationSpec:
    command: str
    shell: Shell = Shell.POWERSHELL
    comparisons: tuple[ComparisonSpec, ...] = ()     # 空则默认 CHANGED 比较

@dataclass(frozen=True)
class SampleSpec:
    id: str                            # 1-64 字符, 不含 / 和 \
    command: str
    shell: Shell = Shell.CMD
    verification: VerificationSpec | None = None

@dataclass(frozen=True)
class AvLogCollectorSpec:
    id: str
    type: str          # 目前仅支持 "guest_command"
    command: str
    shell: Shell = Shell.POWERSHELL

@dataclass(frozen=True)
class CollectedLog:
    collector_id: str
    command: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    capture_method: str = "direct"

@dataclass(frozen=True)
class AvAnalyzeSpec:                         # AV 分析模式配置
    log_collect_shell: Shell = Shell.POWERSHELL
    log_sources: tuple[AvLogSourceSpec, ...] = ()
    log_collect_command: str = ""
    log_export_preset: str = ""
    api_key_env: str = ""
    analyzer_command: str = ""
    enable_image_compare: bool = False       # 启用像素级截图对比
    image_compare_threshold: float = 5.0     # 差异像素百分比阈值

@dataclass(frozen=True)
class AvAnalyzeResult:                       # AV 分析结果
    log_found: bool = False                  # 日志是否有变化
    log_detail: str = ""                     # 日志分析详情
    screenshot_analysis: str | None = None   # AI 截图分析文本
    classification: Classification = Classification.AV_ANALYZE_NOT_BLOCKED

@dataclass
class DeferredImageResult:                   # 后台截图对比结果容器 (mutable)
    value: AvAnalyzeResult | None = None

@dataclass(frozen=True)
class TestCase:                              # 测试输入
    vm_id: str
    snapshot: str | None
    mode: TestMode
    sample_command: str                      # 单样本时的命令
    verify_command: str                      # 验证命令
    credentials: GuestCredentials
    verify_shell: Shell = Shell.POWERSHELL
    sample_shell: Shell = Shell.CMD
    baseline_result: str | None = None
    wait_timeout_seconds: int = 180
    command_timeout_seconds: int = 120
    normalize_trim: bool = True
    normalize_ignore_empty_lines: bool = True
    samples: tuple[SampleSpec, ...] = ()         # 批量样本模式
    verification: VerificationSpec | None = None
    av_log_collectors: tuple[AvLogCollectorSpec, ...] = ()
    capture_screenshot: bool = False
    av_analyze: AvAnalyzeSpec | None = None      # av_analyze 模式配置
    normalize_ignore_patterns: tuple[str, ...] = ()
    # 方法: effective_samples() -> tuple[SampleSpec, ...]
    # 方法: effective_verification() -> VerificationSpec

@dataclass(frozen=True)
class StepResult:
    name: str
    status: str      # "started" | "passed" | "failed"
    detail: str = ""
    stage: str = ""

@dataclass(frozen=True)
class TestResult:                            # 单样本测试输出
    test_case: TestCase
    report_dir: str
    before: CommandResult                    # 样本执行前
    sample: CommandResult                    # 样本执行
    after: CommandResult                     # 样本执行后
    changed: bool
    classification: Classification
    steps: tuple[StepResult, ...] = ()
    evaluation: EvaluationResult | None = None
    logs: tuple[CollectedLog, ...] = ()
    av_analyze_result: AvAnalyzeResult | None = None
    image_compare_result: DeferredImageResult | None = None

@dataclass(frozen=True)
class SampleTestResult:                     # 批量中的单样本结果
    test_case: TestCase
    sample_spec: SampleSpec
    report_dir: str
    before: CommandResult
    sample: CommandResult
    after: CommandResult
    evaluation: EvaluationResult
    classification: Classification
    steps: tuple[StepResult, ...] = ()
    logs: tuple[CollectedLog, ...] = ()
    duration_seconds: float = 0.0
    av_analyze_result: AvAnalyzeResult | None = None
    image_compare_result: DeferredImageResult | None = None
    # 属性: changed -> bool (evaluation.changed 别名)

@dataclass(frozen=True)
class BatchTestResult:                      # 批量测试总结果
    test_case: TestCase
    report_dir: str
    samples: tuple[SampleTestResult, ...]
    classification: Classification
    steps: tuple[StepResult, ...] = ()
    duration_seconds: float = 0.0
```

---

## 三、TestOrchestrator (编排器)

文件: `src/vm_auto_test/orchestrator.py:47`

```python
class TestOrchestrator:
    def __init__(
        self,
        provider: VmwareProvider,
        report_base_dir: Path,
        progress: Callable[[StepResult], None] | None = None,
    ) -> None

    # 列出 VM 快照
    async def list_snapshots(self, vm_id: str) -> list[str]

    # 运行单样本测试 → TestResult
    async def run(self, test_case: TestCase) -> TestResult

    # 运行批量测试 → BatchTestResult
    async def run_batch(self, test_case: TestCase) -> BatchTestResult
```

**测试流程** (baseline/av):

```
create_report_dir → revert_snapshot → start_vm → wait_guest_ready
→ [detect_av] → before_verification → run_sample → after_verification
→ [capture_screenshot] → [collect_av_logs] → evaluate → write_report
```

**测试流程** (av_analyze):

```
create_report_dir → revert_snapshot → start_vm → wait_guest_ready
→ detect_av → screenshot(before) → collect_logs(before) → run_sample
→ screenshot(after) → collect_logs(after)
→ 双轨判定: log_analysis (主) + image_compare (后台并行) → combined_verdict
→ write_report
```

`run_batch` 对每个 sample 重复执行 `run_single_sample`，最后等待所有后台图片对比任务完成后回填分类。

---

## 四、VmwareProvider (抽象接口)

文件: `src/vm_auto_test/providers/base.py:13`

```python
class VmToolsNotReadyError(Exception): ...

class VmwareProvider(ABC):
    @abstractmethod
    async def list_running_vms(self) -> list[str]: ...

    @abstractmethod
    async def list_snapshots(self, vm_id: str) -> list[str]: ...

    @abstractmethod
    async def revert_snapshot(self, vm_id: str, snapshot: str) -> None: ...

    @abstractmethod
    async def start_vm(self, vm_id: str) -> None: ...

    @abstractmethod
    async def reset_vm(self, vm_id: str) -> None: ...

    @abstractmethod
    async def verify_guest_credentials(
        self, vm_id: str, credentials: GuestCredentials
    ) -> str: ...  # 返回 "ok" 或抛异常

    @abstractmethod
    async def wait_guest_ready(
        self, vm_id: str, credentials: GuestCredentials,
        timeout_seconds: int,
        progress: Callable[[StepResult], None] | None = None,
    ) -> None: ...

    @abstractmethod
    async def run_guest_command(
        self, vm_id: str, command: str, shell: Shell,
        credentials: GuestCredentials, timeout_seconds: int,
        progress: Callable[[StepResult], None] | None = None,
    ) -> CommandResult: ...

    @abstractmethod
    async def capture_screen(
        self, vm_id: str, output_path: str,
        credentials: GuestCredentials,
    ) -> str: ...  # 返回输出路径
```

### 4.1 Provider 工厂

```python
# src/vm_auto_test/providers/factory.py:10
def create_provider(provider_type: str = "vmrun") -> VmwareProvider
```

目前只实现了 `"vmrun"` 类型。`"vsphere"`, `"powercli"`, `"mcp"` 为占位符，调用会抛出 `NotImplementedError`。

### 4.2 VmrunProvider (vmrun 实现)

文件: `src/vm_auto_test/providers/vmrun_provider.py:70`

使用 `vmware_mcp.vmrun.VMRun` 作为底层调用。支持：
- 通过 `vmrun createTempfileInGuest` 在 guest 中创建临时文件
- 通过临时文件传递脚本实现 stdout/stderr/exit_code 捕获
- 支持 cmd 和 powershell 两种 shell 包装器
- 输出自动检测编码: UTF-8-SIG → UTF-8 → GBK → Shift_JIS

---

## 五、配置系统

文件: `src/vm_auto_test/config.py`

### 5.1 YAML 配置结构

```yaml
vm_id: "E:\\VM\\win11\\Windows 11 x64.vmx"
snapshot: "Clean"
mode: baseline                    # baseline | av | av_analyze
guest:
  user: Administrator
  password_env: VMWARE_GUEST_PASSWORD  # 环境变量名 (默认)
  password: "plaintext"           # 或直接填写
sample:                           # 单样本 (与 samples 互斥)
  command: "C:\\Samples\\test.exe"
  shell: cmd
verification:                     # 默认验证规则
  command: "type C:\\marker.txt"
  shell: powershell
  comparisons:
    - type: changed               # changed|contains|regex|json_field|file_hash
      target: after               # before|after
    - type: contains
      value: "SUCCESS"
samples:                          # 批量样本 (与 sample 互斥)
  - id: sample1
    command: "C:\\Samples\\a.exe"
    shell: cmd
    verification:
      command: "dir C:\\Temp"
      shell: cmd
      comparisons:
        - type: regex
          pattern: "\\d+ file"
reports_dir: reports
timeouts:
  wait_guest_seconds: 180
  command_seconds: 120
normalize:
  trim: true
  ignore_empty_lines: true
av_logs:                          # 可选：采集杀软日志
  collectors:
    - id: defender
      type: guest_command
      command: "Get-MpThreatDetection | ConvertTo-Json"
      shell: powershell
av_analyze:                        # 仅 av_analyze 模式
  log_collect_shell: powershell
  log_sources:                      # 可选，留空自动检测
    - guest_path: "C:\\Users\\{username}\\AppData\\..."
      description: "360 主数据库"
  log_collect_command: ""           # 可选，日志收集前置脚本
  log_export_preset: ""            # 可选，360|huorong|tencent
  api_key_env: ANTHROPIC_API_KEY   # 可选，启用 AI 分析
  enable_image_compare: true       # 可选，像素级截图对比（无 AI 时）
  image_compare_threshold: 5.0     # 可选，阈值百分比
provider:
  type: vmrun
baseline_result: null
```

### 5.2 关键函数

```python
def load_config(path: Path) -> TestConfig
def write_config(path: Path, config: TestConfig) -> None
def parse_config(data: dict) -> TestConfig
def to_test_case(config: TestConfig, password: str | None = None) -> TestCase
def scan_samples_from_directory(directory: Path, globs=(...)) -> tuple[SampleConfig, ...]
def parse_csv_samples(csv_path: Path, samples_base_dir: str | None = None) -> tuple[SampleConfig, ...]
```

### 5.3 CSV 格式

```csv
sample_file,verify_command,verify_shell
C:\Samples\a.exe,type C:\marker.txt,cmd
C:\Samples\b.exe,dir C:\Users,powershell
```

- 首行列名可选（以 `sample` 开头则跳过）
- baseline/av 模式: 3 列必填，路径非绝对时需 `--samples-base-dir`
- av_analyze 模式: 仅需 1 列 `sample_file`（无需验证命令）
- 编码: UTF-8 BOM → UTF-8 → GBK

---

## 六、评估器 (Evaluator)

文件: `src/vm_auto_test/evaluator.py`

```python
def normalize_output(value: str, test_case: TestCase) -> str
    # \r\n → \n 统一换行 + trim + 去空行

def output_hash(value: str) -> str
    # SHA-256

def evaluate_output(
    before: CommandResult, after: CommandResult,
    verification: VerificationSpec, test_case: TestCase,
) -> EvaluationResult

def classify_result(effect_observed: bool, mode: TestMode) -> Classification
```

**判定逻辑**:

| 模式 | effect_observed=True / 已拦截 | effect_observed=False / 未拦截 |
|---|---|---|---|
| BASELINE | `BASELINE_VALID` | `BASELINE_INVALID` |
| AV | `AV_NOT_BLOCKED` | `AV_BLOCKED_OR_NO_CHANGE` |
| AV_ANALYZE | `AV_ANALYZE_BLOCKED` (日志或截图有变化) | `AV_ANALYZE_NOT_BLOCKED` |

---

## 七、AI 分析与截图对比 (Analysis)

文件: `src/vm_auto_test/analysis.py`

```python
def compare_screenshots(
    before_path: Path, after_path: Path, threshold: float = 5.0,
) -> tuple[bool, float, str]
    # 使用 Pillow 逐像素对比两张截图
    # 返回 (差异显著, 差异百分比, 详情描述)
    # 自动处理 VMware 渲染导致的微小尺寸差异（裁剪重叠区域）

async def run_analysis(
    config: AvAnalyzeSpec, log_content: str,
    before_screenshot: Path, after_screenshot: Path,
) -> AvAnalyzeResult
    # 根据配置选择分析方式：
    #   - AI 分析 (Anthropic API): 同时分析日志文本和截图差异
    #   - 本地分析: 日志关键字匹配 + 像素截图对比
    # AI 分析结果优先于本地分析

def has_analyzer_cli(config: AvAnalyzeSpec) -> bool
    # 检查是否配置了 AI 分析（api_key_env 或 analyzer_command）
```

**内置日志分析** (`_analyze_logs_builtin`): 在导出后的文本日志中搜索威胁相关关键词，包括：`Trojan`, `Backdoor`, `Malware`, `Worm`, `Virus`, `Ransom`, `木马`, `病毒`, `拦截`, `查杀`, `隔离`, 以及厂商特定关键词（360: `云查杀`、`QVM`；火绒: `HipsMain`、`文件实时监控`；腾讯: `Win32.Trojan`、`TfAvCenter`）。

---

## 八、报告系统

文件: `src/vm_auto_test/reporting.py`

```python
def create_report_dir(base_dir: Path, test_id: str) -> Path
    # 生成时间戳目录: <base>/<YYYYmmdd-HHMMSS-μs>-<test_id>

def write_report(result: TestResult) -> None
    # 写入 result.json + before.txt + after.txt + sample_stdout.txt + sample_stderr.txt

def write_batch_report(result: BatchTestResult) -> None
    # 写入 result.json + result.csv + result.html
    # 批量中每个样本独立子目录: samples/<sample_id>/result.json + ...

def batch_classification(
    sample_classifications: tuple[Classification, ...]
) -> Classification
    # baseline: 全部 VALID → VALID, 否则 INVALID
    # av: 任一 NOT_BLOCKED → NOT_BLOCKED, 否则 BLOCKED_OR_NO_CHANGE
    # av_analyze: 任一 NOT_BLOCKED → NOT_BLOCKED, 否则 BLOCKED

def load_baseline_is_valid(path: str) -> bool
    # 解析已有报告，判断 baseline 是否有效

def write_batch_html_from_json(
    batch_json_path: Path, output_html_path: Path | None = None,
) -> None
    # 从批量 JSON 重建 BatchTestResult 并生成交互式 HTML
    # 自动读取 per-sample result.json + before.txt + after.txt
```

### 7.1 报告文件结构

**单样本 (baseline/av)**:
```
reports/20260512-143022-567890-sample/
├── result.json          # TestResult JSON (schema v1)
├── before.txt           # before 验证输出
├── after.txt            # after 验证输出
├── sample_stdout.txt    # 样本执行 stdout
├── sample_stderr.txt    # 样本执行 stderr
├── screenshot.png       # (optional) VM 截图
├── test.log             # 测试步骤日志
└── av_logs/             # (optional) 杀软日志
    └── <collector_id>_stdout.txt
```

**单样本 (av_analyze)**:
```
reports/20260512-143022-567890-sample/
├── result.json          # schema v1
├── before.txt           # 日志收集(before) 导出文本
├── after.txt            # 日志收集(after) 导出文本
├── screenshot_before.png
├── screenshot_after.png
├── test.log
└── av_logs/
    ├── before/          # 原始 SQLite 日志 before
    └── after/           # 原始 SQLite 日志 after + 导出文本
```

**批量模式**:
```
reports/...batch/
├── result.json          # BatchTestResult (schema v2)
├── result.csv           # UTF-8 BOM CSV
├── result.html          # 交互式 HTML 报告
├── test.log
└── samples/<sample_id>/
    ├── result.json
    ├── before.txt / after.txt
    ├── sample_stdout.txt / sample_stderr.txt  # baseline/av
    ├── screenshot.png                          # baseline/av
    ├── screenshot_before.png / screenshot_after.png  # av_analyze
    └── av_logs/                                # av_analyze
```

### 7.2 `result.json` 结构 (schema v2, 批量)

```json
{
  "schema_version": 2,
  "mode": "av",
  "vm_id": "E:\\VM\\win11\\Windows 11 x64.vmx",
  "snapshot": "WithAV",
  "baseline_result": null,
  "summary": {
    "total": 3,
    "classification_counts": {
      "AV_BLOCKED_OR_NO_CHANGE": 2,
      "AV_NOT_BLOCKED": 1
    },
    "overall_classification": "AV_NOT_BLOCKED",
    "duration_seconds": 142.5
  },
  "samples": [
    {
      "id": "sample1",
      "classification": "AV_BLOCKED_OR_NO_CHANGE",
      "changed": false,
      "effect_observed": false,
      "sample_command": "C:\\Samples\\a.exe",
      "report_dir": "samples/sample1",
      "steps": [...],
      "duration_seconds": 35.2,
      "av_analyze_result": {
        "log_found": false,
        "log_detail": "日志无变化",
        "screenshot_analysis": null,
        "classification": "AV_ANALYZE_NOT_BLOCKED"
      },
      "image_compare_result": {
        "log_found": true,
        "log_detail": "检测到 12.3% 像素差异",
        "screenshot_analysis": "检测到弹窗",
        "classification": "AV_ANALYZE_BLOCKED"
      }
    }
  ],
  "steps": [...]
}
```

---

## 九、环境变量与凭据

文件: `src/vm_auto_test/env.py`

| 环境变量 | 说明 |
|---|---|
| `VMRUN_PATH` | vmrun.exe 路径 |
| `VMWARE_HOST` | VMware REST API 主机 (默认 localhost) |
| `VMWARE_PORT` | VMware REST API 端口 (默认 8697) |
| `VMWARE_CREDENTIALS_FILE` | 凭据文件路径 (默认 credentials.json) |
| `VMWARE_GUEST_PASSWORD` | Guest 密码 (YAML 配置中 `password_env` 的默认值) |
| `VM_AUTO_TEST_SMOKE_VM_ID` | 冒烟测试 VM ID |

### 9.1 `credentials.json` 结构

```json
{
  "E:\\VM\\win11\\Windows 11 x64.vmx": {
    "user": "Administrator",
    "password": "mypassword"
  }
}
```

### 9.2 关键函数

```python
def load_env_file(path: Path, *, override: bool = False) -> None
def load_optional_env_file(path: Path | None) -> None
def is_env_configured() -> bool
def resolve_guest_credentials(vm_id: str) -> GuestCredentials | None
def load_credentials_store() -> dict[str, dict[str, str]]
def upsert_vm_credentials(vm_id: str, user: str, password: str) -> None
def remove_vm_credentials(vm_id: str) -> bool
```

---

## 十、AV 检测

文件: `src/vm_auto_test/av_detection.py`

```python
def build_detection_command() -> str
    # 生成 PowerShell 脚本，通过 tasklist 检测进程名

def parse_detection_result(stdout: str) -> str | None
    # "NONE" → None, 否则返回 AV 名称字符串
```

**内置 AV 签名库**:

| AV | 关键进程 |
|---|---|
| 腾讯电脑管家 | `QQPCTray.exe` |
| 360安全卫士 | `360Tray.exe`, `ZhuDongFangYu.exe` |
| 火绒安全软件 | `HipsDaemon.exe`, `HipsMain.exe`, `HipsTrat.exe` |

---

## 十一、AV 日志采集

文件: `src/vm_auto_test/av_logs.py`

```python
async def collect_av_logs(
    provider: VmwareProvider, test_case: TestCase,
) -> tuple[CollectedLog, ...]
```

通过 `test_case.av_log_collectors` 中定义的命令在 guest 中执行并采集输出。

---

## 十二、冒烟测试 (`vm-auto-test-smoke`)

文件: `src/vm_auto_test/smoke.py:18`

```bash
vm-auto-test-smoke
```

前提条件:
- `VM_AUTO_TEST_SMOKE_VM_ID` 已设置
- 凭据已在 `credentials.json` 中配置
- 可选: `VM_AUTO_TEST_SMOKE_SNAPSHOT` 指定快照

执行: `list_snapshots → revert_snapshot → start_vm → wait_guest_ready → hostname`

---

## 十三、MCP Server 接口

入口文件: `src/vmware_mcp/server.py:517`
CLI 命令: `vmware-mcp` (stdio 协议)

环境变量: `VMWARE_HOST`, `VMWARE_PORT`, `VMWARE_USERNAME`, `VMWARE_PASSWORD`

### 12.1 工具分类统计

| 分类 | 数量 | 说明 |
|---|---|---|
| REST API (VMware Workstation REST) | 17 | VM 管理、电源、网卡、共享文件夹、网络 |
| VMRun | 41 | 电源、快照、文件操作、进程、共享文件夹、设备、变量、截图、网络 |
| VMCli | 42 | 快照、Guest 操作、MKS、Chipset、Tools、模板、磁盘、Config、电源、Ethernet、共享文件夹、Serial、SATA、NVMe、VProbes |

### 12.2 工具命名约定

| 前缀 | 后端 |
|---|---|
| `vm_*` | VMware REST API |
| `vmrun_*` | vmrun.exe 命令行 |
| 其他 (`snapshot_*`, `guest_*`, `power_*`, `disk_*` 等) | vmcli.exe 命令行 |

### 12.3 核心 VMRun 方法

文件: `src/vmware_mcp/vmrun.py`

```python
class VMRun:
    def __init__(self, vmrun_path: str | None = None)
    # 路径优先级: 构造函数参数 > VMRUN_PATH 环境变量 > 默认路径

    # 电源
    async def start(vmx_path, gui=True) -> str
    async def stop(vmx_path, hard=False) -> str
    async def reset(vmx_path, hard=False) -> str
    async def suspend(vmx_path, hard=False) -> str
    async def pause / unpause(vmx_path) -> str

    # 快照
    async def list_snapshots(vmx_path, show_tree=False) -> str
    async def snapshot(vmx_path, name) -> str
    async def delete_snapshot(vmx_path, name, delete_children=False) -> str
    async def revert_to_snapshot(vmx_path, name) -> str

    # Guest 文件操作 (user/password 可选)
    async def file_exists / directory_exists(vmx_path, path, user, password) -> str
    async def list_directory / create_directory / delete_directory(...) -> str
    async def delete_file / rename_file(...) -> str
    async def copy_to_guest(vmx, host_path, guest_path, user, password) -> str
    async def copy_from_guest(vmx, guest_path, host_path, user, password) -> str
    async def create_temp_file(vmx, user, password) -> str

    # Guest 进程
    async def run_program / run_program_in_guest(vmx, program, [args], ...) -> str
    async def list_processes(vmx, user, password) -> str
    async def kill_process(vmx, pid, user, password) -> str

    # 其他
    async def capture_screen(vmx, output_path, user, password) -> str
    async def check_tools_state(vmx) -> str
    async def get_guest_ip(vmx, wait=False) -> str
```

### 12.4 VMware REST API Client

文件: `src/vmware_mcp/client.py:7`

```python
class VMwareClient:
    def __init__(self, host="localhost", port=8697, username="", password="")
    # base_url = http://{host}:{port}/api

    async def list_vms() -> list[dict]
    async def get_vm(vm_id) -> dict
    async def create_vm(vm_id, name) -> dict
    async def delete_vm(vm_id) -> None
    async def update_vm(vm_id, settings) -> dict
    async def get_power_state(vm_id) -> dict
    async def change_power_state(vm_id, state) -> dict
    async def list_nics(vm_id) -> list[dict]
    async def create_nic / delete_nic(vm_id, index) -> ...
    async def get_vm_ip(vm_id) -> dict
    async def list_shared_folders(vm_id) -> list[dict]
    async def create_shared_folder / delete_shared_folder(...) -> ...
    async def list_networks() -> list[dict]
    async def create_network(network_config) -> dict
    async def get_portforwards(vmnet) -> list[dict]
    async def update_portforward(vmnet, protocol, port, config) -> dict
    async def delete_portforward(vmnet, protocol, port) -> None
```

---

## 十四、Progress 回调协议

```python
ProgressCallback = Callable[[StepResult], None]
```

`StepResult` 字段:
- `name`: 步骤名 (如 `"revert_snapshot"`, `"before_verification"`)
- `status`: `"started"` | `"passed"` | `"failed"`
- `detail`: 详情文本
- `stage`: 阶段名 (如 `"回滚快照"`, `"验证攻击效果"`)

---

## 十五、扩展指南

### 新增 Provider

1. 继承 `VmwareProvider` 实现所有抽象方法
2. 在 `factory.py` 的 `create_provider` 中注册

### 新增 AV 签名

在 `av_detection.py:14` 的 `AV_SIGNATURES` 元组中添加新的 `AvSignature`.

### 新增 ComparisonKind

1. 在 `models.py` 的 `ComparisonKind` 枚举中添加
2. 在 `evaluator.py:_evaluate_comparison()` 中实现判定逻辑

---

## 十六、依赖

```toml
# pyproject.toml
[project]
name = "vm-auto-test"
requires-python = ">=3.10"
dependencies = [
    "mcp>=1.0.0",
    "httpx>=0.27.0",
    "PyYAML>=6.0",
    "Pillow>=10.0",
]

[project.scripts]
vmware-mcp = "vmware_mcp.server:main"
vm-auto-test = "vm_auto_test.cli:main"
vm-auto-test-smoke = "vm_auto_test.smoke:main"
```
