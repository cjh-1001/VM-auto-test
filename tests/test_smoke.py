from __future__ import annotations

from vm_auto_test.smoke import missing_smoke_env


def test_missing_smoke_env_reports_required_keys(monkeypatch):
    monkeypatch.delenv("VM_AUTO_TEST_SMOKE_VM_ID", raising=False)

    assert missing_smoke_env() == ["VM_AUTO_TEST_SMOKE_VM_ID"]
