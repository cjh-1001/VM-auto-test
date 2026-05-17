from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5"

_POPUP_CLASSIFIER_PROMPT = """\
你是一个安全测试自动化系统的截图分析模块。你的任务是：对比两张虚拟机桌面截图，判断样本执行后出现的变化是不是杀毒软件的拦截弹窗。

## 背景

自动化像素对比发现两张截图存在明显差异：{diff_ctx}。但像素差异不等于杀软拦截——可能只是打开了普通窗口、系统通知、命令行等。

## 输入

- 图1（before）：执行恶意样本前的桌面状态
- 图2（after）：执行恶意样本后的桌面状态

## 你的任务

对比两张截图，找出 after 相对于 before 的变化，然后判断这些变化中是否包含杀毒软件/安全软件的拦截弹窗。

## 判断标准

### 是杀软弹窗的信号（has_popup=true）：
- 出现了安全软件的警告窗口，包含"威胁"、"病毒"、"木马"、"拦截"、"查杀"、"隔离"、"风险"等字样
- 出现了 Windows Defender / 火绒 / 360 / 腾讯管家 等安全软件的明确 UI（如红色/橙色警告横幅、盾牌图标、威胁名称）
- 弹窗通常包含：威胁类型说明 + 文件路径 + 操作按钮（清除/隔离/信任/忽略）

### 不是杀软弹窗的变化（has_popup=false）：
- 命令行窗口（cmd.exe / powershell）打开或内容变化
- 文件资源管理器、记事本、注册表编辑器等系统工具窗口
- 应用程序正常界面、安装向导、更新提示
- 桌面图标位置变化、壁纸变化、任务栏变化
- 系统通知（右下角气泡）、输入法状态、网络连接提示
- 任何没有明显安全警告语义的窗口或 UI 变化

### 不确定时：
- 如果截图模糊、变化区域太小看不清、文字无法辨认
- 如果弹窗来源不明、无法确认是否来自安全软件
- → 保守判断为 has_popup=false, popup_kind="no_popup"

## 分析步骤

1. 先找出 after 比 before 多了什么（新窗口、新图标、新文字）
2. 逐一判断每个新增元素是否与安全软件相关
3. 只有明确识别到安全软件拦截弹窗时才输出 has_popup=true

## 输出格式

严格只输出一行 JSON，不要输出任何其他文字：

{"has_popup": true或false, "popup_kind": "av_alert"或"windows_defender"或"other"或"no_popup", "popup_text": "弹窗中的关键文字，没有就留空", "confidence": 0.0到1.0之间的数字, "reason": "一句话说明判断依据"}"""


@dataclass(frozen=True)
class PopupClassification:
    has_popup: bool
    popup_kind: str  # av_alert / windows_defender / other / no_popup
    popup_text: str
    confidence: float
    reason: str


def _build_prompt(diff_summary: str) -> str:
    return _POPUP_CLASSIFIER_PROMPT.replace("{diff_ctx}", diff_summary)


def _parse_popup_response(text: str) -> PopupClassification:
    text = text.strip()

    # Strip markdown code blocks
    for fence in ("```json", "```"):
        if text.startswith(fence):
            text = text[len(fence):].strip()
            if text.endswith("```"):
                text = text[:-3].strip()
            break

    # Try exact parse first
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON object from within text
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                _LOGGER.warning("Failed to parse popup classifier response: %s", text[:200])
                return PopupClassification(
                    has_popup=False, popup_kind="no_popup", popup_text="",
                    confidence=0.0, reason="JSON parse failed — conservative fallback",
                )
        else:
            _LOGGER.warning("No JSON found in response: %s", text[:200])
            return PopupClassification(
                has_popup=False, popup_kind="no_popup", popup_text="",
                confidence=0.0, reason="No JSON found — conservative fallback",
            )

    return PopupClassification(
        has_popup=bool(data.get("has_popup", False)),
        popup_kind=str(data.get("popup_kind", "other" if data.get("has_popup") else "no_popup")),
        popup_text=str(data.get("popup_text", "")),
        confidence=float(data.get("confidence", 0.0)),
        reason=str(data.get("reason", "")),
    )


