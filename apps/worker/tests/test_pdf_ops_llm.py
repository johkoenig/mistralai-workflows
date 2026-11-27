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


class TestNamingRulesAreAgentsMdDriven:
    @pytest.mark.parametrize(
        ("folder", "expected"),
        [
            (
                "/Dokumente/Scans/Inbox",
                [
                    "/Dokumente/Scans/Inbox/AGENTS.md",
                    "/Dokumente/Scans/AGENTS.md",
                    "/Dokumente/AGENTS.md",
                    "/AGENTS.md",
                ],
            ),
            ("/Dokumente", ["/Dokumente/AGENTS.md", "/AGENTS.md"]),
            ("SCANNER_INBOX", ["SCANNER_INBOX/AGENTS.md", "AGENTS.md"]),
            ("/", ["/AGENTS.md"]),
        ],
    )
    def test_agents_md_candidates_walk_up_to_root(self, folder, expected):
        assert pdf_ops.agents_md_candidates(folder) == expected

    def test_base_prompt_is_generic_about_the_name_schema(self):
        # The file name schema is NOT part of the project: it must come from
        # AGENTS.md rules, not from the base prompt.
        assert "YYYY-MM-DD short descriptive title.pdf" not in pdf_ops.SYSTEM_PROMPT
        assert "AGENTS.md" in pdf_ops.SYSTEM_PROMPT

    async def test_name_document_appends_agents_md_rules_with_precedence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chat = _FakeChat(
            json.dumps({"date": "2026-11-22", "title": "Brief", "confidence": 0.9})
        )
        _patch_client(monkeypatch, chat)

        await pdf_ops.name_document(
            "some text", "mistral-small-latest", "Schema: YYYY-MM-DD Titel.pdf"
        )

        system = chat.calls[0]["messages"][0]["content"]
        assert "Schema: YYYY-MM-DD Titel.pdf" in system
        assert "take precedence" in system

    async def test_load_naming_rules_falls_back_to_parent_folder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pdf_ops, "file_client", lambda connector="": object())

        async def fake_read(nc: object, path: str) -> str | None:
            return "Ordner-Regeln" if path == "/Dokumente/AGENTS.md" else None

        monkeypatch.setattr(pdf_ops, "_read_rules_file", fake_read)

        result = await pdf_ops.load_naming_rules("/Dokumente/Scans/Inbox")

        assert result == {"rules": "Ordner-Regeln", "source": "/Dokumente/AGENTS.md"}

    async def test_load_naming_rules_empty_when_no_agents_md_anywhere(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pdf_ops, "file_client", lambda connector="": object())

        async def fake_read(nc: object, path: str) -> str | None:
            return None

        monkeypatch.setattr(pdf_ops, "_read_rules_file", fake_read)

        result = await pdf_ops.load_naming_rules("/Dokumente/Scans/Inbox")

        assert result == {"rules": "", "source": None}


def test_split_prompt_contains_document_boundary_heuristics():
    prompt = pdf_ops.SPLIT_SYSTEM_PROMPT
    # date change, running page numbers, company letterhead, third-party
    # exception, and self-verification of planned boundaries
    assert "date changes" in prompt
    assert "Seite 1 von 2" in prompt and "Page 1 of 2" in prompt
    assert "company name" in prompt and "contract partner" in prompt
    assert "im Namen von XY" in prompt and "on behalf of XY" in prompt
    assert "Verify every planned boundary" in prompt


def test_max_concurrent_ocr_env_override():
    import os
    import subprocess
    import sys

    code = "from worker import pdf_ops; print(pdf_ops.MAX_CONCURRENT_OCR)"
    env = dict(os.environ)
    env["MAX_CONCURRENT_OCR"] = "4"
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "4"
    env.pop("MAX_CONCURRENT_OCR")
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "2"


def test_ocr_quality_metrics_real_text_is_confident():
    pages = [
        "Rechnung Nr. 4711 vom 18.11.2026 an Beispielpraxis GmbH, Musterweg 12, 88048 Friedrichshafen",
        "Vielen Dank fuer Ihren Auftrag. Wir berechnen 24,93 EUR brutto inkl. 19 Prozent Mehrwertsteuer.",
    ]
    result = pdf_ops.ocr_quality_metrics(pages)
    assert result["low_confidence"] is False
    assert result["confidence"] >= pdf_ops.OCR_QUALITY_MIN_CONFIDENCE
    assert result["metrics"]["empty_pages"] == 0


def test_ocr_quality_metrics_garbage_is_low_confidence():
    garbage = ["x x x x x % & / ( ) = ?", "", "  ", "wwwwwwwwwwwwwwwwwwwwwwwwwwww"]
    result = pdf_ops.ocr_quality_metrics(garbage)
    assert result["low_confidence"] is True
    assert result["confidence"] < pdf_ops.OCR_QUALITY_MIN_CONFIDENCE
    assert result["metrics"]["empty_pages"] == 2


def test_ocr_quality_metrics_empty_document():
    result = pdf_ops.ocr_quality_metrics(["", ""])
    assert result["low_confidence"] is True
    assert result["metrics"]["empty_pages"] == 2
