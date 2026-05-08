from __future__ import annotations

import pytest

from vm_auto_test.providers.factory import create_provider
from vm_auto_test.providers.vmrun_provider import VmrunProvider


def test_create_provider_defaults_to_vmrun():
    assert isinstance(create_provider("vmrun"), VmrunProvider)


@pytest.mark.parametrize("provider_type", ["vsphere", "powercli", "mcp"])
def test_create_provider_reports_unimplemented_placeholders(provider_type: str):
    with pytest.raises(NotImplementedError, match=provider_type):
        create_provider(provider_type)


def test_create_provider_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unknown provider"):
        create_provider("unknown")
