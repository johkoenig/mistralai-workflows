"""Tests for the generic pdf-ocr workflow: target selection, backup handling, OCR command, and a full fake run."""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from mistralai import workflows
from mistralai.workflows.testing import create_test_worker
from worker import ocr, pdf_ops
from worker.workflows.steps import pdf_ocr
from worker.workflows.steps.pdf_ocr import PdfOcrInput, PdfOcrWorkflow


def _entry(name: str, size: int = 1000, is_dir: bool = False) -> dict:
    return {"name": name, "size": size, "isDir": is_dir}


class TestPureHelpers:
    def test_is_pdf_name_case_insensitive(self):
        assert pdf_ocr.is_pdf_name("Rechnung.pdf")
        assert pdf_ocr.is_pdf_name("SCAN.PDF")
        assert not pdf_ocr.is_pdf_name("Brief.jpg")
        assert not pdf_ocr.is_pdf_name("Ordner")

    def test_keep_original_defaults_to_true(self):
        assert PdfOcrInput(target_path="/D/x.pdf").keep_original is True

    def test_split_entries_filters_and_builds_paths(self):
        entries = [
            _entry("a.pdf"),
            _entry("b.PDF"),
            _entry("bild.jpg"),
            _entry("Unterordner", is_dir=True),
            _entry(".originals", is_dir=True),
            {"not": "a dict-like entry"},
        ]
        files, subfolders = pdf_ocr.split_entries("/X", entries)
        assert [f["name"] for f in files] == ["a.pdf", "b.PDF"]
        assert [f["path"] for f in files] == ["/X/a.pdf", "/X/b.PDF"]
        # the .originals backup folder is never descended into
        assert subfolders == ["/X/Unterordner"]


