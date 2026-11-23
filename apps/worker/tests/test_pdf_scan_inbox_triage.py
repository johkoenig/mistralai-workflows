"""Tests for the pdf-scan-inbox-triage workflow: pure planning logic, duplex side naming, and a full run with faked I/O activities."""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from mistralai import workflows
from mistralai.workflows.testing import create_test_worker
from pypdf import PdfReader, PdfWriter
from worker import pdf_ops
from worker.workflows import pdf_scan_inbox_triage
from worker.workflows.pdf_scan_inbox_triage import (
    PdfScanInboxTriageInput,
    PdfScanInboxTriageWorkflow,
)


def _file(
    name: str,
    order: int,
    size: int = 1000,
    page_count: int = 1,
    text: str = "",
    has_text: bool = True,
):
    return {
        "name": name,
        "path": f"SCANNER_INBOX/{name}",
        "size": size,
        "etag": None,
        "order": order,
        "page_count": page_count,
        "has_text": has_text,
        "ocr_done": False,
        "text": text,
    }


class TestPureHelpers:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Scan.pdf", -1),
            ("Scan0000.pdf", 0),
            ("Scan0012.pdf", 12),
            ("scan0001.PDF", 1),
            ("Scan123.pdf", None),
            ("Scan00001.pdf", None),
            ("Scanner.pdf", None),
            ("2026-11-09 Rechnung.pdf", None),
        ],
    )
    def test_scan_order(self, name, expected):
        assert pdf_scan_inbox_triage.scan_order(name) == expected

    def test_sanitize_title_transliterates_and_strips(self):
        assert (
            pdf_ops.sanitize_title("Rechnung Börse GmbH.pdf") == "Rechnung Boerse GmbH"
        )
        assert (
            pdf_ops.sanitize_title("  A/B: C?  Straßennamen  ") == "A B C Strassennamen"
        )
        assert pdf_ops.sanitize_title("") == "Document"
        long_title = "x" * 200
        assert len(pdf_ops.sanitize_title(long_title)) <= pdf_ops.NAME_MAX_CHARS

    def test_front_back_suffixes_follow_document_language(self):
        assert pdf_ops.front_back_suffixes(
            "Briefkopf Praxis Datum 18.11.2026 Rechnungsnummer 4711"
        ) == ("Vorderseite", "Rueckseite")
        assert pdf_ops.front_back_suffixes("Börse GmbH, Überweisung") == (
            "Vorderseite",
            "Rueckseite",
        )
        assert pdf_ops.front_back_suffixes(
            "Letterhead invoice 4711 date 2026-11-18 payment"
        ) == ("Front", "Back")

    @pytest.mark.parametrize(
        ("date", "expected"),
        [
            ("2026-11-09", "2026-11-09"),
            ("2026-13-01", None),
            ("09.11.2026", None),
            ("1899-01-01", None),
            (None, None),
        ],
    )
    def test_valid_date(self, date, expected):
        assert pdf_ops.valid_date(date) == expected

    def test_build_filename(self):
        assert (
            pdf_ops.build_filename("2026-11-09", "UKV Versicherungsschein")
            == "2026-11-09 UKV Versicherungsschein.pdf"
        )
        assert pdf_ops.build_filename(None, "Notiz") == "Notiz.pdf"

    def test_unique_filename(self):
        taken = {"2026-11-09 A.pdf", "2026-11-09 A_2.pdf"}
        assert (
            pdf_ops.unique_filename("2026-11-09 A.pdf", taken) == "2026-11-09 A_3.pdf"
        )
        assert pdf_ops.unique_filename("2026-11-09 B.pdf", taken) == "2026-11-09 B.pdf"

    def test_text_similarity(self):
        a = "Rechnung der Beispielwerke vom 09.11.2026 Rechnungsnummer 4711"
        b = "Rechnung der Beispielwerke Seite 2, Datum 09.11.2026, Nummer 4711"
        assert pdf_ops.text_similarity(a, b) > 0.4
        assert (
            pdf_ops.text_similarity(a, "totally different content about something else")
            < 0.2
        )
        assert pdf_ops.text_similarity("", b) == 0.0

    def test_interleave_order(self):
        assert pdf_ops.interleave_order(3) == [
            ("front", 0),
            ("back", 2),
            ("front", 1),
            ("back", 1),
            ("front", 2),
            ("back", 0),
        ]