def _image_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _call_vision_api_raw(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    before_b64: str,
    after_b64: str,
    max_tokens: int = 2048,
    timeout: int = 120,
    api_format: str = "anthropic",
    verify_ssl: bool = True,
) -> str:
    """Call vision API using raw HTTPS socket.

    api_format: "anthropic" or "openai"
    """
    import ssl
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or 443

    # Avoid double version prefix when base_url already contains /v1
    base_path = parsed.path.rstrip("/")
    if base_path.endswith("/v1"):
        api_path_openai = base_path + "/chat/completions"
        api_path_anthropic = base_path + "/messages"
    else:
        api_path_openai = base_path + "/v1/chat/completions"
        api_path_anthropic = base_path + "/v1/messages"

    if api_format == "openai":
        path = api_path_openai
        body = json.dumps({
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + before_b64}},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + after_b64}},
                ],
            }],
        })
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"Content-Type: application/json\r\n"
            f"Authorization: Bearer {api_key}\r\n"
            f"Content-Length: {len(body.encode())}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
            f"{body}"
        )
    else:
        path = api_path_anthropic
        body = json.dumps({
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": before_b64}},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": after_b64}},
                ],
            }],
        })
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"Content-Type: application/json\r\n"
            f"x-api-key: {api_key}\r\n"
            f"anthropic-version: 2023-06-01\r\n"
            f"Content-Length: {len(body.encode())}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
            f"{body}"
        )

    ctx = ssl.create_default_context()
    if not verify_ssl:
        _LOGGER.warning("SSL verification disabled for %s — connection is insecure", host)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        ss = ctx.wrap_socket(sock, server_hostname=host)
        try:
            ss.settimeout(timeout)
            ss.sendall(request.encode())

            response = b""
            while True:
                chunk = ss.recv(65536)
                if not chunk:
                    break
                response += chunk
        finally:
            ss.close()
    finally:
        sock.close()

    # Split headers and body
    resp_text = response.decode(errors="replace")
    parts = resp_text.split("\r\n\r\n", 1)
    if len(parts) < 2:
        raise RuntimeError(f"Invalid HTTP response: {resp_text[:200]}")
    body_text = parts[1]

    # Handle chunked transfer encoding
    if "Transfer-Encoding: chunked" in parts[0]:
        lines = body_text.split("\r\n")
        unchunked: list[str] = []
        i = 0
        while i < len(lines):
            size_line = lines[i].strip()
            # Strip chunk extensions (RFC 7230: chunk-size[;ext-name=ext-value])
            if ";" in size_line:
                size_line = size_line.split(";")[0]
            try:
                size = int(size_line, 16)
            except ValueError:
                break
            if size == 0:
                break
            i += 1
            if i < len(lines):
                unchunked.append(lines[i][:size])
            i += 1
        body_text = "".join(unchunked)

    data = json.loads(body_text)
    if "error" in data:
        raise RuntimeError(f"API error: {data['error']}")

    if api_format == "openai":
        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
    else:
        content = data.get("content", [])
        if content:
            return content[0].get("text", "")
    raise RuntimeError(f"Unexpected response: {body_text[:200]}")


async def classify_popup(
    before_path: Path,
    after_path: Path,
    diff_summary: str,
    api_key: str,
    *,
    model: str = _DEFAULT_MODEL,
    base_url: str | None = None,
    api_format: str = "anthropic",
    verify_ssl: bool = True,
) -> PopupClassification:
    import asyncio

    if not before_path.exists():
        return PopupClassification(
            has_popup=False, popup_kind="no_popup", popup_text="",
            confidence=0.0, reason=f"before screenshot missing: {before_path}",
        )
    if not after_path.exists():
        return PopupClassification(
            has_popup=False, popup_kind="no_popup", popup_text="",
            confidence=0.0, reason=f"after screenshot missing: {after_path}",
        )

    prompt = _build_prompt(diff_summary)

    try:
        before_b64 = _image_b64(before_path)
        after_b64 = _image_b64(after_path)
    except Exception as exc:
        _LOGGER.warning("Failed to encode screenshots for popup classifier: %s", exc)
        return PopupClassification(
            has_popup=False, popup_kind="no_popup", popup_text="",
            confidence=0.0, reason=f"Image encoding error: {exc}",
        )

    try:
        if base_url:
            response_text = await asyncio.to_thread(
                _call_vision_api_raw,
                base_url, api_key, model, prompt, before_b64, after_b64,
                2048, 120, api_format, verify_ssl,
            )
        else:
            response_text = await asyncio.to_thread(
                _call_anthropic_sync, api_key, model, prompt, before_b64, after_b64,
            )
    except Exception as exc:
        _LOGGER.warning("Popup classifier API call failed: %s", exc)
        return PopupClassification(
            has_popup=False,
            popup_kind="no_popup",
            popup_text="",
            confidence=0.0,
            reason=f"API error: {exc}",
        )

    return _parse_popup_response(response_text)


def _call_anthropic_sync(
    api_key: str,
    model: str,
    prompt: str,
    before_b64: str,
    after_b64: str,
) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": before_b64}},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": after_b64}},
                ],
            }
        ],
    )
    return message.content[0].text
