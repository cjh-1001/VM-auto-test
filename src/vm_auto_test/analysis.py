from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shlex
from pathlib import Path

from vm_auto_test.models import AvAnalyzeResult, AvAnalyzeSpec, Classification

_LOGGER = logging.getLogger(__name__)

_DEFAULT_LOG_PROMPT = (
    "以下是杀毒软件日志内容。请分析日志中是否有新的威胁检测/拦截记录（如病毒查杀、实时防护拦截、"
    "文件隔离等）。如果你发现任何表示杀软成功拦截恶意样本的记录，返回 blocked=true 并详细说明。"
    "如果日志为空或没有任何拦截记录，返回 blocked=false。"
    "\n\n日志内容：\n{log_content}"
    "\n\n请以JSON格式回复：{{\"blocked\": true/false, \"detail\": \"你的分析说明\", \"evidence\": \"关键证据行\"}}"
)

_DEFAULT_SCREENSHOT_PROMPT = (
    "请仔细对比这两张虚拟机桌面截图。\n\n"
    "图1（before）：执行恶意样本前的桌面状态\n"
    "图2（after）：执行恶意样本后的桌面状态\n\n"
    "请判断：\n"
    "1. 桌面上是否出现了杀毒软件的拦截弹窗、警告提示、或威胁通知？\n"
    "2. 样本是否似乎成功运行（例如打开了新窗口、出现了新的程序界面等）？\n"
    "3. 综合判断：杀毒软件是否成功拦截了恶意样本？\n\n"
    "请以JSON格式回复：{{\"blocked\": true/false, \"detail\": \"你的详细分析\", \"screenshots_differ\": true/false}}"
)


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:]) if lines else text
        if text.endswith("```"):
            text = text[:-3]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        _LOGGER.warning("Failed to parse response as JSON: %s", text[:200])
        return {}


def _get_api_key(api_key_env: str) -> str:
    if not api_key_env:
        api_key_env = "ANTHROPIC_API_KEY"
    key = os.environ.get(api_key_env, "")
    if not key:
        raise RuntimeError(
            f"AI分析需要API Key，请设置环境变量 {api_key_env}"
        )
    return key


# ── Built-in Claude API ────────────────────────────────────────────


def _call_claude_text(prompt: str, api_key: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _call_claude_vision(before_path: Path, after_path: Path, prompt: str, api_key: str) -> str:
    import anthropic

    def _image_b64(path: Path) -> str:
        return base64.b64encode(path.read_bytes()).decode("ascii")

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _image_b64(before_path)}},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _image_b64(after_path)}},
                ],
            }
        ],
    )
    return message.content[0].text


def _analyze_logs_builtin(log_content: str, config: AvAnalyzeSpec) -> AvAnalyzeResult:
    prompt_template = config.log_analysis_prompt or _DEFAULT_LOG_PROMPT
    prompt = prompt_template.format(log_content=log_content)
    api_key = _get_api_key(config.api_key_env)

    _LOGGER.info("Running built-in AI log analysis...")
    response = _call_claude_text(prompt, api_key)
    parsed = _parse_json_response(response)

    blocked = parsed.get("blocked", False)
    detail = parsed.get("detail", response[:500])
    return AvAnalyzeResult(
        log_found=blocked,
        log_detail=detail,
        screenshot_analysis=None,
        classification=Classification.AV_ANALYZE_BLOCKED if blocked else Classification.AV_ANALYZE_NOT_BLOCKED,
    )


async def _analyze_screenshots_builtin(
    before_path: Path,
    after_path: Path,
    config: AvAnalyzeSpec,
) -> str:
    prompt = config.screenshot_analysis_prompt or _DEFAULT_SCREENSHOT_PROMPT
    api_key = _get_api_key(config.api_key_env)

    _LOGGER.info("Running built-in AI screenshot analysis...")
    return _call_claude_vision(before_path, after_path, prompt, api_key)


# ── External CLI analyzer ──────────────────────────────────────────


async def _run_analyzer_cli(
    command_template: str,
    log_file: Path,
    before_screenshot: Path,
    after_screenshot: Path,
    report_dir: Path,
) -> AvAnalyzeResult:
    cmd = (
        command_template
        .replace("{log_file}", shlex.quote(str(log_file)) if log_file.exists() else "")
        .replace("{before_screenshot}", shlex.quote(str(before_screenshot)) if before_screenshot.exists() else "")
        .replace("{after_screenshot}", shlex.quote(str(after_screenshot)) if after_screenshot.exists() else "")
        .replace("{report_dir}", shlex.quote(str(report_dir)))
    )

    _LOGGER.info("Running analyzer CLI: %s", cmd)
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()

    if stderr_text:
        _LOGGER.warning("Analyzer CLI stderr: %s", stderr_text[:500])

    parsed = _parse_json_response(stdout_text)
    if not parsed:
        return AvAnalyzeResult(
            log_found=False,
            log_detail=f"CLI analyzer exit={proc.returncode}, stdout={stdout_text[:300]}, stderr={stderr_text[:200]}",
            classification=Classification.AV_ANALYZE_NOT_BLOCKED,
        )

    blocked = parsed.get("blocked", False)
    log_found = parsed.get("log_found", blocked)
    detail = parsed.get("detail", "")
    screenshot_analysis = parsed.get("screenshot_analysis")
    return AvAnalyzeResult(
        log_found=log_found,
        log_detail=detail,
        screenshot_analysis=screenshot_analysis,
        classification=Classification.AV_ANALYZE_BLOCKED if blocked else Classification.AV_ANALYZE_NOT_BLOCKED,
    )


# ── Public API ─────────────────────────────────────────────────────


def has_analyzer_cli(config: AvAnalyzeSpec) -> bool:
    return bool(config.analyzer_command)


async def run_analysis(
    config: AvAnalyzeSpec,
    log_content: str,
    log_file: Path,
    before_screenshot: Path,
    after_screenshot: Path,
    report_dir: Path,
) -> AvAnalyzeResult:
    """Run the full analysis: logs first, then screenshots if needed.

    Uses external CLI if ``analyzer_command`` is configured, otherwise
    the built-in Claude API.
    """
    if config.analyzer_command:
        # For external CLI, delegate the full analysis — the CLI decides
        # whether to check logs first or go straight to screenshots.
        return await _run_analyzer_cli(
            config.analyzer_command,
            log_file,
            before_screenshot,
            after_screenshot,
            report_dir,
        )

    # Built-in Claude API path
    if log_content.strip():
        result = _analyze_logs_builtin(log_content, config)
        if result.log_found:
            return result

    if before_screenshot.exists() and after_screenshot.exists():
        response = await _analyze_screenshots_builtin(before_screenshot, after_screenshot, config)
        parsed = _parse_json_response(response)
        blocked = parsed.get("blocked", False)
        detail = parsed.get("detail", response[:500])
        return AvAnalyzeResult(
            log_found=False,
            log_detail="",
            screenshot_analysis=detail,
            classification=Classification.AV_ANALYZE_BLOCKED if blocked else Classification.AV_ANALYZE_NOT_BLOCKED,
        )

    return AvAnalyzeResult(
        log_found=False,
        log_detail="无日志且无截图可分析",
        classification=Classification.AV_ANALYZE_NOT_BLOCKED,
    )
