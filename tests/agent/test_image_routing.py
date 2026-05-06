"""Tests for agent.image_routing — decide_image_input_mode and build_native_content_parts."""

import asyncio
import json

import pytest

from agent.image_routing import (
    IMAGE_ONLY_REPLY_GUIDANCE,
    IMAGE_ONLY_USER_MESSAGE,
    _LEGACY_IMAGE_ONLY_USER_MESSAGE_CN,
    build_native_content_parts,
    decide_image_input_mode,
    is_synthetic_image_only_user_text,
)


@pytest.mark.parametrize("forced,expected", [("native", "native"), ("text", "text"), ("TEXT", "text")])
def test_decide_image_input_mode_forced_via_config(forced, expected):
    cfg = {"agent": {"image_input_mode": forced}}
    assert decide_image_input_mode("openai", "gpt-5", cfg) == expected


def test_decide_image_input_mode_empty_credentials():
    assert decide_image_input_mode("", "gpt-5", {}) == "text"
    assert decide_image_input_mode("openai", "", {}) == "text"


def test_decide_image_input_mode_text_when_capabilities_missing(monkeypatch):
    monkeypatch.setattr(
        "agent.models_dev.get_model_capabilities",
        lambda *_: None,
    )
    assert decide_image_input_mode("openai", "nonexistent-mm-test", {}) == "text"


def test_decide_image_input_mode_native_when_capabilities_support_vision(monkeypatch):

    class _Caps:
        supports_vision = True

    monkeypatch.setattr(
        "agent.models_dev.get_model_capabilities",
        lambda *_: _Caps(),
    )
    assert decide_image_input_mode("openai", "gpt-test-mm-vision", {}) == "native"


def test_decide_image_input_mode_text_when_capabilities_no_vision(monkeypatch):

    class _Caps:
        supports_vision = False

    monkeypatch.setattr(
        "agent.models_dev.get_model_capabilities",
        lambda *_: _Caps(),
    )
    assert decide_image_input_mode("openai", "gpt-test-plain", {}) == "text"


def test_build_native_encodes_readable_jpeg(tmp_path):
    img = tmp_path / "tiny.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)

    parts, skipped = build_native_content_parts("Hello", [str(img)])
    assert skipped == []
    assert parts[0] == {"type": "text", "text": "Hello"}
    assert parts[1]["type"] == "image_url"
    url = parts[1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")


def test_build_native_skips_missing_file(tmp_path):
    missing = tmp_path / "gone.png"

    parts, skipped = build_native_content_parts("", [str(missing)])
    assert parts == []
    assert skipped == [str(missing)]


def test_gateway_enrich_image_only_drops_synthetic_for_guidance(monkeypatch, tmp_path):
    """Synthetic image-only caption must not suppress the follow-up guidance."""
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.run import GatewayRunner

    img = tmp_path / "photo.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"z" * 40)

    async def _fake_vision(*args, image_url=None, **kwargs):
        assert str(image_url) == str(img)
        return json.dumps({"success": True, "analysis": "red circle"})

    monkeypatch.setattr("tools.vision_tools.vision_analyze_tool", _fake_vision)

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="x")})

    msg = asyncio.run(
        runner._enrich_message_with_vision(
            IMAGE_ONLY_USER_MESSAGE,
            [str(img)],
        )
    )

    assert IMAGE_ONLY_REPLY_GUIDANCE in msg
    assert "[The user sent an image. Description:" in msg
    assert IMAGE_ONLY_USER_MESSAGE.strip() not in msg


def test_gateway_enrich_preserves_real_user_caption(monkeypatch, tmp_path):
    """When the sender supplies text, prepend vision but don't inject image-only guidance."""
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.run import GatewayRunner

    img = tmp_path / "photo.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"z" * 40)

    async def _fake_vision(*args, **kwargs):
        return json.dumps({"success": True, "analysis": "x"})

    monkeypatch.setattr("tools.vision_tools.vision_analyze_tool", _fake_vision)

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="x")})

    msg = asyncio.run(runner._enrich_message_with_vision("请 OCR", [str(img)]))
    assert "请 OCR" in msg
    assert IMAGE_ONLY_REPLY_GUIDANCE not in msg


def test_is_synthetic_image_only_recognizes_shared_channel_prefix():
    prefixed = f"[Alice] {IMAGE_ONLY_USER_MESSAGE}"
    assert is_synthetic_image_only_user_text(prefixed) is True


def test_is_synthetic_image_only_recognizes_legacy_cn():
    assert is_synthetic_image_only_user_text(_LEGACY_IMAGE_ONLY_USER_MESSAGE_CN.strip()) is True
