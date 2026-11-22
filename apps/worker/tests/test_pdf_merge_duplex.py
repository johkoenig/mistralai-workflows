"""Tests for the pdf-merge-duplex workflow: control-file logic and a full run with faked activities."""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from mistralai import workflows
from mistralai.workflows.testing import create_test_worker
from worker.workflows.pdf_merge_duplex import (
    PdfMergeDuplexInput,
    PdfMergeDuplexWorkflow,
    entry_error,
    pending_indices,
)


def test_pending_indices_skips_processed():
    pairs = [
        {"front": "a.pdf", "back": "b.pdf"},
        {"front": "c.pdf", "back": "d.pdf", "status": "done"},
        {"front": "e.pdf", "back": "f.pdf", "status": "error", "error": "x"},
    ]
    assert pending_indices(pairs) == [0]
    assert pending_indices([]) == []


def test_entry_error_validates_paths():
    assert entry_error({"front": "/x/a.pdf", "back": "/x/b.pdf"}) is None
    assert entry_error({"front": "/x/a.pdf"}) is not None
    assert entry_error({"back": "/x/b.pdf"}) is not None
    assert entry_error(["nope"]) is not None
    assert entry_error({"front": "/x/a.txt", "back": "/x/b.pdf"}) is not None
    assert entry_error({}) is not None