class _FakeNC:
    """Minimal ToolCallClient stand-in for the pdf_ops backup helpers."""

    def __init__(self, listings: dict[str, list] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.listings = listings or {}

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        self.calls.append((tool_name, arguments))
        if tool_name == "files_list":
            if arguments["path"] in self.listings:
                return self.listings[arguments["path"]]
            raise RuntimeError("folder not found")
        return {}


class TestBackupHelpers:
    async def test_park_original_copies_with_unique_name(self):
        root = "/D/.originals/pdf-scan-inbox-triage"
        nc = _FakeNC({root: [{"name": "Scan0000.pdf"}]})
        dest = await pdf_ops.park_original(nc, "/D/Scan0000.pdf", root)
        assert dest == f"{root}/Scan0000_2.pdf"
        assert (
            "files_copy",
            {
                "from": "/D/Scan0000.pdf",
                "to": dest,
                "confirm": True,
                "confirm_shared": True,
            },
        ) in nc.calls
        assert (
            "files_mkdir",
            {"path": root, "confirm": True, "confirm_shared": True},
        ) in nc.calls

    async def test_park_original_first_backup_keeps_name(self):
        root = "/D/.originals/pdf-ocr"
        nc = _FakeNC()
        dest = await pdf_ops.park_original(nc, "/D/Rechnung.pdf", root)
        assert dest == f"{root}/Rechnung.pdf"

    async def test_list_names_empty_for_missing_folder(self):
        nc = _FakeNC()
        assert await pdf_ops.list_names(nc, "/D/.originals/pdf-ocr") == set()

    def test_originals_dir_per_workflow(self):
        assert pdf_ops.originals_dir("/D", "pdf-ocr") == "/D/.originals/pdf-ocr"
        assert pdf_ops.originals_dir("/D/", "pdf-split") == "/D/.originals/pdf-split"

    def test_build_ocr_command_flags(self):
        cmd = ocr.build_ocr_command(
            "in.pdf", "out.pdf", languages="deu+eng", rotate=True, deskew=True, jobs="4"
        )
        assert cmd[0] == "ocrmypdf"
        assert "deu+eng" in cmd and "-l" in cmd
        assert "--deskew" in cmd and "--rotate-pages" in cmd
        assert cmd[-2:] == ["in.pdf", "out.pdf"]

    def test_build_ocr_command_without_preprocessing(self):
        cmd = ocr.build_ocr_command(
            "in.pdf", "out.pdf", languages="de", rotate=False, deskew=False, jobs="2"
        )
        assert "--deskew" not in cmd
        assert "--rotate-pages" not in cmd
        assert "--rotate-pages-threshold" not in cmd


def _write_calls(record_path: str, entry: dict) -> None:
    with open(record_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def _make_fakes(record_path: str, listing: dict, processed: dict[str, dict]) -> list:
    @workflows.activity(name="ocr-list-targets")
    async def fake_list(
        target_path: str, recursive: bool, connector: str = ""
    ) -> dict[str, Any]:
        _write_calls(
            record_path,
            {"activity": "list", "target": target_path, "recursive": recursive},
        )
        return listing

    @workflows.activity(name="ocr-process-file")
    async def fake_process(
        entry: dict[str, Any],
        force: bool,
        keep_original: bool,
        apply_changes: bool,
        languages: str,
        connector: str = "",
    ) -> dict[str, Any]:
        _write_calls(
            record_path,
            {
                "activity": "process",
                "path": entry["path"],
                "force": force,
                "keep_original": keep_original,
                "apply": apply_changes,
            },
        )
        return processed.get(
            entry["path"], {"path": entry["path"], "action": "replaced", "pages": 2}
        )

    return [fake_list, fake_process]


async def test_workflow_processes_folder_and_reports_originals_dir(
    temporal_env, tmp_path, monkeypatch
):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
    listing = {
        "kind": "folder",
        "files": [
            {"name": "OhneText.pdf", "path": "/D/OhneText.pdf", "size": 10},
            {"name": "Fehler.pdf", "path": "/D/Fehler.pdf", "size": 10},
        ],
        "total": 2,
        "truncated": False,
    }
    processed = {
        "/D/OhneText.pdf": {
            "path": "/D/OhneText.pdf",
            "action": "replaced",
            "pages": 3,
            "original": "/D/.originals/pdf-ocr/OhneText.pdf",
        },
        "/D/Fehler.pdf": {"path": "/D/Fehler.pdf", "action": "error", "error": "boom"},
    }
    fakes = _make_fakes(record_path, listing, processed)
    async with create_test_worker(temporal_env, [PdfOcrWorkflow], activities=fakes):
        raw = await temporal_env.client.execute_workflow(
            PdfOcrWorkflow.run,
            PdfOcrInput(target_path="/D", apply_changes=True),
            id="test-pdf-ocr-folder",
            task_queue="test-task-queue",
            execution_timeout=timedelta(seconds=60),
        )
        result = raw["result"] if isinstance(raw, dict) and "result" in raw else raw

    assert result["kind"] == "folder"
    assert result["applied"] is True
    actions = {a["path"]: a for a in result["actions"]}
    assert actions["/D/OhneText.pdf"]["action"] == "replaced"
    assert actions["/D/Fehler.pdf"]["action"] == "error"

    calls = [
        json.loads(line)
        for line in Path(record_path).read_text(encoding="utf-8").splitlines()
    ]
    processed_paths = [c["path"] for c in calls if c["activity"] == "process"]
    assert processed_paths == ["/D/OhneText.pdf", "/D/Fehler.pdf"]
    ohnetext = next(
        c
        for c in calls
        if c["activity"] == "process" and c["path"] == "/D/OhneText.pdf"
    )
    # backups are on by default
    assert ohnetext["keep_original"] is True


async def test_workflow_single_file_mode(temporal_env, tmp_path, monkeypatch):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
    listing = {
        "kind": "file",
        "files": [{"name": "X.pdf", "path": "/D/X.pdf", "size": 10}],
        "total": 1,
        "truncated": False,
    }
    fakes = _make_fakes(record_path, listing, {})
    async with create_test_worker(temporal_env, [PdfOcrWorkflow], activities=fakes):
        raw = await temporal_env.client.execute_workflow(
            PdfOcrWorkflow.run,
            PdfOcrInput(target_path="/D/X.pdf", force=True, apply_changes=True),
            id="test-pdf-ocr-single",
            task_queue="test-task-queue",
            execution_timeout=timedelta(seconds=60),
        )
        result = raw["result"] if isinstance(raw, dict) and "result" in raw else raw

    assert result["kind"] == "file"
    assert result["actions"][0]["action"] == "replaced"
    calls = [
        json.loads(line)
        for line in Path(record_path).read_text(encoding="utf-8").splitlines()
    ]
    assert [c["path"] for c in calls if c["activity"] == "process"] == ["/D/X.pdf"]


async def test_workflow_reports_truncation_and_empty(
    temporal_env, tmp_path, monkeypatch
):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
    empty = {"kind": "folder", "files": [], "total": 0, "truncated": False}
    fakes = _make_fakes(record_path, empty, {})
    async with create_test_worker(temporal_env, [PdfOcrWorkflow], activities=fakes):
        raw = await temporal_env.client.execute_workflow(
            PdfOcrWorkflow.run,
            PdfOcrInput(target_path="/D", apply_changes=False),
            id="test-pdf-ocr-empty",
            task_queue="test-task-queue",
            execution_timeout=timedelta(seconds=60),
        )
        result = raw["result"] if isinstance(raw, dict) and "result" in raw else raw
    assert result["actions"] == []
    assert result["note"] == "no PDFs found"
    assert result["applied"] is False

    truncated = {
        "kind": "folder",
        "files": [{"name": "a.pdf", "path": "/D/a.pdf", "size": 1}],
        "total": pdf_ocr.MAX_FILES_PER_RUN + 5,
        "truncated": True,
    }
    fakes2 = _make_fakes(record_path, truncated, {})
    async with create_test_worker(temporal_env, [PdfOcrWorkflow], activities=fakes2):
        raw = await temporal_env.client.execute_workflow(
            PdfOcrWorkflow.run,
            PdfOcrInput(target_path="/D", apply_changes=False),
            id="test-pdf-ocr-truncated",
            task_queue="test-task-queue",
            execution_timeout=timedelta(seconds=60),
        )
        result = raw["result"] if isinstance(raw, dict) and "result" in raw else raw
    assert "limit" in result["note"]
    assert len(result["actions"]) == 1


def test_workflow_is_discovered():
    classes = workflows.discover_all_workflows_in_package("worker.workflows")
    names = [workflows.get_workflow_definition(c).name for c in classes]
    assert "pdf-ocr" in names
