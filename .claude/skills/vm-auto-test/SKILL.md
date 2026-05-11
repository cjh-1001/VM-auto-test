# VM Auto Test

Use this skill when working on the local `vm-auto-test` project or helping the user operate it. The project automates VMware Workstation lab validation for authorized sample-effect checks and defensive AV blocking comparison.

The safe workflow is:

```text
revert snapshot -> start VM -> wait for VMware Tools -> verify before -> run sample -> verify after -> collect configured logs -> write report
```

It only automates execution, observation, comparison, and reporting. Do not use it to generate samples, bypass detection, evade AV/EDR, establish persistence, move laterally, escalate privileges, or run tests outside an authorized local lab.

## Safety boundary

Before helping the user run a real test, confirm these boundaries:

- Authorized local VMware Workstation lab only.
- Prefer Host-only or NAT networking.
- Use a known rollback snapshot before executing any sample.
- Do not run unknown samples on production hosts, shared systems, or non-owned VMs.
- Do not print, summarize, or expose passwords from `.env`, YAML files, or `credentials.json`.
- Do not invent AV bypass, stealth, obfuscation, anti-analysis, persistence, privilege escalation, or payload-generation steps.
- If the user asks for offensive capability beyond automation and comparison, refuse that part and redirect to safe validation/reporting workflows.

## Project facts to keep aligned

Check current code before changing this skill. The authoritative files are:

- `src/vm_auto_test/cli.py` — CLI arguments, interactive menu, environment-variable pre-resolution.
- `src/vm_auto_test/config.py` — YAML/CSV schema, comparison config, sample scanning.
- `src/vm_auto_test/orchestrator.py` — execution flow and report generation.
- `README.md` — user-facing setup and workflow documentation.
- `pyproject.toml` — console script names.

Console scripts:

- `vm-auto-test`
- `vm-auto-test-smoke`
- `vmware-mcp`

## Preflight checklist for real VMware runs

Check or ask for:

1. `vmrun.exe` is installed and `VMRUN_PATH` is configured in `.env`.
2. The target VM is either a `.vmx` path or a running VM returned by `vm-auto-test vms`.
3. VMware Tools is installed and ready inside the guest.
4. VM access control encryption is disabled; encrypted VMs often make `vmrun` snapshot commands hang or fail.
5. A local guest administrator account exists. Avoid Microsoft online accounts because `vmrun` guest auth expects local credentials.
6. The credential user has logged into the VM desktop at least once so Windows creates that user profile directory.
7. Guest credentials are configured through the interactive menu or provided through the intended credential mechanism.
8. A clean snapshot exists for baseline mode; an AV-installed snapshot exists for AV mode when needed.
9. The sample command resolves to a path that is valid inside the guest VM.
10. The verification command observes a real, safe effect of the sample before/after execution.
11. All guest commands run as the configured credential user, not necessarily the currently visible desktop user.

## Choosing the right command

| User intent | Command |
|---|---|
| First-time setup or guided operation | `vm-auto-test` |
| Configure `VMRUN_PATH` | `vm-auto-test` then menu `[5] 重新配置环境` |
| Configure/verify VM credentials | `vm-auto-test` then menu `[3] 列出 VM` |
| List running VMs | `vm-auto-test vms` |
| List snapshots | `vm-auto-test snapshots --vm "<vmx path>"` |
| Test one known sample | `vm-auto-test run ...` |
| Batch test from a local directory scan | `vm-auto-test run-dir ...` |
| Batch test from CSV with per-sample verification | `vm-auto-test run-csv ...` |
| Create reusable YAML config | `vm-auto-test init-config ...` |
| Run reusable YAML config | `vm-auto-test run-config <yaml>` |
| Check real VMware connectivity | `vm-auto-test-smoke` only when explicitly requested |

If the user provides partial details, prefer the interactive menu (`vm-auto-test`) rather than guessing paths, credentials, snapshots, or verification commands.

`--env-file` is a top-level argument:

```bash
vm-auto-test --env-file .env <command>
```

