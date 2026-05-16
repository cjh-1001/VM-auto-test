from __future__ import annotations

from pathlib import Path

_EXPORT_PRESETS = {
    "tencent": "scripts/av_logs/export_tencent.py",
    "huorong": "scripts/av_logs/export_huorong.py",
    "360": "scripts/av_logs/export_360.py",
}


def get_presets() -> dict[str, str]:
    return dict(_EXPORT_PRESETS)


def run_log_export(
    preset: str,
    raw_files: tuple[Path, ...],
    output_dir: Path,
    project_root: Path,
) -> str:
    script_rel = _EXPORT_PRESETS.get(preset)
    if script_rel is None:
        raise ValueError(
            f"Unknown log export preset: {preset!r}. "
            f"Available: {', '.join(_EXPORT_PRESETS)}"
        )

    script_path = project_root / script_rel
    if not script_path.exists():
        raise FileNotFoundError(f"Export script not found: {script_path}")

    import importlib.util

    spec = importlib.util.spec_from_file_location(f"av_export_{preset}", str(script_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load export script: {script_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    return mod.export_logs(raw_files, output_dir)
