# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (dev mode)
pip install -e ".[dev]"

# Run all tests (125 cases, no VMware needed — tests use FakeProvider)
pytest
pytest -x -q --tb=short          # fast-fail

# Run a single test
pytest tests/test_cli.py::test_function_name

# Syntax check only (no tests)
python -m compileall -q src tests

# List test cases without running
python -m pytest --collect-only -q

# Real VMware smoke test (requires actual VMware environment)
vm-auto-test-smoke
```

## Architecture

**Two packages, layered:**

- **`vmware_mcp`** — low-level VMware API wrappers: `vmrun.exe` subprocess calls (`vmrun.py`), `vmcli.exe` wrapper (`vmcli.py`), and Workstation REST API HTTP client (`client.py`). Also exposes an MCP stdio server (`server.py`) with ~100 tools.
- **`vm_auto_test`** — the test framework on top: models → config → orchestrator → evaluator → reporting.

**Data flow:**
```
YAML config / CLI args → TestCase (frozen dataclass)
  → TestOrchestrator.run() / run_batch()
    → VmwareProvider (vmrun) for all VM operations
    → Evaluator for output comparison & classification
    → Reporting for JSON/CSV/HTML artifacts
  → TestResult / BatchTestResult → reports/ directory
```

**Provider pattern:** `VmwareProvider` is an ABC (`providers/base.py`). `VmrunProvider` is the only concrete implementation, wrapping `vmware_mcp.vmrun.VMRun`. `create_provider()` in `providers/factory.py` is the factory. All tests use `FakeProvider` from `conftest.py`.

**Guest command pattern:** Commands run inside the VM by writing a wrapper script to a temp file, copying it into the guest, executing it, and copying output back. The orchestrator handles this via the provider.

**Immutability constraint:** All data model objects are `@dataclass(frozen=True)`. Use `dataclasses.replace()` to create modified copies — never mutate in place.

**Chinese localization:** Step labels, classifications, and error messages are in Chinese. Classifications: `BASELINE_VALID`, `BASELINE_INVALID`, `AV_NOT_BLOCKED`, `AV_BLOCKED_OR_NO_CHANGE`, `AV_ANALYZE_BLOCKED`, `AV_ANALYZE_NOT_BLOCKED`.

**Test modes:** `baseline` (verify sample produces expected effect), `av` (compare baseline vs AV environment), `av_analyze` (capture screenshots + AV logs, AI analysis via Anthropic API). In `av_analyze` mode, `TestResult.before`/`.after` store collected log content as `CommandResult(command="log_collect", stdout=...)` — not screenshot references.

## Key constraints

- `run --config` and direct flags (`--vm`, `--mode`, etc.) are mutually exclusive — CLI rejects mixed usage.
- YAML is the recommended entry point for single-sample tests. For batch, use `run-dir` or `run-csv`.
- `credentials.json` and YAML `guest.password` fields must never be committed or printed.
- Verification commands use `cmd` or `powershell` shells — `%APPDATA%` in cmd, `$env:APPDATA` in PowerShell.
- Plan tasks are an in-memory interactive queue, not persisted. Exiting discards them.

## Source of truth

| Area | File |
|------|------|
| CLI args, interactive menu, subcommand dispatch | `src/vm_auto_test/cli.py` |
| YAML/CSV config schema, sample scanning | `src/vm_auto_test/config.py` |
| Execution orchestration (screenshots, AV detection, verify/evaluate pipeline) | `src/vm_auto_test/orchestrator.py` |
| Frozen data models (`TestCase`, `TestResult`, enums) | `src/vm_auto_test/models.py` |
| Output normalization, comparison strategies, classification | `src/vm_auto_test/evaluator.py` |
| Report generation (JSON/CSV/HTML) | `src/vm_auto_test/reporting.py` |
| AV process detection, log profiles, export presets | `src/vm_auto_test/av_detection.py` |
| AI-powered log/screenshot analysis via Anthropic API | `src/vm_auto_test/analysis.py` |
| Guest AV log collection | `src/vm_auto_test/av_logs.py` |
| AV log export (per-AV SQLite→text profiles: 360/火绒/腾讯电脑管家) | `src/vm_auto_test/av_exporters/` |
| .env loading, credentials management | `src/vm_auto_test/env.py` |
| Provider ABC + VmrunProvider | `src/vm_auto_test/providers/` |
| FakeProvider (all tests) + shared fixtures | `tests/conftest.py` |
| Subcommand implementations (batch, config, doctor, inventory, output, report) | `src/vm_auto_test/commands/` |
| vmrun.exe wrapper, path quoting, credential passing | `src/vmware_mcp/vmrun.py` |
| User-facing documentation | `README.md` |
| Detailed API reference | `API_INTERFACE.md` |