## Common workflows

### 1. Interactive first run

```bash
vm-auto-test
```

Use this for setup, guided single-sample tests, guided CSV tests, VM listing, snapshot listing, and per-VM credential configuration.

The menu currently exposes:

```text
[0] 退出
[1] 测试单样本
[2] 测试多样本 (CSV)
[3] 列出 VM
[4] 列出快照
[5] 重新配置环境
```

### 2. Discover VM and snapshots

```bash
vm-auto-test vms
vm-auto-test snapshots --vm "E:\VM-MCP\windows11\Windows 11 x64.vmx"
```

If snapshot listing times out or fails, first suspect VM encryption/access control or an invalid `.vmx` path.

### 3. Single-sample baseline test

Use baseline mode on a clean snapshot to prove the sample creates the expected observable effect:

```bash
vm-auto-test run \
  --vm "E:\VM-MCP\windows11\Windows 11 x64.vmx" \
  --mode baseline \
  --snapshot "clean-snapshot" \
  --sample-command "C:\Samples\sample.exe" \
  --sample-shell cmd \
  --verify-command "hostname" \
  --verify-shell powershell \
  --reports-dir reports
```

`hostname` is a harmless smoke example, but real validations should use a verification command that observes the expected effect.

### 4. Single-sample AV test

Run AV mode only after a baseline report classified as `BASELINE_VALID`:

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

AV mode checks whether the same observable effect still occurs in the AV snapshot. It does not tune around detection.

### 5. Directory batch test

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

Important: `run-dir` scans `--dir` from the machine running the CLI, then uses each discovered path as the guest sample command. Use it only when those paths are also valid inside the guest, such as a shared or mirrored path. Otherwise use CSV and provide guest paths explicitly.

When `--pattern` is omitted, default patterns are `*.exe`, `*.bat`, `*.ps1`, and `*.cmd`. `.ps1` samples run with PowerShell; others run with `cmd`.

### 6. CSV batch test

Use when each sample has its own verification command:

```bash
vm-auto-test run-csv \
  --vm "E:\VM-MCP\windows11\Windows 11 x64.vmx" \
  --mode baseline \
  --snapshot "clean-snapshot" \
  --csv samples.csv \
  --samples-base-dir "C:\Samples" \
  --reports-dir reports
```

CSV is UTF-8/UTF-8 BOM or GBK with 3 columns. A header row is optional when the first column starts with `sample`.

| sample_file | verify_command | verify_shell |
|---|---|---|
| `sample.exe` | `hostname` | `cmd` |
| `test.bat` | `schtasks /query` | `powershell` |

Relative `sample_file` values require `--samples-base-dir`. CSV sample paths are intended to become guest commands.

### 7. YAML config workflow

Create and run reusable configs:

```bash
vm-auto-test init-config --output configs/baseline.yaml --mode baseline
vm-auto-test run-config configs/baseline.yaml
```

`init-config` can accept:

```bash
vm-auto-test init-config --output configs/batch.yaml --mode baseline --vm "<vmx path>" --samples-dir "C:\Samples"
```

For AV configs, include a valid baseline result path:

```yaml
mode: av
baseline_result: "reports/20260509-120000-000000-sample/result.json"
```

Prefer `guest.password_env` over storing passwords directly in YAML.

## YAML essentials

Single-sample baseline shape:

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

Multi-sample configs use `samples:` instead of `sample:`. Do not include both.

```yaml
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
```

## Verification commands and environment variables

All sample, verification, and environment-probe commands run as the credential user.

