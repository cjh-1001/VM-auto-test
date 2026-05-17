"""AI check: post-process reports to classify screenshot differences as AV popups or not."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from vm_auto_test.models import Classification, AvAnalyzeResult, DeferredImageResult
from vm_auto_test.popup_classifier import classify_popup, _DEFAULT_MODEL

_LOGGER = logging.getLogger(__name__)

_CANDIDATE_CONDITION = (
    lambda ar, ic: ar is not None
    and ic is not None
    and ic.value is not None
    and ar.classification == Classification.AV_ANALYZE_NOT_BLOCKED
    and ic.value.classification == Classification.AV_ANALYZE_BLOCKED
)


@dataclass
class AiCheckResult:
    sample_id: str
    screenshot_diff: str
    ai_has_popup: bool
    ai_popup_kind: str
    ai_confidence: float
    ai_reason: str
    verdict_changed: bool
    new_classification: str


def _load_sample_results(sample_dir: Path) -> tuple[dict, AvAnalyzeResult | None, DeferredImageResult | None]:
    """Load per-sample result.json and parse av_analyze_result / image_compare_result."""
    sj = json.loads((sample_dir / "result.json").read_text(encoding="utf-8-sig"))

    ar = None
    ar_data = sj.get("av_analyze_result")
    if ar_data:
        ar = AvAnalyzeResult(
            log_found=ar_data.get("log_found", False),
            log_detail=ar_data.get("log_detail", ""),
            screenshot_analysis=ar_data.get("screenshot_analysis"),
            classification=Classification(ar_data["classification"]),
        )

    ic = None
    ic_data = sj.get("image_compare_result")
    if ic_data and not ic_data.get("pending") and ic_data.get("classification"):
        ic_value = AvAnalyzeResult(
            log_found=ic_data.get("log_found", False),
            log_detail=ic_data.get("log_detail", ""),
            screenshot_analysis=ic_data.get("screenshot_analysis"),
            classification=Classification(ic_data["classification"]),
        )
        ic = DeferredImageResult(value=ic_value)

    return sj, ar, ic


def _update_sample_json(sj: dict, new_classification: Classification, popup_kind: str, popup_text: str,
                        confidence: float, reason: str) -> None:
    """Mutate sample JSON dict in place with AI override result."""
    sj["classification"] = new_classification.value

    ar = sj.get("av_analyze_result") or {}
    ar["classification"] = new_classification.value
    if popup_kind in ("av_alert", "windows_defender"):
        ar["log_found"] = True
        ar["log_detail"] = f"AI分类器确认: {popup_text} (confidence {confidence:.0%})"
    else:
        ar["log_detail"] = f"AI分类器覆盖截图结果: {reason}"
    sj["av_analyze_result"] = ar

    ic = sj.get("image_compare_result") or {}
    ic["classification"] = new_classification.value
    if popup_kind in ("av_alert", "windows_defender"):
        ic["log_found"] = True
    sj["image_compare_result"] = ic


def _recalc_batch_summary(samples_data: list[dict]) -> dict:
    """Recalculate classification_counts and overall_classification for batch summary."""
    counts: dict[str, int] = {}
    for sd in samples_data:
        cls = sd.get("classification", "")
        counts[cls] = counts.get(cls, 0) + 1

    classifications = tuple(Classification(c) for c in counts.keys() for _ in range(counts[c]))
    from vm_auto_test.reporting import batch_classification

    overall = batch_classification(classifications)
    return {
        "total": sum(counts.values()),
        "classification_counts": counts,
        "overall_classification": overall.value,
    }


async def run_ai_check(
    report_dir: Path,
    *,
    api_key: str = "",
    model: str = "",
    base_url: str = "",
    api_format: str = "anthropic",
    verify_ssl: bool = True,
    dry_run: bool = False,
) -> tuple[list[AiCheckResult], Path]:
    """Run AI popup classifier on all eligible samples in a batch report.

    Returns (results, batch_json_path).
    """
    batch_json_path = report_dir / "result.json"
    if not batch_json_path.exists():
        raise FileNotFoundError(f"Batch result.json not found: {batch_json_path}")

    batch = json.loads(batch_json_path.read_text(encoding="utf-8-sig"))
    if batch.get("schema_version") != 2 or "samples" not in batch:
        raise ValueError("Not a batch result JSON (schema_version 2 required)")

    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("No API key. Set ANTHROPIC_API_KEY env var or pass --api-key")

    samples_data: list[dict] = batch["samples"]
    candidates: list[tuple[int, dict, Path, Path, dict, AvAnalyzeResult, DeferredImageResult]] = []

    for idx, sd in enumerate(samples_data):
        relative = sd.get("report_dir", "")
        sample_dir = report_dir / relative
        sample_json_path = sample_dir / "result.json"
        if not sample_json_path.exists():
            _LOGGER.warning("Sample result.json not found: %s", sample_json_path)
            continue

        sj, ar, ic = _load_sample_results(sample_dir)
        if _CANDIDATE_CONDITION(ar, ic):
            before_path = sample_dir / "screenshot_before.png"
            after_path = sample_dir / "screenshot_after.png"
            if before_path.exists() and after_path.exists():
                diff_summary = ic.value.screenshot_analysis or ""
                candidates.append((idx, sd, before_path, after_path, sj, ar, ic))
            else:
                _LOGGER.warning("Screenshots missing for %s, skipping", sd.get("id", relative))

    results: list[AiCheckResult] = []

    if not candidates:
        print(f"  无需 AI 检查 — 没有符合条件的样本（log=NOT_BLOCKED 且 image=BLOCKED）")
        return results, batch_json_path

    print(f"  检查 {len(samples_data)} 个样本，其中 {len(candidates)} 个需要 AI 确认...\n")

    for i, (idx, sd, before_path, after_path, sj, ar, ic) in enumerate(candidates):
        sample_id = sd.get("id", f"sample-{idx}")
        diff_summary = ic.value.screenshot_analysis or ""
        print(f"  [{i + 1}/{len(candidates)}] {sample_id}")
        print(f"    截图差异: {diff_summary[:80]}")

        if dry_run:
            print(f"    (dry-run) 将调用 AI 分类器...")
            results.append(AiCheckResult(
                sample_id=sample_id,
                screenshot_diff=diff_summary,
                ai_has_popup=False,
                ai_popup_kind="no_popup",
                ai_confidence=0.0,
                ai_reason="dry-run skipped",
                verdict_changed=False,
                new_classification=sj["classification"],
            ))
            continue

        try:
            popup = await classify_popup(
                before_path, after_path, diff_summary, api_key,
                model=model or _DEFAULT_MODEL,
                base_url=base_url or None,
                api_format=api_format,
                verify_ssl=verify_ssl,
            )
        except Exception as exc:
            _LOGGER.error("AI check failed for %s: %s", sample_id, exc)
            print(f"    AI 调用失败: {exc}")
            continue

        is_av = popup.has_popup and popup.popup_kind in ("av_alert", "windows_defender")
        old_cls = sj["classification"]

        if is_av:
            # AI confirms it IS an AV popup → upgrade to BLOCKED
            new_cls = Classification.AV_ANALYZE_BLOCKED
            print(f"    AI 判定: ✓ AV popup ({popup.popup_kind}, confidence {popup.confidence:.0%})")
            print(f"    结果: {old_cls} → {new_cls.value} ✓ 确认拦截")
        else:
            # AI says it's NOT an AV popup → override image BLOCKED, keep NOT_BLOCKED
            new_cls = Classification.AV_ANALYZE_NOT_BLOCKED
            print(f"    AI 判定: ✗ NOT an AV popup — {popup.reason}")
            print(f"    结果: 保持 {new_cls.value} (AI 覆盖了截图 BLOCKED)")

        _update_sample_json(sj, new_cls, popup.popup_kind, popup.popup_text,
                           popup.confidence, popup.reason)

        # Write per-sample result.json
        sample_json_path = report_dir / sd.get("report_dir", "") / "result.json"
        sample_json_path.write_text(
            json.dumps(sj, ensure_ascii=False, indent=2), encoding="utf-8-sig",
        )

        # Sync batch sample entry
        sd["classification"] = new_cls.value
        sd["av_analyze_result"] = sj["av_analyze_result"]
        sd["image_compare_result"] = sj["image_compare_result"]

        results.append(AiCheckResult(
            sample_id=sample_id,
            screenshot_diff=diff_summary,
            ai_has_popup=popup.has_popup,
            ai_popup_kind=popup.popup_kind,
            ai_confidence=popup.confidence,
            ai_reason=popup.reason,
            verdict_changed=(old_cls != new_cls.value),
            new_classification=new_cls.value,
        ))

    # Recalculate batch summary
    batch["summary"] = _recalc_batch_summary(samples_data)

    if not dry_run:
        # Write batch result.json
        batch_json_path.write_text(
            json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8-sig",
        )

        # Regenerate HTML
        html_path = report_dir / "result.html"
        from vm_auto_test.reporting import write_batch_html_from_json
        write_batch_html_from_json(batch_json_path, html_path)
        print(f"  result.html 已更新: {html_path}")

    print(f"\n  ---")
    changed = [r for r in results if r.verdict_changed]
    confirmed = [r for r in results if r.ai_has_popup and r.ai_popup_kind in ("av_alert", "windows_defender")]
    print(f"  修正: {len(changed)} 个 (AI覆盖截图误判)")
    print(f"  确认拦截: {len(confirmed)} 个")
    unchanged = len(results) - len(changed) - len(confirmed)
    if unchanged > 0:
        print(f"  不变: {unchanged} 个")

    return results, batch_json_path
