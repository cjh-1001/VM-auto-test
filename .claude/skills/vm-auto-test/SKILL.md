# VM Auto Test

Use this skill when the user wants to operate or reason about the local `vm-auto-test` project: VMware Workstation lab automation for authorized sample validation and AV blocking checks.

The project automates this workflow:

```text
revert snapshot -> start VM -> wait for VMware Tools -> run verification before -> run sample -> run verification after -> collect logs -> write report
```

It only performs automation and result comparison. Do not use it to generate samples, bypass detection, evade AV/EDR, establish persistence, move laterally, or run tests outside an authorized local lab.

## Safety boundary

Before helping the user run a test, confirm the work stays within these boundaries:

- Authorized local VMware Workstation lab only.
- Prefer isolated networking such as Host-only or NAT.
- Always use a known rollback snapshot before executing a sample.
- Do not run unknown samples on production hosts, shared systems, or non-owned VMs.
- Do not print, summarize, or expose passwords from `credentials.json`.
- Do not suggest AV bypass, stealth, obfuscation, anti-analysis, persistence, privilege escalation, or payload generation.
- If the user asks for offensive capability beyond automation and comparison, refuse that part and redirect to safe validation/reporting workflows.

## Preflight checklist

Before running `vm-auto-test`, check or ask for:

1. `vmrun.exe` is installed and `VMRUN_PATH` is configured in `.env`.
2. The target VM is a `.vmx` path or a running VM returned by `vm-auto-test vms`.
3. VMware Tools is installed and ready inside the guest.
4. VM access control encryption is disabled; encrypted VMs often cause `vmrun` snapshot commands to hang or fail.
5. A local guest administrator account exists (not a Microsoft online account — `vmrun` guest auth only supports local accounts).
6. Guest credentials are configured through the interactive menu or available through the configured credential store.
7. A clean snapshot exists for baseline mode, and an AV-installed snapshot exists for AV mode if needed.
8. The sample path is the path inside the guest VM, not necessarily the host path.
9. The verification command observes a real effect of the sample and is safe to run before and after execution.
10. **All guest commands (sample, verify, env-var expansion) run as the credential user, not the desktop user.** If a sample creates user-specific artifacts (e.g. startup folder LNK, `HKCU` registry keys), they affect the credential user's profile. Verification commands should target that same user's context — `%APPDATA%` and `HKCU` automatically resolve correctly because they run as the credential user.

## Choosing the right command

Use this decision guide:

| User intent | Command |
|---|---|
| First-time setup or guided operation | `vm-auto-test` |
| List running VMs | `vm-auto-test vms` |
| List snapshots for a VM | `vm-auto-test snapshots --vm "<vmx path>"` |
| Test one sample with known parameters | `vm-auto-test run ...` |
| Test all matching files in a directory | `vm-auto-test run-dir ...` |
| Test many samples from an Excel/CSV table | `vm-auto-test run-csv ...` |
| Create a reusable YAML config | `vm-auto-test init-config ...` |
| Run a reusable YAML config | `vm-auto-test run-config <yaml>` |
| Check the real VMware path without running a sample suite | `vm-auto-test-smoke` |

If the user provides only partial information, prefer the interactive menu (`vm-auto-test`) rather than guessing paths, credentials, snapshots, or verification commands.

`--env-file` is a top-level argument:

```bash
vm-auto-test --env-file .env <command>
```

## Common workflows

### 1. First run / interactive mode

Use this when the user wants guidance or has not configured the environment yet:

```bash
vm-auto-test
```

The menu can configure `VMRUN_PATH`, list VMs, list snapshots, configure guest credentials, and run single-sample or CSV tests.

### 2. Discover VM and snapshots

```bash
vm-auto-test vms
vm-auto-test snapshots --vm "E:\VM-MCP\windows11\Windows 11 x64.vmx"
```

If snapshot listing times out or fails, first suspect VM encryption/access control or an invalid `.vmx` path.

### 3. Single-sample baseline test

Use baseline mode on a clean snapshot to prove the sample produces the expected observable effect:

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

Prefer a verification command that observes the sample's actual expected effect. `hostname` is useful as a harmless smoke example, but often not enough to prove a real behavior change.

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

AV mode validates whether the effect still occurs in the AV snapshot. It does not attempt to bypass or tune around detection.

### 5. Directory batch test

Use when all samples in a guest-visible directory share one verification command:

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

Default patterns include `*.exe`, `*.bat`, `*.ps1`, and `*.cmd` when `--pattern` is omitted.

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

CSV format is UTF-8 or GBK with 3 columns. A header row is optional when the first column starts with `sample`.

| sample_file | verify_command | verify_shell |
|---|---|---|
| `sample.exe` | `hostname` | `cmd` |
| `test.bat` | `schtasks /query` | `powershell` |

Relative `sample_file` values require `--samples-base-dir`.

### 7. YAML config workflow

Use YAML configs for repeatable test runs:

```bash
vm-auto-test init-config --output configs/baseline.yaml --mode baseline
vm-auto-test run-config configs/baseline.yaml
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

## Result interpretation

| Classification | Meaning | Next step |
|---|---|---|
| `BASELINE_VALID` | The verification output changed in baseline mode. | The sample/effect is valid enough to compare against AV mode. |
| `BASELINE_INVALID` | The verification output did not change in baseline mode. | Check sample path, guest permissions, timeout, and whether the verification command observes the right effect. |
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

YAML `verification.comparisons` can use:

| Type | Use |
|---|---|
| `changed` | Before/after output differs after normalization. |
| `contains` | Output contains `value`. |
| `regex` | Output matches `pattern`. |
| `json_field` | JSON field at `path` equals `expected`. |
| `file_hash` | Output hash equals `expected`. |

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
| Guest authentication fails repeatedly | Wrong local credentials or Microsoft online account | Use a local administrator account and reconfigure credentials through the menu. |
| AV mode says missing baseline | `--baseline-result` absent or not `BASELINE_VALID` | Run baseline first and pass its `result.json`. |
| CSV parse error | Encoding or column mismatch | Save as CSV UTF-8/GBK with 3 columns. |
| `BASELINE_INVALID` | Verification command does not observe the effect, or user-specific path mismatch | Choose a better verification command; or verify the path targets the credential user, not a different user or the desktop user. Use `%APPDATA%` instead of hardcoded `C:\Users\<name>\` paths. |

## Development checks

When modifying Python project code, run:

```bash
pytest
python -m compileall -q src tests
```

The test suite uses fake providers and does not require a real VMware environment. `vm-auto-test-smoke` is the real VMware smoke test and should only be run when the user explicitly wants to touch the local VMware setup.

When modifying only this skill document, Python tests are usually unnecessary; review that commands match `src/vm_auto_test/cli.py`, the safety boundary is intact, and no credentials or sensitive paths are exposed.