Interactive single-sample and interactive CSV flows pre-resolve `%VAR%` in verification commands by running `echo %VAR%` as the credential user. If the expanded value contains a `C:\Users\<name>\` profile that is not the credential user and is not a system profile such as `Public` or `Default`, the CLI checks that profile exists and rewrites the path to the credential user. This helps avoid checking the wrong user profile after Microsoft-account or profile-name drift.

Example interactive verification command:

```cmd
dir "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Updater.lnk"
```

Non-interactive `run`, `run-dir`, `run-csv`, and `run-config` do not perform that CLI pre-resolution step. In those modes:

- `cmd` verification commands can use normal `%APPDATA%` expansion inside the guest.
- PowerShell verification commands should use PowerShell syntax such as `$env:APPDATA`, or use `--verify-shell cmd` for `%VAR%` syntax.
- Prefer `%APPDATA%`/`$env:APPDATA` or credential-user-relative logic over hardcoded `C:\Users\<name>\...` paths.

## Result interpretation

| Classification | Meaning | Next step |
|---|---|---|
| `BASELINE_VALID` | Verification output changed in baseline mode. | The sample/effect is valid enough to compare against AV mode. |
| `BASELINE_INVALID` | Verification output did not change in baseline mode. | Check sample path, guest permissions, timeout, and whether verification observes the right effect. |
| `AV_NOT_BLOCKED` | In AV mode, the effect still occurred. | Keep the report for defensive analysis; do not pivot into evasion guidance. |
| `AV_BLOCKED_OR_NO_CHANGE` | In AV mode, no effect was observed. | Check report files and configured AV logs to distinguish blocking from sample failure. |

Reports are written under `reports/` unless overridden:

```text
reports/<timestamp>-<sample>/
  result.json
  before.txt
  after.txt
  sample_stdout.txt
  sample_stderr.txt
```

Batch reports include per-sample result directories under the batch report directory.

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

Example:

```yaml
verification:
  command: "type C:\\marker.txt"
  shell: cmd
  comparisons:
    - type: contains
      target: after
      value: "created"
```

## AV log collection

The tool only runs explicitly configured log collection commands:

```yaml
av_logs:
  collectors:
    - id: app-events
      type: guest_command
      command: "Get-WinEvent -LogName Application -MaxEvents 20"
      shell: powershell
```

Do not invent vendor-specific collectors unless the user provides the exact safe command they want to run.

## Troubleshooting

| Symptom | Likely cause | Safe action |
|---|---|---|
| Snapshot listing fails or times out | VM access control encryption or wrong `.vmx` path | Ask user to disable encryption and verify the VM path. |
| `VmToolsNotReadyError` | VMware Tools missing, stopped, or guest not booted | Ask user to install/restart VMware Tools and retry. |
| Repeated guest auth failures | Wrong local credentials, Microsoft online account, or user profile not initialized | Use a local administrator account, log in once, and reconfigure credentials. |
| AV mode says missing baseline | `--baseline-result` absent or not from a `BASELINE_VALID` run | Run baseline first and pass its `result.json`. |
| CSV parse error | Encoding, missing columns, relative sample without base dir, or invalid shell | Save as UTF-8/GBK CSV with `sample_file,verify_command,verify_shell`; set `--samples-base-dir` for relative paths. |
| `BASELINE_INVALID` | Verification command does not observe the effect, wrong user profile, sample path invalid, or insufficient guest permissions | Choose a better verification command and ensure it targets the credential user's context. |
| PowerShell verification with `%APPDATA%` fails in non-interactive CLI | `%VAR%` is cmd syntax and non-interactive CLI does not pre-resolve it | Use `$env:APPDATA` with PowerShell or run the verification with `cmd`. |
| `run-dir` finds files but guest execution fails | Host-scanned paths are not valid inside the guest | Use shared/mirrored paths or switch to CSV with explicit guest paths. |

## Development checks

When modifying Python project code, run:

```bash
pytest
python -m compileall -q src tests
```

The test suite uses fake providers and does not require a real VMware environment. `vm-auto-test-smoke` touches the real VMware setup and should only be run when the user explicitly asks for a real VMware smoke test.

When modifying only this skill document, Python tests are usually unnecessary. Instead verify:

- Commands and arguments match `src/vm_auto_test/cli.py`.
- YAML examples match `src/vm_auto_test/config.py`.
- The safety boundary remains intact.
- No real credentials, passwords, private samples, or sensitive local paths are exposed.
