from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vm_auto_test.popup_classifier import (
    PopupClassification,
    classify_popup,
    _build_prompt,
    _parse_popup_response,
    _POPUP_CLASSIFIER_PROMPT,
)


# ── Prompt tests ──────────────────────────────────────────────────────

def test_build_prompt_includes_diff_summary():
    prompt = _build_prompt("12.5% pixels differ, possible popup region")
    assert "before" in prompt.lower() or "截图" in prompt
    assert "after" in prompt.lower() or "截图" in prompt
    assert "12.5%" in prompt
    assert "弹窗" in prompt


def test_build_prompt_requires_json_output():
    prompt = _build_prompt("some diff")
    assert "JSON" in prompt


# ── Response parsing tests ────────────────────────────────────────────

def test_parse_recognizes_av_popup():
    response = json.dumps({
        "has_popup": True,
        "popup_kind": "av_alert",
        "popup_text": "检测到威胁: Trojan.Win32.Generic",
        "confidence": 0.9,
        "reason": "弹窗内容包含威胁检测、隔离等杀软特征",
    })
    result = _parse_popup_response(response)
    assert result.has_popup is True
    assert result.popup_kind == "av_alert"
    assert result.confidence == 0.9
    assert "Trojan" in result.popup_text


def test_parse_recognizes_non_av_popup():
    response = json.dumps({
        "has_popup": True,
        "popup_kind": "other",
        "popup_text": "Windows Update 已完成",
        "confidence": 0.85,
        "reason": "这是系统更新通知，不是安全软件弹窗",
    })
    result = _parse_popup_response(response)
    assert result.has_popup is True
    assert result.popup_kind == "other"


def test_parse_recognizes_no_popup():
    response = json.dumps({
        "has_popup": False,
        "popup_kind": "no_popup",
        "popup_text": "",
        "confidence": 0.95,
        "reason": "两张截图仅有桌面图标位置微小变化，无弹窗",
    })
    result = _parse_popup_response(response)
    assert result.has_popup is False
    assert result.popup_kind == "no_popup"


def test_parse_strips_markdown_code_block():
    response = '```json\n{"has_popup": false, "popup_kind": "no_popup", "popup_text": "", "confidence": 0.9, "reason": "none"}\n```'
    result = _parse_popup_response(response)
    assert result.has_popup is False


def test_parse_returns_conservative_on_invalid_json():
    result = _parse_popup_response("not valid json at all")
    assert result.has_popup is False
    assert result.popup_kind == "no_popup"
    assert result.confidence == 0.0
    assert "json" in result.reason.lower()


def test_parse_returns_conservative_on_empty():
    result = _parse_popup_response("")
    assert result.has_popup is False


def test_parse_returns_conservative_on_missing_fields():
    response = json.dumps({"has_popup": True})
    result = _parse_popup_response(response)
    assert result.has_popup is True
    assert result.popup_kind == "other"


# ── classify_popup integration tests ──────────────────────────────────

@pytest.mark.asyncio
async def test_classify_popup_calls_api_and_returns_result(tmp_path):
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    before.write_bytes(b"\x89PNGfake")
    after.write_bytes(b"\x89PNGfake")

    mock_response = MagicMock()
    mock_response.content = [MagicMock()]
    mock_response.content[0].text = json.dumps({
        "has_popup": True,
        "popup_kind": "av_alert",
        "popup_text": "360安全卫士已拦截威胁",
        "confidence": 0.92,
        "reason": "弹窗明确显示安全软件拦截信息",
    })

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = await classify_popup(before, after, "diff 15%", api_key="test-key")

    assert result.has_popup is True
    assert result.popup_kind == "av_alert"
    mock_client.messages.create.assert_called_once()


@pytest.mark.asyncio
async def test_classify_popup_handles_api_error_gracefully(tmp_path):
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    before.write_bytes(b"\x89PNGfake")
    after.write_bytes(b"\x89PNGfake")

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("API timeout")

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = await classify_popup(before, after, "diff", api_key="test-key")

    assert result.has_popup is False
    assert result.confidence == 0.0
    assert "error" in result.reason.lower()


@pytest.mark.asyncio
async def test_classify_popup_handles_missing_file(tmp_path):
    before = tmp_path / "missing.png"
    after = tmp_path / "also_missing.png"

    result = await classify_popup(before, after, "diff", api_key="test-key")

    assert result.has_popup is False
    assert "missing" in result.reason.lower() or "exist" in result.reason.lower()


def test_popup_classification_is_immutable():
    pc = PopupClassification(
        has_popup=True,
        popup_kind="av_alert",
        popup_text="blocked",
        confidence=0.8,
        reason="clear av popup",
    )
    with pytest.raises(Exception):
        pc.has_popup = False  # type: ignore[misc]
