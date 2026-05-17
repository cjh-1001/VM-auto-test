# VM Auto Test

Use this skill when working on or operating the local `vm-auto-test` project. The project automates VMware Workstation lab validation: revert snapshot → start VM → run sample → verify effect → collect logs → write report.

It only automates execution, observation, comparison, and reporting. Do not use it to generate samples, bypass AV/EDR, evade detection, or run outside an authorized local lab.

## Safety boundary (always enforce)

- Authorized local VMware Workstation lab only; Host-only/NAT networking.
- Known rollback snapshot before every sample execution.
- No unknown samples on production hosts, shared systems, or non-owned VMs.
- Never print/expose passwords from `.env`, YAML, or `credentials.json`.
- Prefer `guest.password_env` in YAML; keep credential files out of version control.
- Treat `credentials.json`, YAML `guest.password`, reports, and AV logs as sensitive.
- Refuse bypass, stealth, obfuscation, anti-analysis, persistence, privilege escalation, payload generation, or evasion requests.

## Command quick reference

| User intent | Command |
|-------------|---------|
| Interactive menu | `vm-auto-test` |
| Plan tasks | `vm-auto-test` → `[5] 计划任务` |
| List running VMs | `vm-auto-test vms` |
| List snapshots | `vm-auto-test snapshots --vm "<vmx>"` |
| Doctor (no VM) | `vm-auto-test doctor [--config <yaml>]` |
| Single sample | `vm-auto-test run ...` |
| Single from YAML | `vm-auto-test run --config <yaml>` (preferred) |
| Batch directory | `vm-auto-test run-dir ...` |
| Batch CSV | `vm-auto-test run-csv ...` |
| Create YAML | `vm-auto-test init-config --output <yaml> --mode baseline\|av\|av-analyze` |
| Validate YAML | `vm-auto-test config validate --config <yaml>` |
| Report from JSON | `vm-auto-test report --input <json> --output <file>` |
| Real VMware smoke | `vm-auto-test-smoke` (only when explicitly requested) |

`--env-file` is a top-level arg: `vm-auto-test --env-file .env <command>`.

Console scripts: `vm-auto-test`, `vm-auto-test-smoke`, `vmware-mcp`.

## Key constraints

- `run --config` is the recommended config entrypoint. Do NOT mix with direct flags (`--vm`, `--mode`, `--sample-command`, `--reports-dir`); CLI rejects mixed usage.
- Before non-interactive real runs, prefer `vm-auto-test doctor --config <yaml>` first.
- Interactive menu: `[0] 退出` `[1] 测试单样本` `[2] 测试多样本 (CSV)` `[3] 列出 VM` `[4] 列出快照` `[5] 计划任务` `[6] 重新配置环境`
- Plan tasks are an interactive in-memory queue: add single or CSV batch tests, set repeat counts (default 1, max 100), view/delete/clear tasks, then execute sequentially in the current session.
- Plan tasks are not persisted and are not a background scheduler/cron. Exiting the interactive session discards the queue.
- Plan-task execution reuses existing single/batch orchestration and reports; it must not print guest passwords.

## Real-run preflight

Confirm/ask for: (1) `vmrun.exe` installed and `VMRUN_PATH` configured; (2) `.vmx` path valid; (3) VMware Tools installed; (4) VM encryption disabled; (5) local admin credentials, not Microsoft online account; (6) credential user has logged in at least once; (7) snapshot safe to revert; (8) sample command valid inside guest; (9) verification command observes safe, real effect; (10) no secrets in commands.

## Verification command rules

- All commands run as the configured guest credential user.
- Interactive flows pre-resolve `%VAR%`; non-interactive (`run`, `run-dir`, `run-csv`, `run-config`) do not.
- `cmd`: use `%APPDATA%`. PowerShell: use `$env:APPDATA`. Avoid hardcoded `C:\Users\<name>\...`.

## Result interpretation

| Classification | Meaning | Action |
|----------------|---------|--------|
| `BASELINE_VALID` | Effect observed in baseline | Use as optional AV reference |
| `BASELINE_INVALID` | No effect in baseline | Fix sample path, permissions, or verification |
| `AV_NOT_BLOCKED` | Effect still occurred under AV | Keep for defensive analysis; do not pivot to evasion |
| `AV_BLOCKED_OR_NO_CHANGE` | No effect under AV | Distinguish blocking vs sample failure via reports/logs |
| `AV_ANALYZE_BLOCKED` | Log/screenshot changed, AV recorded new activity | AV intercepted the sample |
| `AV_ANALYZE_NOT_BLOCKED` | Log/screenshot unchanged, AV detected no threat | AV did not intercept the sample |

## Report structures

Single run: `reports/<timestamp>-<sample>/` → `result.json` (schema 1), `before.txt`, `after.txt`, `sample_stdout.txt`, `sample_stderr.txt`, `test.log`, optional `screenshot.png` (baseline/av) or `screenshot_before.png` + `screenshot_after.png` (av_analyze), optional `av_logs/`.

Batch run: `reports/<timestamp>-batch/` → `result.json` (schema 2), `result.csv` (UTF-8 BOM, formula-safe), `result.html` (interactive dashboard), `test.log`, `samples/<id>/` per-sample artifacts. av_analyze mode: HTML has log analysis + image diff columns (no verify command), 5 artifact files only.

`vm-auto-test report` reads existing JSON, auto-detects schema version: batch JSON (schema 2 with `samples` and `mode`) generates interactive HTML; single-sample JSON generates standalone HTML. Default `--format html`.

## Comparison strategies (YAML `verification.comparisons`)

| Type | Required field | Behavior |
|------|---------------|----------|
| `changed` (default) | — | Before/after output differs |
| `contains` | `value` | Output contains string |
| `regex` | `pattern` | Output matches regex |
| `json_field` | `path`, `expected` | JSON field at dot-path equals expected |
| `file_hash` | `expected` | Output SHA-256 equals expected |