class TestDetectPairs:
    def test_consecutive_similar_files_pair(self):
        text = "Briefkopf Beispielpraxis Datum 18.11.2026 Adresse"
        files = [
            _file("Scan0000.pdf", 0, size=1000, page_count=2, text=text),
            _file(
                "Scan0001.pdf",
                1,
                size=1100,
                page_count=2,
                text=text + " Rueckseite Empfehlung",
            ),
        ]
        pairs = pdf_ops.detect_pairs(files)
        assert len(pairs) == 1
        assert pairs[0][0]["name"] == "Scan0000.pdf"
        assert pairs[0][1]["name"] == "Scan0001.pdf"

    def test_different_page_count_not_paired(self):
        files = [
            _file("Scan0000.pdf", 0, size=1000, page_count=1, text="aaa bbb ccc ddd"),
            _file("Scan0001.pdf", 1, size=1000, page_count=2, text="aaa bbb ccc ddd"),
        ]
        assert pdf_ops.detect_pairs(files) == []

    def test_dissimilar_text_not_paired(self):
        files = [
            _file(
                "Scan0000.pdf",
                0,
                size=1000,
                page_count=2,
                text="alpha beta gamma delta epsilon",
            ),
            _file(
                "Scan0001.pdf",
                1,
                size=1000,
                page_count=2,
                text="one two three four five six seven",
            ),
        ]
        assert pdf_ops.detect_pairs(files) == []

    def test_size_out_of_range_not_paired(self):
        text = "identical letterhead and footer text repeated on every page"
        files = [
            _file("Scan0000.pdf", 0, size=100, page_count=2, text=text),
            _file("Scan0001.pdf", 1, size=10000, page_count=2, text=text),
        ]
        assert pdf_ops.detect_pairs(files) == []

    def test_third_file_not_consumed_by_earlier_pair(self):
        text = "gemeinsamer Briefkopf Beispielpraxis 2026"
        files = [
            _file("Scan0000.pdf", 0, size=1000, page_count=1, text=text),
            _file("Scan0001.pdf", 1, size=1000, page_count=1, text=text),
            _file(
                "Scan0002.pdf",
                2,
                size=5000,
                page_count=3,
                text="völlig anderer Inhalt hier",
            ),
        ]
        pairs = pdf_ops.detect_pairs(files)
        assert len(pairs) == 1
        assert pairs[0][1]["name"] == "Scan0001.pdf"


class TestMerge:
    def test_merge_interleaves_back_reversed(self, tmp_path):
        def make_pdf(path, widths):
            writer = PdfWriter()
            for width in widths:
                writer.add_blank_page(width=width, height=100)
            with open(path, "wb") as handle:
                writer.write(handle)

        front = tmp_path / "front.pdf"
        back = tmp_path / "back.pdf"
        out = tmp_path / "merged.pdf"
        make_pdf(front, [10, 20, 30])  # front pages 1..3
        make_pdf(
            back, [31, 21, 11]
        )  # back pages scanned in reverse: back of sheet 3, 2, 1
        pages = pdf_ops.merge_pdf_files(str(front), str(back), str(out))
        assert pages == 6
        merged = PdfReader(str(out))
        widths = [float(page.mediabox.width) for page in merged.pages]
        assert widths == [10, 11, 20, 21, 30, 31]


