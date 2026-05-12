# VM Auto Test

Use this skill when working on the local `vm-auto-test` project or helping the user operate it. The project automates authorized VMware Workstation lab validation:

```text
revert snapshot -> start VM -> wait for VMware Tools -> detect AV when available -> verify before -> run sample -> verify after -> collect configured logs -> write report
```

It only automates execution, observation, comparison, and reporting. Do not use it to generate samples, bypass AV/EDR, evade detection, establish persistence, move laterally, escalate privileges, or run outside an authorized local lab.

## Safety boundary

Before any real VMware run, keep these boundaries intact:

- Authorized local VMware Workstation lab only.
- Prefer Host-only or NAT networking.
- Use a known rollback snapshot before executing any sample.
- Do not run unknown samples on production hosts, shared systems, or non-owned VMs.
- Do not print, summarize, or expose passwords from `.env`, YAML files, or `credentials.json`.
- Treat `credentials.json`, YAML `guest.password`, reports, and AV logs as sensitive local artifacts.
- Prefer `guest.password_env` in YAML; keep credential files out of version control.
- Refuse requests for bypass, stealth, obfuscation, anti-analysis, persistence, privilege escalation, payload generation, or evasion guidance.

## Source of truth

Check current code before changing this skill:

| Area | File |
|---|---|
| CLI args, interactive flow, `config validate`, `report`, and `run --config` | `src/vm_auto_test/cli.py` |
| YAML/CSV schema and sample scanning | `src/vm_auto_test/config.py` |
| Execution orchestration | `src/vm_auto_test/orchestrator.py` |
| Report artifacts and schemas | `src/vm_auto_test/reporting.py` |
| Console scripts | `pyproject.toml` |
| User-facing docs | `README.md` |

Console scripts:

- `vm-auto-test`
- `vm-auto-test-smoke`
- `vmware-mcp`

## Command selection

| User intent | Command |
|---|---|
| First-time setup or guided use | `vm-auto-test` |
| Configure `VMRUN_PATH` | `vm-auto-test` -> `[5] 重新配置环境` |
| Configure/verify VM credentials | `vm-auto-test` -> `[3] 列出 VM` |
| List running VMs | `vm-auto-test vms` |
| List snapshots | `vm-auto-test snapshots --vm "<vmx path>"` |
| Diagnose local CLI environment | `vm-auto-test doctor [--config <yaml>]` |
| Single sample | `vm-auto-test run ...` |
| Batch from host-scanned directory | `vm-auto-test run-dir ...` |
| Batch from CSV with per-sample verification | `vm-auto-test run-csv ...` |
| Create YAML config | `vm-auto-test init-config ...` |
| Validate YAML config | `vm-auto-test config validate --config <yaml>` |
| Run YAML config | `vm-auto-test run --config <yaml>` preferred; `run-config <yaml>` kept for compatibility |
| Standalone report from existing JSON | `vm-auto-test report --input <result.json> --output <file>` |
| Real VMware connectivity smoke test | `vm-auto-test-smoke` only when explicitly requested |

If details are incomplete, prefer the interactive menu instead of guessing paths, credentials, snapshots, or verification commands.

`run --config` is the recommended config-driven entrypoint. Do not mix it with direct `run` flags like `--vm`, `--mode`, `--sample-command`, or `--reports-dir`; the CLI rejects mixed usage.

Before a non-interactive real run, prefer `vm-auto-test doctor --config <yaml>` to catch local setup issues without touching VMware or running guest commands.

`--env-file` is a top-level argument:

```bash
vm-auto-test --env-file .env <command>
```

## Real-run preflight

Confirm or ask for:

1. `vmrun.exe` is installed and `VMRUN_PATH` is configured.
2. Target VM is a `.vmx` path or appears in `vm-auto-test vms`.
3. VMware Tools is installed and ready.
4. VMware access-control encryption is disabled.
5. Guest credentials are for a local administrator account, not a Microsoft online account.
6. The credential user has logged into the VM desktop at least once.
7. The selected snapshot is safe to revert to.
8. The sample command is valid inside the guest VM.
9. The verification command observes a safe, real effect.
10. Commands do not print secrets that would be persisted into reports.

## Common workflows

### Interactive

```bash
vm-auto-test
```

Menu:

```text
[0] 退出
[1] 测试单样本
[2] 测试多样本 (CSV)
[3] 列出 VM
[4] 列出快照
[5] 重新配置环境
```

Interactive single-sample and CSV flows can prompt for screenshots. Screenshots are saved as `screenshot.png` in the relevant report directory.

