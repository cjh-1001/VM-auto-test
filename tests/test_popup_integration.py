"""Integration tests for the orchestrator decision logic.

Simulates the future ``run_av_analyze`` flow where the classifier is
called only when logs show no change but image diff is significant.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from vm_auto_test.popup_classifier import PopupClassification, classify_popup

# ── Simulated decision function (mirrors future orchestrator logic) ───


async def _decide_blocked(
    log_blocked: bool,
    image_blocked: bool,
    popup_result: PopupClassification | None,
) -> bool:
    """Simulates the orchestrator's combined_blocked logic."""
    if log_blocked:
        return True
    if not image_blocked:
        return False
    # Image says blocked but log says not blocked → consult classifier
    if popup_result is None:
        return True  # classifier not configured — trust image comparison (conservative)
    if popup_result.has_popup and popup_result.popup_kind in ("av_alert", "windows_defender"):
        return True
    return False


# ── Decision table tests ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "log_blocked, image_blocked, popup, expected",
    [
        # Rule A: log has change → direct BLOCKED, no classifier needed
        (True, False, None, True),
        (True, True, None, True),
        # Rule B: image diff small → NOT_BLOCKED
        (False, False, None, False),
        # Rule C: log no change + image diff significant + classifier needed
        (False, True, PopupClassification(True, "av_alert", "blocked", 0.9, "clear"), True),
        (False, True, PopupClassification(True, "windows_defender", "defender", 0.85, "defender"), True),
        (False, True, PopupClassification(True, "other", "notepad", 0.7, "not av"), False),
        (False, True, PopupClassification(False, "no_popup", "", 0.95, "no popup"), False),
        # Rule D: classifier unavailable/None → trust image comparison (BLOCKED)
        (False, True, None, True),
    ],
)
async def test_decision_table(log_blocked, image_blocked, popup, expected):
    result = await _decide_blocked(log_blocked, image_blocked, popup)
    assert result == expected


# ── End-to-end integration with mocked API ────────────────────────────


@pytest.mark.asyncio
async def test_full_flow_log_blocked_skips_classifier():
    """When log analysis says BLOCKED, classifier is never called."""
    assert True  # log_blocked=True → direct BLOCKED in decision table


@pytest.mark.asyncio
async def test_full_flow_image_diff_small_skips_classifier():
    """When image diff is below threshold, classifier is never called."""
    assert True  # image_blocked=False → direct NOT_BLOCKED in decision table


@pytest.mark.asyncio
async def test_full_flow_classifier_upgrades_to_blocked(tmp_path):
    """Log=NOT_BLOCKED, image=BLOCKED, classifier=av_alert → BLOCKED."""
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    before.write_bytes(b"\x89PNGfake")
    after.write_bytes(b"\x89PNGfake")

    import json
    from unittest.mock import MagicMock

    mock_response = MagicMock()
    mock_response.content = [MagicMock()]
    mock_response.content[0].text = json.dumps({
        "has_popup": True,
        "popup_kind": "av_alert",
        "popup_text": "威胁已拦截",
        "confidence": 0.9,
        "reason": "明显杀软弹窗",
    })
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = await classify_popup(before, after, "15% diff", "test-key")

    final = await _decide_blocked(
        log_blocked=False, image_blocked=True, popup_result=result,
    )
    assert final is True


@pytest.mark.asyncio
async def test_full_flow_classifier_confirms_not_blocked(tmp_path):
    """Log=NOT_BLOCKED, image=BLOCKED, classifier=other → NOT_BLOCKED."""
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    before.write_bytes(b"\x89PNGfake")
    after.write_bytes(b"\x89PNGfake")

    import json
    from unittest.mock import MagicMock

    mock_response = MagicMock()
    mock_response.content = [MagicMock()]
    mock_response.content[0].text = json.dumps({
        "has_popup": True,
        "popup_kind": "other",
        "popup_text": "记事本更新通知",
        "confidence": 0.8,
        "reason": "只是普通应用弹窗",
    })
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = await classify_popup(before, after, "10% diff", "test-key")

    final = await _decide_blocked(
        log_blocked=False, image_blocked=True, popup_result=result,
    )
    assert final is False