def _write_calls(record_path: str, entry: dict) -> None:
    with open(record_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def _make_fakes(record_path: str, scans: list[dict], naming: dict) -> list:
    from typing import Any

    @workflows.activity(name="list-scan-files")
    async def fake_list(folder_path: str, connector: str = "") -> dict[str, Any]:
        return {
            "folder": folder_path,
            "scans": scans,
            "existing_names": ["AGENTS.md"] + [s["name"] for s in scans],
        }

    @workflows.activity(name="analyze-scan-file")
    async def fake_analyze(
        entry: dict[str, Any], apply_changes: bool, connector: str = ""
    ) -> dict[str, Any]:
        _write_calls(
            record_path,
            {"activity": "analyze", "path": entry["path"], "apply": apply_changes},
        )
        return entry

    @workflows.activity(name="name-document")
    async def fake_name(
        text: str, model: str, naming_rules: str = ""
    ) -> dict[str, Any]:
        _write_calls(
            record_path,
            {"activity": "name", "rules": naming_rules},
        )
        return naming

    @workflows.activity(name="load-naming-rules")
    async def fake_rules(folder: str, connector: str = "") -> dict[str, Any]:
        return {"rules": "", "source": None}

    @workflows.activity(name="backup-scan-file")
    async def fake_backup(from_path: str, connector: str = "") -> dict[str, Any]:
        backup = (
            f"SCANNER_INBOX/.originals/pdf-scan-inbox-triage/{from_path.split('/')[-1]}"
        )
        _write_calls(
            record_path, {"activity": "backup", "from": from_path, "backup": backup}
        )
        return {"from": from_path, "backup": backup}

    @workflows.activity(name="rename-scan-file")
    async def fake_rename(
        from_path: str, to_path: str, connector: str = ""
    ) -> dict[str, Any]:
        _write_calls(
            record_path, {"activity": "rename", "from": from_path, "to": to_path}
        )
        return {"from": from_path, "to": to_path}

    return [fake_list, fake_analyze, fake_name, fake_rules, fake_backup, fake_rename]


PAIR_TEXT = "Briefkopf Beispielpraxis Datum 18.11.2026 Rechnungsnummer 4711"


async def test_workflow_renames_single_and_pair_with_side_suffix(
    temporal_env, tmp_path, monkeypatch
):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
    scans = [
        _file(
            "Scan0000.pdf",
            0,
            size=900,
            page_count=1,
            text="einzelnes Dokument ganz anderer Inhalt xyz",
        ),
        _file("Scan0001.pdf", 1, size=1000, page_count=2, text=PAIR_TEXT),
        _file(
            "Scan0002.pdf", 2, size=1100, page_count=2, text=PAIR_TEXT + " rueckseite"
        ),
    ]
    naming = {"date": "2026-11-18", "title": "Test Rechnung", "confidence": 0.9}
    fakes = _make_fakes(record_path, scans, naming)
    async with create_test_worker(
        temporal_env, [PdfScanInboxTriageWorkflow], activities=fakes
    ):
        raw = await temporal_env.client.execute_workflow(
            PdfScanInboxTriageWorkflow.run,
            PdfScanInboxTriageInput(folder_path="SCANNER_INBOX", apply_changes=True),
            id="test-triage-apply",
            task_queue="test-task-queue",
            execution_timeout=timedelta(seconds=60),
        )
        result = raw["result"] if isinstance(raw, dict) and "result" in raw else raw

    actions = {a["action"]: a for a in result["actions"]}
    assert result["applied"] is True
    assert actions["renamed"]["files"] == ["Scan0000.pdf"]
    assert actions["renamed"]["to"] == "SCANNER_INBOX/2026-11-18 Test Rechnung.pdf"
    assert actions["renamed_pair"]["files"] == ["Scan0001.pdf", "Scan0002.pdf"]
    assert actions["renamed_pair"]["to"] == [
        "SCANNER_INBOX/2026-11-18 Test Rechnung Vorderseite.pdf",
        "SCANNER_INBOX/2026-11-18 Test Rechnung Rueckseite.pdf",
    ]

    calls = [
        json.loads(line)
        for line in Path(record_path).read_text(encoding="utf-8").splitlines()
    ]
    renames = [c for c in calls if c["activity"] == "rename"]
    assert len(renames) == 3
    # every renamed scan is parked under .originals/pdf-scan-inbox-triage/ first
    backups = [c for c in calls if c["activity"] == "backup"]
    assert len(backups) == 3
    assert all(
        b["backup"].startswith("SCANNER_INBOX/.originals/pdf-scan-inbox-triage/")
        for b in backups
    )
    for rename in renames:
        backup_before = [
            c
            for c in calls
            if c["activity"] == "backup" and c["from"] == rename["from"]
        ]
        assert backup_before, f"no backup before rename of {rename['from']}"
        assert calls.index(backup_before[0]) < calls.index(rename)
    assert actions["renamed"]["backups"] == [
        "SCANNER_INBOX/.originals/pdf-scan-inbox-triage/Scan0000.pdf"
    ]
    assert not any(c["activity"] == "merge" for c in calls)
    assert len([c for c in calls if c["activity"] == "analyze"]) == 3


async def test_workflow_leaves_uncertain_files_unchanged(
    temporal_env, tmp_path, monkeypatch
):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
    scans = [_file("Scan0000.pdf", 0, size=900, page_count=1, text="irgendein Text")]
    naming = {"date": None, "title": "", "confidence": 0.2}
    fakes = _make_fakes(record_path, scans, naming)
    async with create_test_worker(
        temporal_env, [PdfScanInboxTriageWorkflow], activities=fakes
    ):
        raw = await temporal_env.client.execute_workflow(
            PdfScanInboxTriageWorkflow.run,
            PdfScanInboxTriageInput(folder_path="SCANNER_INBOX", apply_changes=True),
            id="test-triage-uncertain",
            task_queue="test-task-queue",
            execution_timeout=timedelta(seconds=60),
        )
        result = raw["result"] if isinstance(raw, dict) and "result" in raw else raw

    assert result["actions"][0]["action"] == "left_unchanged"
    assert result["actions"][0]["files"] == ["Scan0000.pdf"]
    calls = [
        json.loads(line)
        for line in Path(record_path).read_text(encoding="utf-8").splitlines()
    ]
    assert not any(c["activity"] in ("rename", "backup", "merge") for c in calls)


async def test_workflow_no_scans(temporal_env, tmp_path, monkeypatch):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
    fakes = _make_fakes(record_path, [], {"date": None, "title": "", "confidence": 0.0})
    async with create_test_worker(
        temporal_env, [PdfScanInboxTriageWorkflow], activities=fakes
    ):
        raw = await temporal_env.client.execute_workflow(
            PdfScanInboxTriageWorkflow.run,
            PdfScanInboxTriageInput(folder_path="SCANNER_INBOX"),
            id="test-triage-empty",
            task_queue="test-task-queue",
            execution_timeout=timedelta(seconds=60),
        )
        result = raw["result"] if isinstance(raw, dict) and "result" in raw else raw

    assert result["actions"] == []
    assert result["note"] == "no scan files found"


def test_workflow_is_discovered():
    classes = workflows.discover_all_workflows_in_package("worker.workflows")
    names = [workflows.get_workflow_definition(c).name for c in classes]
    assert "pdf-scan-inbox-triage" in names
