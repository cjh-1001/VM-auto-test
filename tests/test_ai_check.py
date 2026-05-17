from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from vm_auto_test.commands.ai_check import (
    AiCheckResult,
    _CANDIDATE_CONDITION,
    _load_sample_results,
    _update_sample_json,
    _recalc_batch_summary,
    run_ai_check,
)
from vm_auto_test.models import AvAnalyzeResult, Classification, DeferredImageResult


# ── Candidate condition tests ─────────────────────────────────────────

def test_candidate_condition_matches_log_not_blocked_image_blocked():
    ar = AvAnalyzeResult(log_found=False, classification=Classification.AV_ANALYZE_NOT_BLOCKED)
    ic = DeferredImageResult(value=AvAnalyzeResult(
        log_found=False, classification=Classification.AV_ANALYZE_BLOCKED,
    ))
    assert _CANDIDATE_CONDITION(ar, ic) is True


def test_candidate_condition_skips_log_blocked():
    ar = AvAnalyzeResult(log_found=True, classification=Classification.AV_ANALYZE_BLOCKED)
    ic = DeferredImageResult(value=AvAnalyzeResult(
        log_found=False, classification=Classification.AV_ANALYZE_BLOCKED,
    ))
    assert _CANDIDATE_CONDITION(ar, ic) is False


def test_candidate_condition_skips_image_not_blocked():
    ar = AvAnalyzeResult(log_found=False, classification=Classification.AV_ANALYZE_NOT_BLOCKED)
    ic = DeferredImageResult(value=AvAnalyzeResult(
        log_found=False, classification=Classification.AV_ANALYZE_NOT_BLOCKED,
    ))
    assert _CANDIDATE_CONDITION(ar, ic) is False


def test_candidate_condition_skips_none():
    assert _CANDIDATE_CONDITION(None, None) is False


# ── Sample JSON loading ───────────────────────────────────────────────

def test_load_sample_results(tmp_path):
    sj = {
        "classification": "AV_ANALYZE_NOT_BLOCKED",
        "av_analyze_result": {
            "log_found": False,
            "log_detail": "no threat",
            "classification": "AV_ANALYZE_NOT_BLOCKED",
        },
        "image_compare_result": {
            "log_found": False,
            "screenshot_analysis": "21.7% diff",
            "classification": "AV_ANALYZE_BLOCKED",
        },
    }
    (tmp_path / "result.json").write_text(json.dumps(sj), encoding="utf-8-sig")
    loaded, ar, ic = _load_sample_results(tmp_path)
    assert loaded["classification"] == "AV_ANALYZE_NOT_BLOCKED"
    assert ar.classification == Classification.AV_ANALYZE_NOT_BLOCKED
    assert ic.value.classification == Classification.AV_ANALYZE_BLOCKED


# ── JSON update ───────────────────────────────────────────────────────

def test_update_sample_json_av_alert():
    sj = {
        "classification": "AV_ANALYZE_NOT_BLOCKED",
        "av_analyze_result": {"log_found": False, "log_detail": "", "classification": "AV_ANALYZE_NOT_BLOCKED"},
        "image_compare_result": {"log_found": False, "screenshot_analysis": "diff", "classification": "AV_ANALYZE_BLOCKED"},
    }
    _update_sample_json(sj, Classification.AV_ANALYZE_BLOCKED, "av_alert", "火绒已拦截", 0.92, "clear av popup")
    assert sj["classification"] == "AV_ANALYZE_BLOCKED"
    assert sj["av_analyze_result"]["log_found"] is True
    assert "火绒已拦截" in sj["av_analyze_result"]["log_detail"]


def test_update_sample_json_non_av():
    sj = {
        "classification": "AV_ANALYZE_BLOCKED",
        "av_analyze_result": {"log_found": False, "log_detail": "", "classification": "AV_ANALYZE_NOT_BLOCKED"},
        "image_compare_result": {"log_found": False, "screenshot_analysis": "diff", "classification": "AV_ANALYZE_BLOCKED"},
    }
    _update_sample_json(sj, Classification.AV_ANALYZE_NOT_BLOCKED, "no_popup", "", 0.95, "只是cmd窗口")
    assert sj["classification"] == "AV_ANALYZE_NOT_BLOCKED"
    assert "AI分类器覆盖" in sj["av_analyze_result"]["log_detail"]


# ── Batch summary recalculation ───────────────────────────────────────

def test_recalc_batch_summary():
    samples = [
        {"classification": "AV_ANALYZE_BLOCKED"},
        {"classification": "AV_ANALYZE_BLOCKED"},
        {"classification": "AV_ANALYZE_NOT_BLOCKED"},
    ]
    summary = _recalc_batch_summary(samples)
    assert summary["total"] == 3
    assert summary["classification_counts"]["AV_ANALYZE_BLOCKED"] == 2
    assert summary["classification_counts"]["AV_ANALYZE_NOT_BLOCKED"] == 1
    assert summary["overall_classification"] == "AV_ANALYZE_NOT_BLOCKED"


# ── End-to-end dry-run ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_ai_check_dry_run(tmp_path):
    # Build a minimal batch report
    sample_dir = tmp_path / "samples" / "test-sample"
    sample_dir.mkdir(parents=True)

    sample_json = {
        "schema_version": 2,
        "classification": "AV_ANALYZE_NOT_BLOCKED",
        "av_analyze_result": {
            "log_found": False,
            "log_detail": "日志无变化",
            "classification": "AV_ANALYZE_NOT_BLOCKED",
        },
        "image_compare_result": {
            "log_found": False,
            "screenshot_analysis": "截图差异显著：21.7% 像素不同",
            "classification": "AV_ANALYZE_BLOCKED",
        },
    }
    (sample_dir / "result.json").write_text(json.dumps(sample_json), encoding="utf-8-sig")
    (sample_dir / "screenshot_before.png").write_bytes(b"\x89PNGfake")
    (sample_dir / "screenshot_after.png").write_bytes(b"\x89PNGfake")

    batch_json = {
        "schema_version": 2,
        "mode": "av_analyze",
        "samples": [
            {
                "id": "test-sample",
                "report_dir": "samples/test-sample",
                "classification": "AV_ANALYZE_NOT_BLOCKED",
                "av_analyze_result": sample_json["av_analyze_result"],
                "image_compare_result": sample_json["image_compare_result"],
            }
        ],
        "summary": {
            "total": 1,
            "classification_counts": {"AV_ANALYZE_NOT_BLOCKED": 1},
            "overall_classification": "AV_ANALYZE_NOT_BLOCKED",
            "duration_seconds": 45.0,
        },
    }
    (tmp_path / "result.json").write_text(json.dumps(batch_json), encoding="utf-8-sig")

    results, path = await run_ai_check(tmp_path, api_key="test-key", dry_run=True)

    assert len(results) == 1
    assert results[0].sample_id == "test-sample"
    assert results[0].verdict_changed is False  # dry-run never changes