## YAML config shapes

Single sample: `vm_id`, `snapshot`, `mode`, `guest.{user,password_env}`, `sample.{command,shell}`, `verification.{command,shell}`, `reports_dir`, `provider.{type:vmrun}`.

Multi-sample: use `samples:` array (not `sample:`). Top-level `verification` required; per-sample overrides supported.

AV optional: `baseline_result: "reports/<path>/result.json"`.

AV_ANALYZE mode: `mode: av_analyze`, `av_analyze.log_sources` (optional, auto-populated from AV detection), `av_analyze.log_collect_command` (optional), `av_analyze.log_export_preset` (optional), `av_analyze.api_key_env` (optional for AI analysis), `av_analyze.enable_image_compare` (optional, pixel-level screenshot diff), `av_analyze.image_compare_threshold` (optional, default 5.0%). Verification is optional in this mode. CSV only needs 1 column (`sample_file`).

## AV detection & logs

AV mode and AV_ANALYZE mode perform non-fatal process-name detection: 腾讯电脑管家 (`QQPCTray.exe`), 360 (`360Tray.exe`), 火绒 (`HipsDaemon.exe`). Configured collectors run only explicit guest commands via `av_logs.collectors`. Do not invent vendor-specific collectors.

AV_ANALYZE mode auto-detects the AV and applies built-in log profiles:

| AV | Log files | Export preset |
|----|-----------|---------------|
| 360安全卫士 | `360safe.Summary.dat`, `...Summary.union1` | `360` |
| 火绒 | `HipsLogV3.db`, `HipsPolicy.db`, `HrLogV3.db`, `HrTrayMsg.db` | `huorong` |
| 腾讯电脑管家 | `TfAvCenter.db`, `FileMon.db`, `NetFlow.db` | `tencent` |

Log source paths use `{username}` placeholder, resolved at runtime via `cmd /c echo %USERNAME%` on the guest. Export scripts are in `src/vm_auto_test/av_exporters/` and dispatch via presets.

## Screenshot behavior

When `--capture-screenshot` is enabled:
- **AV/Baseline mode**: framework captures a screenshot 10 seconds after sample execution begins (parallel to sample run), plus a post-verification screenshot. Both go to `screenshot.png` in the report directory.
- **AV_ANALYZE mode**: framework captures `screenshot_before.png` before sample execution and `screenshot_after.png` 10 seconds after sample execution starts (same delayed async logic as AV mode).

## Troubleshooting quick reference

| Symptom | Likely cause | Action |
|---------|-------------|--------|
| Snapshot listing fails | VM encryption or wrong `.vmx` | Disable encryption, verify path |
| `VmToolsNotReadyError` | VMware Tools missing | Install/restart VMware Tools |
| Guest auth failures | Wrong creds, online account, no profile | Local admin, log in once, reconfigure |
| `BASELINE_INVALID` | Bad path/permissions/verification | Fix verification command for cred user context |
| CSV parse error | Encoding/columns/path | UTF-8/GBK, `sample_file,verify_command,verify_shell` (av_analyze mode: `sample_file` only) |
| `%APPDATA%` in PS | cmd syntax | Use `$env:APPDATA` or `cmd` shell |
| `run-dir` guest fail | Host path != guest path | Use shared paths or CSV |
| Screenshot missing | Not requested or failed | `--capture-screenshot`, check `result.json` steps |
| `run` missing args | Missing `--config` or direct args | Provide `--config <yaml>` or all direct args |
| `run cannot combine` | Mixed `--config` + direct args | Choose one or the other |
| `doctor` fails | Bad VMRUN_PATH/config/dir | Fix `.env` or config |
| AV_ANALYZE no logs | AV not detected, wrong username, log files not found | Verify AV processes running, check `{username}` in paths |
| AV_ANALYZE export empty | WAL recovery or SQLite parse issue | Check raw log files in `av_logs/after/` |
| AV_ANALYZE false positive | Volatile headers in exported text | Already handled by `_normalize_log_for_comparison` |
| AV_ANALYZE image compare pending | Screenshot files missing or comparison not completed | Check `screenshot_before.png` and `screenshot_after.png` exist, verify Pillow installed |

## Development checks

When modifying Python code:
```bash
pytest
python -m compileall -q src tests
```

Tests use fake providers (no VMware needed). `vm-auto-test-smoke` only when explicitly requested.

When modifying only this skill document, verify against source files instead of running tests:
- CLI args → `src/vm_auto_test/cli.py`
- YAML schema → `src/vm_auto_test/config.py`
- Report artifacts → `src/vm_auto_test/reporting.py`
- Safety boundary intact; no real credentials exposed.

## Source of truth

| Area | File |
|------|------|
| CLI args, interactive flow, `config validate`, `report`, `run --config` | `src/vm_auto_test/cli.py` |
| YAML/CSV schema, sample scanning | `src/vm_auto_test/config.py` |
| Execution orchestration (incl. screenshot timing, av_analyze dual-track) | `src/vm_auto_test/orchestrator.py` |
| AV process detection + log profiles | `src/vm_auto_test/av_detection.py` |
| AV log collection from guest | `src/vm_auto_test/av_logs.py` |
| AV log export (SQLite → text) | `src/vm_auto_test/av_exporters/` |
| AI log + screenshot analysis, pixel-level image comparison | `src/vm_auto_test/analysis.py` |
| Output comparison and classification | `src/vm_auto_test/evaluator.py` |
| Report artifacts and schemas | `src/vm_auto_test/reporting.py` |
| Console scripts | `pyproject.toml` |
| User-facing docs | `README.md` |
