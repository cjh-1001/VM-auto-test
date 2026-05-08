from __future__ import annotations

from vm_auto_test.providers.base import VmwareProvider
from vm_auto_test.providers.vmrun_provider import VmrunProvider


_PLACEHOLDER_PROVIDERS = {"vsphere", "powercli", "mcp"}


def create_provider(provider_type: str = "vmrun") -> VmwareProvider:
    normalized_type = provider_type.strip().lower()
    if normalized_type == "vmrun":
        return VmrunProvider()
    if normalized_type in _PLACEHOLDER_PROVIDERS:
        raise NotImplementedError(
            f"Provider '{normalized_type}' is an extension placeholder and is not implemented yet"
        )
    raise ValueError(f"Unknown provider: {provider_type}")
