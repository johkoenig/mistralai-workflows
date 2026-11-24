"""Tests for the LLM activities in pdf_ops: greedy sampling (temperature=0.0) must also send top_p=1, otherwise the Chat API rejects the request with HTTP 400 (invalid_request_greedy_sampling)."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from worker import pdf_ops


class _FakeChat:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        message = SimpleNamespace(role="assistant", content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _patch_client(monkeypatch: pytest.MonkeyPatch, chat: _FakeChat) -> None:
    monkeypatch.setattr(
        pdf_ops, "get_mistral_client", lambda: SimpleNamespace(chat=chat)
    )


async def test_name_document_sends_greedy_sampling_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = _FakeChat(
        json.dumps({"date": "2026-11-22", "title": "Brief", "confidence": 0.9})
    )
    _patch_client(monkeypatch, chat)

    result = await pdf_ops.name_document("some text", "mistral-large-latest")

    assert result["date"] == "2026-11-22"
    assert result["title"] == "Brief"
    assert chat.calls[0]["temperature"] == 0.0
    assert chat.calls[0]["top_p"] == 1


async def test_plan_split_sends_greedy_sampling_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = {
        "split_at": [2],
        "titles": ["A", "B"],
        "confidence": 0.9,
        "needs_input": False,
        "question": "",
    }
    chat = _FakeChat(json.dumps(plan))
    _patch_client(monkeypatch, chat)

    result = await pdf_ops.plan_split(
        ["page one", "page two", "page three"], 3, None, "mistral-large-latest", ""
    )

    assert result["split_at"] == [2]
    assert result["titles"] == ["A", "B"]
    assert chat.calls[0]["temperature"] == 0.0
    assert chat.calls[0]["top_p"] == 1


async def test_front_back_check_sends_greedy_sampling_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = _FakeChat(
        json.dumps(
            {
                "is_front_back": True,
                "confidence": 0.9,
                "reason": "gleiche Rechnungsnummer",
            }
        )
    )
    _patch_client(monkeypatch, chat)

    result = await pdf_ops.front_back_check(
        "front text", "back text", "mistral-small-latest"
    )

    assert result == {
        "is_front_back": True,
        "confidence": 0.9,
        "reason": "gleiche Rechnungsnummer",
    }
    assert chat.calls[0]["temperature"] == 0.0
    assert chat.calls[0]["top_p"] == 1


async def test_front_back_check_rejects_unparseable_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = _FakeChat("not json at all")
    _patch_client(monkeypatch, chat)

    result = await pdf_ops.front_back_check("a", "b", "mistral-small-latest")

    assert result["is_front_back"] is False
    assert result["confidence"] == 0.0
    assert result["reason"] == ""