def _write_calls(record_path, entry):
    with open(record_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def _make_fakes(record_path, naming, folder_names, loaded):
    @workflows.activity(name="merge-load-control-file")
    async def fake_load(control_file: str, connector: str = "") -> dict[str, Any]:
        _write_calls(record_path, {"activity": "load"})
        return loaded

    @workflows.activity(name="merge-save-control-file")
    async def fake_save(
        control_file: str, data: dict[str, Any], connector: str = ""
    ) -> dict[str, Any]:
        _write_calls(record_path, {"activity": "save"})
        loaded.clear()
        loaded.update(data)
        return {}

    @workflows.activity(name="merge-list-folder-names")
    async def fake_list(folder_path: str, connector: str = "") -> list[str]:
        return list(folder_names)

    @workflows.activity(name="merge-read-pair-texts")
    async def fake_read(
        front_path: str, back_path: str, connector: str = ""
    ) -> dict[str, Any]:
        return {
            "combined_text": "Rechnung Beispielwerke 2026",
            "has_text": True,
            "pages": 2,
        }

    @workflows.activity(name="name-document")
    async def fake_name(
        text: str, model: str, naming_rules: str = ""
    ) -> dict[str, Any]:
        return naming

    @workflows.activity(name="load-naming-rules")
    async def fake_rules(folder: str, connector: str = "") -> dict[str, Any]:
        return {"rules": "", "source": None}

    @workflows.activity(name="merge-apply-pair")
    async def fake_apply(
        front_path: str,
        back_path: str,
        target_path: str,
        archive_dir: str,
        connector: str = "",
    ) -> dict[str, Any]:
        _write_calls(
            record_path,
            {"activity": "apply", "from": [front_path, back_path], "to": target_path},
        )
        return {"target": target_path, "archive_dir": archive_dir, "pages": 4}

    return [
        fake_load,
        fake_save,
        fake_list,
        fake_read,
        fake_name,
        fake_rules,
        fake_apply,
    ]


async def test_workflow_merges_pair_and_writes_status(
    temporal_env, tmp_path, monkeypatch
):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("MERGE_TEST_CALLS", record_path)
    loaded = {
        "pairs": [
            {
                "front": "SCANNER_INBOX/2026-11-28 A Vorderseite.pdf",
                "back": "SCANNER_INBOX/2026-11-28 A Rueckseite.pdf",
            }
        ]
    }
    naming = {
        "date": "2026-11-28",
        "title": "Rechnung Beispielwerke",
        "confidence": 0.9,
    }
    fakes = _make_fakes(record_path, naming, ["AGENTS.md"], loaded)
    async with create_test_worker(
        temporal_env, [PdfMergeDuplexWorkflow], activities=fakes
    ):
        raw = await temporal_env.client.execute_workflow(
            PdfMergeDuplexWorkflow.run,
            PdfMergeDuplexInput(
                control_file="SCANNER_INBOX/steuerdatei.json", apply_changes=True
            ),
            id="test-merge-apply",
            task_queue="test-task-queue",
            execution_timeout=timedelta(seconds=60),
        )
        result = raw["result"] if isinstance(raw, dict) and "result" in raw else raw

    assert result["processed"] == 1
    assert result["actions"][0]["action"] == "merged"
    assert (
        result["actions"][0]["to"]
        == "SCANNER_INBOX/2026-11-28 Rechnung Beispielwerke.pdf"
    )
    assert loaded["pairs"][0]["status"] == "done"
    assert loaded["pairs"][0]["result"]["archive"].startswith("SCANNER_INBOX/merged/")
    calls = [
        json.loads(line)
        for line in Path(record_path).read_text(encoding="utf-8").splitlines()
    ]
    assert any(c["activity"] == "apply" for c in calls)
    assert any(c["activity"] == "save" for c in calls)


async def test_workflow_skips_processed_and_marks_errors(
    temporal_env, tmp_path, monkeypatch
):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("MERGE_TEST_CALLS", record_path)
    loaded = {
        "pairs": [
            {"front": "SCANNER_INBOX/x.pdf", "back": "", "status": None},
            {
                "front": "SCANNER_INBOX/2026-11-28 A Vorderseite.pdf",
                "back": "SCANNER_INBOX/2026-11-28 A Rueckseite.pdf",
                "title": "2026-11-28 Rechnung A",
                "status": "done",
            },
            {
                "front": "SCANNER_INBOX/2026-11-29 B Vorderseite.pdf",
                "back": "SCANNER_INBOX/2026-11-29 B Rueckseite.pdf",
            },
        ]
    }
    naming = {"date": "2026-11-29", "title": "Rechnung B", "confidence": 0.9}
    fakes = _make_fakes(record_path, naming, [], loaded)
    async with create_test_worker(
        temporal_env, [PdfMergeDuplexWorkflow], activities=fakes
    ):
        raw = await temporal_env.client.execute_workflow(
            PdfMergeDuplexWorkflow.run,
            PdfMergeDuplexInput(
                control_file="SCANNER_INBOX/steuerdatei.json", apply_changes=True
            ),
            id="test-merge-mixed",
            task_queue="test-task-queue",
            execution_timeout=timedelta(seconds=60),
        )
        result = raw["result"] if isinstance(raw, dict) and "result" in raw else raw

    assert result["processed"] == 2
    by_index = {a["index"]: a for a in result["actions"]}
    assert by_index[0]["action"] == "error"
    assert loaded["pairs"][0]["status"] == "error"
    assert loaded["pairs"][1]["status"] == "done"
    assert by_index[2]["to"] == "SCANNER_INBOX/2026-11-29 Rechnung B.pdf"


async def test_workflow_dry_run_does_not_write(temporal_env, tmp_path, monkeypatch):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("MERGE_TEST_CALLS", record_path)
    loaded = {
        "pairs": [
            {
                "front": "SCANNER_INBOX/2026-11-28 A Vorderseite.pdf",
                "back": "SCANNER_INBOX/2026-11-28 A Rueckseite.pdf",
            }
        ]
    }
    naming = {"date": "2026-11-28", "title": "Rechnung A", "confidence": 0.9}
    fakes = _make_fakes(record_path, naming, [], loaded)
    async with create_test_worker(
        temporal_env, [PdfMergeDuplexWorkflow], activities=fakes
    ):
        raw = await temporal_env.client.execute_workflow(
            PdfMergeDuplexWorkflow.run,
            PdfMergeDuplexInput(
                control_file="SCANNER_INBOX/steuerdatei.json", apply_changes=False
            ),
            id="test-merge-dry",
            task_queue="test-task-queue",
            execution_timeout=timedelta(seconds=60),
        )
        result = raw["result"] if isinstance(raw, dict) and "result" in raw else raw

    assert result["applied"] is False
    assert result["actions"][0]["action"] == "planned"
    assert "status" not in loaded["pairs"][0]
    calls = [
        json.loads(line)
        for line in Path(record_path).read_text(encoding="utf-8").splitlines()
    ]
    assert not any(c["activity"] in ("apply", "save") for c in calls)


def test_workflow_is_discovered():
    classes = workflows.discover_all_workflows_in_package("worker.workflows")
    names = [workflows.get_workflow_definition(c).name for c in classes]
    assert "pdf-merge-duplex" in names
