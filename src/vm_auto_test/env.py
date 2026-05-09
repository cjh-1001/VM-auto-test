from __future__ import annotations

import json
import os
from pathlib import Path

from vm_auto_test.models import GuestCredentials


def load_env_file(path: Path, *, override: bool = False) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Env file not found: {path}")
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            raise ValueError(f"Invalid env line {line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid env line {line_number}: key is empty")
        if not override and key in os.environ:
            continue
        os.environ[key] = _strip_quotes(value.strip())


def load_optional_env_file(path: Path | None) -> None:
    if path is not None:
        load_env_file(path)
        return
    default_path = Path(".env")
    if default_path.exists():
        load_env_file(default_path)


def is_env_configured() -> bool:
    vmrun = os.getenv("VMRUN_PATH", "")
    if not vmrun:
        return False
    return Path(vmrun).is_file()


def resolve_guest_credentials(vm_id: str) -> GuestCredentials | None:
    """Resolve guest credentials for a VM.

    Lookup order:
    1. VMWARE_CREDENTIALS_FILE JSON, keyed by vm_id (absolute .vmx path)
    2. VMWARE_GUEST_USER / VMWARE_GUEST_PASSWORD env vars
    3. Return None (caller should prompt)
    """
    creds_file = os.getenv("VMWARE_CREDENTIALS_FILE", "")
    if creds_file:
        creds_path = Path(creds_file)
        if creds_path.is_file():
            try:
                data = json.loads(creds_path.read_text(encoding="utf-8"))
                entry = data.get(vm_id)
                if isinstance(entry, dict) and entry.get("user"):
                    return GuestCredentials(
                        user=entry["user"],
                        password=entry.get("password", ""),
                    )
            except (json.JSONDecodeError, OSError):
                pass

    user = os.getenv("VMWARE_GUEST_USER", "")
    if user:
        return GuestCredentials(
            user=user,
            password=os.getenv("VMWARE_GUEST_PASSWORD", ""),
        )
    return None


def _credentials_file_path() -> Path:
    creds_file = os.getenv("VMWARE_CREDENTIALS_FILE", "")
    if creds_file:
        return Path(creds_file)
    return Path("credentials.json")


def load_credentials_store() -> dict[str, dict[str, str]]:
    path = _credentials_file_path()
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_credentials_store(data: dict[str, dict[str, str]]) -> None:
    path = _credentials_file_path()
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def upsert_vm_credentials(vm_id: str, user: str, password: str) -> None:
    store = load_credentials_store()
    store[vm_id] = {"user": user, "password": password}
    save_credentials_store(store)


def remove_vm_credentials(vm_id: str) -> bool:
    store = load_credentials_store()
    if vm_id in store:
        del store[vm_id]
        save_credentials_store(store)
        return True
    return False


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