### Doctor preflight

```bash
vm-auto-test doctor
vm-auto-test doctor --config configs/baseline.yaml --reports-dir reports
```

Doctor checks Python, package metadata, `VMRUN_PATH`, optional YAML config parsing, and report directory writability. It returns `3` on failed checks, does not connect to VMware, and must not print secrets from config files.

### Discover VM and snapshots

```bash
vm-auto-test vms
vm-auto-test snapshots --vm "E:\VM-MCP\windows11\Windows 11 x64.vmx"
```

If snapshot listing times out or fails, first suspect VM access-control encryption or an invalid `.vmx` path.

### Single baseline run

Baseline mode proves whether the sample creates the expected observable effect in a clean snapshot:

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

`hostname` is only a harmless smoke example. Real validations should use a verification command that observes the intended effect.

### Single AV run

AV mode can run independently in an AV-installed snapshot. If a prior `BASELINE_VALID` report exists, pass it as optional reference metadata:

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

`--baseline-result` is optional reference metadata, not a hard prerequisite. AV mode also runs a non-fatal best-effort known-AV process check; do not use detection output to tailor bypass behavior.

### Directory batch

Use when multiple sample files share one verification command:

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

`run-dir` scans `--dir` on the host running the CLI, then uses each discovered path as the guest sample command. Use it only when those paths are also valid inside the guest, such as shared or mirrored paths. Otherwise use CSV and provide explicit guest paths.

Default patterns when omitted: `*.exe`, `*.bat`, `*.ps1`, `*.cmd`. PowerShell scripts run with PowerShell; the others use `cmd`.

### CSV batch

Use when samples have their own verification commands:

```bash
vm-auto-test run-csv \
  --vm "E:\VM-MCP\windows11\Windows 11 x64.vmx" \
  --mode baseline \
  --snapshot "clean-snapshot" \
  --csv samples.csv \
  --samples-base-dir "C:\Samples" \
  --reports-dir reports
```

CSV accepts UTF-8, UTF-8 BOM, or GBK. Header row is optional when the first column starts with `sample`.

| sample_file | verify_command | verify_shell |
|---|---|---|
| `sample.exe` | `hostname` | `cmd` |
| `test.bat` | `schtasks /query` | `powershell` |

Relative `sample_file` values require `--samples-base-dir`. CSV sample paths are intended to become guest commands.

### YAML config

Create, validate, and run reusable configs:

```bash
vm-auto-test init-config --output configs/baseline.yaml --mode baseline
vm-auto-test doctor --config configs/baseline.yaml
vm-auto-test config validate --config configs/baseline.yaml
vm-auto-test run --config configs/baseline.yaml
```

Single-sample shape:

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

Multi-sample configs use `samples:` instead of `sample:`. Do not include both. A top-level `verification` mapping is still required; per-sample `verification` can override it.

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

For AV configs, `baseline_result` is optional:

```yaml
mode: av
baseline_result: "reports/20260509-120000-000000-sample/result.json"  # optional
```

## Verification command rules

All sample, verification, environment-probe, and AV log commands run as the configured guest credential user.

Interactive single-sample and interactive CSV flows pre-resolve `%VAR%` in verification commands by running `echo %VAR%` as the credential user. If the expanded value points to a different real user profile, the CLI rewrites it to the credential user's profile when safe.

Non-interactive `run`, `run-dir`, `run-csv`, and `run-config` do not perform that CLI pre-resolution step:

- `cmd` commands can use `%APPDATA%` inside the guest.
- PowerShell commands should use `$env:APPDATA`, or use `--verify-shell cmd` for `%VAR%` syntax.
- Prefer environment-variable-based paths over hardcoded `C:\Users\<name>\...` paths.

## Result interpretation

| Classification | Meaning | Safe next step |
|---|---|---|
| `BASELINE_VALID` | Verification output changed in baseline mode. | Use as optional reference for AV comparison. |
| `BASELINE_INVALID` | Verification output did not change in baseline mode. | Fix sample path, permissions, timeout, or verification command. |
| `AV_NOT_BLOCKED` | In AV mode, the effect still occurred. | Keep report for defensive analysis; do not pivot to evasion. |
| `AV_BLOCKED_OR_NO_CHANGE` | In AV mode, no effect was observed. | Check reports and configured logs to distinguish blocking from sample failure. |

Reports are written under `reports/` unless overridden.

Single run:

