from __future__ import annotations

from vm_auto_test.models import BatchTestResult, Classification, StepResult


_STEP_LABELS: dict[str, str] = {
    "create_report_dir": "创建报告目录",
    "revert_snapshot": "回滚到快照",
    "start_vm": "开机",
    "wait_guest_ready": "等待系统就绪",
    "detect_av": "识别杀软环境",
    "before_verification": "攻击前环境检查",
    "run_sample": "执行恶意样本",
    "after_verification": "攻击后效果验证",
    "capture_screenshot": "截取 VM 画面",
    "capture_screenshot_before": "截图(before)",
    "capture_screenshot_after": "截图(after)",
    "collect_av_logs": "采集杀软日志",
    "log_analysis": "日志判断",
    "image_compare": "截图对比",
    "evaluate": "综合判定攻击效果",
    "write_report": "生成测试报告",
    "write_batch_report": "生成批量报告",
    "batch_sample": "处理样本",
    "batch_evaluate": "批量判定",
}

_SUB_STEPS = {"check_vmware_tools", "guest_process_check", "guest_script"}

_last_stage: str = ""


def reset_progress() -> None:
    global _last_stage
    _last_stage = ""


def print_batch_report_paths(report_dir: str, indent: str = "  ") -> None:
    print(f"\n{indent}报告文件:")
    print(f"{indent}  HTML: {report_dir}/result.html")
    print(f"{indent}  CSV:  {report_dir}/result.csv")


def print_batch_summary(batch_result: BatchTestResult, indent: str = "  ") -> None:
    max_id_width = max((display_width(s.sample_spec.id) for s in batch_result.samples), default=0)
    for sample_item in batch_result.samples:
        label = classify_cn(sample_item.classification, short=True)
        duration = _format_duration(sample_item.duration_seconds)
        print(f"{indent}{display_ljust(sample_item.sample_spec.id, max_id_width + 2)}  {label}  ({duration})")
    total = _format_duration(batch_result.duration_seconds)
    print(f"\n{indent}总用时: {total}")


def classify_cn(classification: Classification | str, short: bool = False) -> str:
    mapping = {
        "BASELINE_VALID": "✓ SUCCESS — 有效" if short else "样本有效（前后输出有变化）",
        "BASELINE_INVALID": "✗ FAILED — 无效" if short else "样本无效（前后输出无变化）",
        "AV_NOT_BLOCKED": "✗ FAILED — 未拦截" if short else "杀软未拦截（攻击效果发生）",
        "AV_BLOCKED_OR_NO_CHANGE": "✓ SUCCESS — 已拦截" if short else "杀软已拦截或未生效",
        "AV_ANALYZE_BLOCKED": "✓ 已拦截",
        "AV_ANALYZE_NOT_BLOCKED": "✗ 未拦截",
    }
    value = classification.value if hasattr(classification, "value") else str(classification)
    return mapping.get(value, value)


def display_width(text: str) -> int:
    return sum(2 if ord(c) > 127 else 1 for c in text)


def display_ljust(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def print_progress(step: StepResult) -> None:
    global _last_stage

    if step.name in _SUB_STEPS:
        return

    if step.status == "started":
        if step.name == "run_sample":
            if step.stage.strip() and step.stage.strip() != _last_stage:
                _last_stage = step.stage.strip()
                print(f"\n  ── {_last_stage} ──")
            label = _STEP_LABELS.get(step.name, step.name.replace("_", " "))
            print(f"  - {display_ljust(label, 18)}执行中...", flush=True)
        return

    if step.name.endswith("_output"):
        _print_indented(step.detail)
        return

    stage = step.stage.strip()
    if stage and stage != _last_stage:
        _last_stage = stage
        print(f"\n  ── {stage} ──")

    label = _STEP_LABELS.get(step.name, step.name.replace("_", " "))
    if step.status == "skipped":
        icon = "⊘"
    elif step.status == "passed":
        icon = "✓"
    else:
        icon = "✗"
    label_col = display_ljust(label, 18)
    detail = step.detail if step.detail else ""
    print(f"  {icon} {label_col}{detail}", flush=True)


def _print_indented(text: str, indent: int = 6) -> None:
    prefix = " " * indent
    for line in text.splitlines():
        print(f"{prefix}│ {line}", flush=True)


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m {secs:.0f}s"