```text
reports/<timestamp>-<sample>/
  result.json            schema_version: 1
  before.txt
  after.txt
  sample_stdout.txt
  sample_stderr.txt
  test.log
  screenshot.png         optional
  av_logs/               optional
```

Batch run:

```text
reports/<timestamp>-batch/
  result.json            schema_version: 2
  result.csv             UTF-8 BOM, Excel-friendly, formula guarded
  result.html            static summary page
  test.log
  samples/<sample_id>/
    result.json          schema_version: 2
    before.txt
    after.txt
    sample_stdout.txt
    sample_stderr.txt
    screenshot.png       optional
    av_logs/             optional
```

Batch `result.csv` has one row per sample. Batch `result.html` is the interactive batch summary generated by the normal batch run path and links to per-sample artifacts. All dynamic HTML values are escaped; CSV cells are guarded against common spreadsheet formula execution.

Report artifacts store verification output, sample stdout/stderr, and configured AV log output verbatim. Treat report directories as sensitive local evidence and avoid commands that print secrets.

## Comparison strategies

Default behavior is `changed`: compare normalized before/after output.

YAML `verification.comparisons` and per-sample `verification.comparisons` can use:

| Type | Required field | Use |
|---|---|---|
| `changed` | none | Before/after output differs after normalization. |
| `contains` | `value` | Output contains a string. |
| `regex` | `pattern` | Output matches a regex. |
| `json_field` | `path`, `expected` | JSON field at dot path equals expected value. |
| `file_hash` | `expected` | Output SHA-256 equals expected value. |

## AV detection and log collection

In AV mode, the project attempts non-fatal best-effort process-name detection for known local AV products represented in `src/vm_auto_test/av_detection.py` (`腾讯电脑管家`, `360安全卫士`, `火绒安全软件`). Current detection checks one required process marker per known AV signature. It is reporting context only.

Configured AV log collectors run only explicit guest commands:

```yaml
av_logs:
  collectors:
    - id: app-events
      type: guest_command
      command: "Get-WinEvent -LogName Application -MaxEvents 20"
      shell: powershell
```

`vm-auto-test report` reads existing JSON and emits a simple standalone HTML or formatted JSON file; it does not rebuild the full batch dashboard.

Do not invent vendor-specific collectors unless the user provides the exact safe command.

## Troubleshooting

| Symptom | Likely cause | Safe action |
|---|---|---|
| Snapshot listing fails or times out | VM access-control encryption or wrong `.vmx` path | Ask user to disable encryption and verify VM path. |
| `VmToolsNotReadyError` | VMware Tools missing, stopped, or guest not booted | Install/restart VMware Tools and retry. |
| Repeated guest auth failures | Wrong local credentials, Microsoft online account, or user profile not initialized | Use local administrator credentials, log in once, then reconfigure. |
| AV result is hard to interpret | No comparable baseline reference exists | Optionally run baseline first and pass its `result.json`; AV mode remains available without it. |
| CSV parse error | Encoding, missing columns, relative sample without base dir, invalid shell, or missing CSV file | Save as UTF-8/GBK CSV with `sample_file,verify_command,verify_shell`; set `--samples-base-dir` for relative paths. |
| `BASELINE_INVALID` | Verification does not observe the effect, wrong user profile, sample path invalid, or permissions issue | Choose a better verification command and ensure it targets the credential user's context. |
| PowerShell verification with `%APPDATA%` fails in non-interactive CLI | `%VAR%` is cmd syntax and non-interactive CLI does not pre-resolve it | Use `$env:APPDATA` or run verification with `cmd`. |
| `run-dir` finds files but guest execution fails | Host-scanned paths are not valid inside the guest | Use shared/mirrored paths or CSV with explicit guest paths. |
| `detect_av` reports no known AV | AV not installed, process names differ, or unsupported product | Treat it as context only; use configured verification and logs. |
| Screenshot missing | Screenshot capture failed or was not requested | Use `--capture-screenshot` or answer yes interactively; check step status in `result.json`. |

## Development checks

When modifying Python code, run:

```bash
pytest
python -m compileall -q src tests
```

The test suite uses fake providers and does not require VMware. `vm-auto-test-smoke` touches the real VMware setup and should only run when explicitly requested.

When modifying only this skill document, Python tests are usually unnecessary. Instead verify:

- Commands and arguments match `src/vm_auto_test/cli.py`.
- YAML examples match `src/vm_auto_test/config.py`.
- Report descriptions match `src/vm_auto_test/reporting.py`.
- The safety boundary remains intact.
- No real credentials, passwords, private samples, or sensitive local paths are exposed.
