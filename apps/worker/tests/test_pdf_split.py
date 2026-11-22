"""Tests for the pdf-split workflow: ranges, plan parsing/validation, AI modes, switches, and discovery."""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from mistralai import workflows
from mistralai.workflows.testing import create_test_worker
from pypdf import PdfReader, PdfWriter
from worker import pdf_ops
from worker.workflows import pdf_split
from worker.workflows.pdf_split import (
    PdfSplitInput,
    PdfSplitWorkflow,
    part_filename,
    resolve_plan,
)


class TestPartRanges:
    def test_basic_splits(self):
        assert pdf_ops.part_ranges(8, [2, 4]) == [(1, 2), (3, 4), (5, 8)]
        assert pdf_ops.part_ranges(5, [3]) == [(1, 3), (4, 5)]
        assert pdf_ops.part_ranges(3, [1, 2]) == [(1, 1), (2, 2), (3, 3)]

    def test_invalid_inputs(self):
        for bad in ([], [4, 2], [2, 2], [0], [9]):
            with pytest.raises(ValueError):
                pdf_ops.part_ranges(9, bad)

    def test_cut_after_second_to_last_page_valid(self):
        # the last allowed cut is after page page_count - 1
        assert pdf_ops.part_ranges(9, [8]) == [(1, 8), (9, 9)]


class TestOriginalsDir:
    def test_originals_dir_layout(self):
        assert pdf_ops.originals_dir("/D", "pdf-split") == "/D/.originals/pdf-split"
        assert pdf_ops.originals_dir("/D/", "pdf-split") == "/D/.originals/pdf-split"
        assert pdf_ops.ORIGINALS_DIR_NAME == ".originals"


class TestFileConnectorSelection:
    def test_default_names_and_client(self):
        assert pdf_ops.FILE_CONNECTOR_NAMES == ["nextcloud"]
        assert pdf_ops.DEFAULT_FILE_CONNECTOR == "nextcloud"
        assert pdf_ops.file_client("").connector_name == "nextcloud"
        assert pdf_ops.file_client("nextcloud").connector_name == "nextcloud"

    def test_unknown_connector_rejected(self):
        with pytest.raises(ValueError, match="bogus"):
            pdf_ops.file_client("bogus")

    async def test_run_rejects_unknown_connector(
        self, temporal_env, tmp_path, monkeypatch
    ):
        record_path = str(tmp_path / "calls.jsonl")
        monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
        raw = await _run(
            temporal_env,
            record_path,
            PdfSplitInput(target_path="/D/Scan.pdf", connector="bogus"),
            plan={},
            split_result={},
        )
        assert raw["action"] == "error"
        assert "bogus" in raw["error"]
        assert "nextcloud" in raw["error"]
        assert not Path(record_path).exists()

    async def test_connector_reaches_activities(
        self, temporal_env, tmp_path, monkeypatch
    ):
        record_path = str(tmp_path / "calls.jsonl")
        monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
        split_result = {"action": "planned", "applied": False, "parts": []}
        raw = await _run(
            temporal_env,
            record_path,
            PdfSplitInput(
                target_path="/D/Scan.pdf",
                split_at=[2],
                titles=["A", "B"],
                apply_changes=False,
                connector="nextcloud",
            ),
            plan={},
            split_result=split_result,
        )
        assert raw == split_result
        calls = _read_calls(record_path)
        assert [c["activity"] for c in calls] == ["load", "split"]
        assert all(c["connector"] == "nextcloud" for c in calls)


def _blank_pdf(path: str, pages: int) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as handle:
        writer.write(handle)


def test_split_pdf_into_ranges(tmp_path):
    source = str(tmp_path / "src.pdf")
    _blank_pdf(source, 5)
    out_paths = [str(tmp_path / n) for n in ("p1.pdf", "p2.pdf", "p3.pdf")]
    pdf_ops.split_pdf_into_ranges(source, [(1, 2), (3, 4), (5, 5)], out_paths)
    assert [len(PdfReader(p).pages) for p in out_paths] == [2, 2, 1]


class TestPartFilename:
    def test_titled_part_has_no_stem_prefix(self):
        assert part_filename("Sehr langer Originalstamm", 1, "DBV Michaela") == (
            "DBV Michaela.pdf"
        )

    def test_fallback_part_uses_stem(self):
        assert part_filename("Original", 2, None) == "Original Part-2.pdf"

    def test_empty_title_falls_back_to_teil(self):
        assert part_filename("Original", 1, "  ") == "Original Part-1.pdf"


class TestParseSplitPlan:
    def test_auto_valid(self):
        plan = pdf_ops.parse_split_plan(
            '{"split_at": [2, 4], "titles": ["A", "B", "C"], "confidence": 0.9}', 6
        )
        assert plan == {
            "split_at": [2, 4],
            "titles": ["A", "B", "C"],
            "confidence": 0.9,
            "needs_input": False,
            "question": "",
        }

    def test_single_document_ok(self):
        plan = pdf_ops.parse_split_plan(
            '{"split_at": [], "titles": ["alles"], "confidence": 0.9}', 6
        )
        assert plan["needs_input"] is False and plan["titles"] == ["alles"]

    def test_invalid_cut_becomes_question(self):
        plan = pdf_ops.parse_split_plan(
            '{"split_at": [0], "titles": ["A"], "confidence": 0.9}', 6
        )
        assert plan["needs_input"] and "invalid" in plan["question"]

    def test_title_mismatch_auto_needs_input(self):
        plan = pdf_ops.parse_split_plan(
            '{"split_at": [2], "titles": ["A"], "confidence": 0.9}', 6
        )
        assert plan["needs_input"] and plan["titles"] == []

    def test_title_mismatch_manual_falls_back(self):
        plan = pdf_ops.parse_split_plan(
            '{"split_at": [9], "titles": [], "confidence": 0.5}',
            6,
            fixed_split_at=[2],
        )
        assert not plan["needs_input"]
        assert plan["titles"] == [] and plan["split_at"] == [2]

    def test_garbage_json(self):
        plan = pdf_ops.parse_split_plan("kein json", 6)
        assert plan["needs_input"]


class TestResolvePlan:
    def test_confident_split(self):
        kind, details = resolve_plan(
            {
                "split_at": [3],
                "titles": ["A", "B"],
                "confidence": 0.9,
                "needs_input": False,
                "question": "",
            }
        )
        assert kind == "split" and details == {"split_at": [3], "titles": ["A", "B"]}

    def test_low_confidence_clarification(self):
        kind, details = resolve_plan(
            {
                "split_at": [3],
                "titles": ["A"],
                "confidence": 0.5,
                "needs_input": False,
                "question": "",
            }
        )
        assert kind == "clarification" and details["confidence"] == 0.5

    def test_needs_input_clarification(self):
        kind, details = resolve_plan(
            {
                "split_at": [3],
                "titles": ["A", "B"],
                "confidence": 0.9,
                "needs_input": True,
                "question": "Wo genau?",
            }
        )
        assert kind == "clarification" and details["question"] == "Wo genau?"

    def test_no_split(self):
        kind, details = resolve_plan(
            {
                "split_at": [],
                "titles": ["alles"],
                "confidence": 0.9,
                "needs_input": False,
                "question": "",
            }
        )
        assert kind == "no_split" and details["titles"] == ["alles"]


def _write_calls(record_path: str, entry: dict) -> None:
    with open(record_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def _read_calls(record_path: str) -> list[dict[str, Any]]:
    text = Path(record_path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def _unwrap_result(raw: Any) -> Any:
    """The test worker returns the workflow result in a result envelope."""
    return raw["result"] if isinstance(raw, dict) and "result" in raw else raw


def _fake_load(record_path: str, result: dict[str, Any]):
    @workflows.activity(name="load-split-pages")
    async def fake_load(target_path: str, connector: str = "") -> dict[str, Any]:
        _write_calls(
            record_path,
            {"activity": "load", "path": target_path, "connector": connector},
        )
        return result

    return fake_load


def _fake_rules(record_path: str, rules: str):
    @workflows.activity(name="load-naming-rules")
    async def fake_rules(folder: str, connector: str = "") -> dict[str, Any]:
        _write_calls(
            record_path,
            {
                "activity": "rules",
                "folder": folder,
                "rules": rules,
                "connector": connector,
            },
        )
        return {"rules": rules, "source": f"{folder}/AGENTS.md" if rules else None}

    return fake_rules


def _make_plan(record_path: str, result: dict[str, Any]):
    @workflows.activity(name="plan-split")
    async def fake_plan(
        page_texts: list[str],
        page_count: int,
        fixed_split_at: list[int] | None,
        model: str,
        naming_rules: str,
    ) -> dict[str, Any]:
        _write_calls(
            record_path,
            {
                "activity": "plan",
                "texts": len(page_texts),
                "pages": page_count,
                "fixed": fixed_split_at,
                "model": model,
                "rules": naming_rules,
            },
        )
        return result

    return fake_plan


def _make_split(record_path: str, result: dict[str, Any]):
    @workflows.activity(name="split-file")
    async def fake_split(
        target_path: str,
        split_at: list[int],
        titles: list[str] | None,
        apply_changes: bool,
        connector: str = "",
    ) -> dict[str, Any]:
        _write_calls(
            record_path,
            {
                "activity": "split",
                "path": target_path,
                "split_at": split_at,
                "titles": titles,
                "apply": apply_changes,
                "connector": connector,
            },
        )
        return result

    return fake_split


def _loaded(pages: list[str]) -> dict[str, Any]:
    return {
        "action": "loaded",
        "base_name": "Scan.pdf",
        "stem": "Scan",
        "parent": "/D",
        "pages": pages,
        "page_count": len(pages),
    }


def _fakes(record_path: str, *, plan, split_result, rules: str = ""):
    return [
        _fake_load(record_path, _loaded(["a", "b", "c", "d", "e", "f"])),
        _fake_rules(record_path, rules),
        _make_plan(record_path, plan),
        _make_split(record_path, split_result),
    ]


CONFIDENT_PLAN = {
    "split_at": [2, 4],
    "titles": ["Erster", "Zweiter", "Dritter"],
    "confidence": 0.95,
    "needs_input": False,
    "question": "",
}


async def _run(temporal_env, record_path, wf_input, plan, split_result, rules=""):
    async with create_test_worker(
        temporal_env,
        [PdfSplitWorkflow],
        activities=_fakes(
            record_path, plan=plan, split_result=split_result, rules=rules
        ),
    ):
        raw = await temporal_env.client.execute_workflow(
            PdfSplitWorkflow.run,
            wf_input,
            id=f"test-split-{Path(record_path).stem}",
            task_queue="test-task-queue",
            execution_timeout=timedelta(seconds=60),
        )
        return _unwrap_result(raw)


async def test_auto_mode_plans_and_splits(temporal_env, tmp_path, monkeypatch):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
    split_result = {
        "target": "/D/Scan.pdf",
        "applied": True,
        "action": "split",
        "pages": 6,
        "parts": [],
        "original": "/D/.originals/pdf-split/Scan/Scan.pdf",
    }
    raw = await _run(
        temporal_env,
        record_path,
        PdfSplitInput(target_path="/D/Scan.pdf", apply_changes=True),
        CONFIDENT_PLAN,
        split_result,
        rules="maximal 40 Zeichen",
    )
    assert raw == split_result
    calls = _read_calls(record_path)
    assert [c["activity"] for c in calls] == ["load", "rules", "plan", "split"]
    assert calls[1]["rules"] == "maximal 40 Zeichen"
    assert calls[2]["fixed"] is None
    assert calls[2]["pages"] == 6
    assert calls[2]["rules"] == "maximal 40 Zeichen"
    assert calls[3]["split_at"] == [2, 4]
    assert calls[3]["titles"] == ["Erster", "Zweiter", "Dritter"]


async def test_auto_mode_clarification_blocks(temporal_env, tmp_path, monkeypatch):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
    plan = {
        "split_at": [3],
        "titles": [],
        "confidence": 0.4,
        "needs_input": True,
        "question": "Wo genau?",
    }
    raw = await _run(
        temporal_env,
        record_path,
        PdfSplitInput(target_path="/D/Scan.pdf"),
        plan,
        {},
    )
    assert raw["action"] == "clarification"
    assert raw["question"] == "Wo genau?"
    assert raw["applied"] is False
    assert raw["mode"] == "auto"
    assert [c["activity"] for c in _read_calls(record_path)] == [
        "load",
        "rules",
        "plan",
    ]


async def test_auto_mode_no_split(temporal_env, tmp_path, monkeypatch):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
    plan = {
        "split_at": [],
        "titles": ["alles"],
        "confidence": 0.9,
        "needs_input": False,
        "question": "",
    }
    raw = await _run(
        temporal_env,
        record_path,
        PdfSplitInput(target_path="/D/Scan.pdf"),
        plan,
        {},
    )
    assert raw["action"] == "no_split"
    assert raw["titles"] == ["alles"]
    assert raw["applied"] is False
    assert [c["activity"] for c in _read_calls(record_path)] == [
        "load",
        "rules",
        "plan",
    ]


async def test_auto_mode_name_parts_false_discards_titles(
    temporal_env, tmp_path, monkeypatch
):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
    raw = await _run(
        temporal_env,
        record_path,
        PdfSplitInput(target_path="/D/Scan.pdf", name_parts=False),
        CONFIDENT_PLAN,
        {"action": "split", "applied": True, "parts": []},
    )
    assert raw["action"] == "split"
    assert _read_calls(record_path)[-1]["titles"] is None


async def test_plan_splits_false_without_split_at_errors(
    temporal_env, tmp_path, monkeypatch
):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
    async with create_test_worker(
        temporal_env,
        [PdfSplitWorkflow],
        activities=_fakes(record_path, plan={}, split_result={}),
    ):
        raw = await temporal_env.client.execute_workflow(
            PdfSplitWorkflow.run,
            PdfSplitInput(target_path="/D/Scan.pdf", plan_splits=False),
            id="test-split-noplanning",
            task_queue="test-task-queue",
            execution_timeout=timedelta(seconds=60),
        )
    raw = _unwrap_result(raw)
    assert raw["action"] == "error"
    assert "split_at" in raw["error"]
    assert raw["applied"] is False
    assert [c["activity"] for c in _read_calls(record_path)] == ["load"]


async def test_manual_mode_uses_ai_titles(temporal_env, tmp_path, monkeypatch):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
    plan = {
        "split_at": [9],
        "titles": ["A", "B"],
        "confidence": 0.9,
        "needs_input": False,
        "question": "",
    }
    raw = await _run(
        temporal_env,
        record_path,
        PdfSplitInput(target_path="/D/Scan.pdf", split_at=[2], apply_changes=True),
        plan,
        {"action": "split", "applied": True, "parts": []},
    )
    assert raw["action"] == "split"
    calls = _read_calls(record_path)
    assert [c["activity"] for c in calls] == ["load", "rules", "plan", "split"]
    assert calls[1]["folder"] == "/D"
    assert calls[2]["fixed"] == [2]
    assert calls[3]["split_at"] == [2]
    assert calls[3]["titles"] == ["A", "B"]


async def test_manual_mode_name_parts_false_skips_plan(
    temporal_env, tmp_path, monkeypatch
):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
    raw = await _run(
        temporal_env,
        record_path,
        PdfSplitInput(target_path="/D/Scan.pdf", split_at=[2], name_parts=False),
        {"needs_input": True},
        {"action": "split", "applied": True, "parts": []},
    )
    assert raw["action"] == "split"
    calls = _read_calls(record_path)
    assert [c["activity"] for c in calls] == ["load", "split"]
    assert calls[1]["titles"] is None


async def test_manual_mode_explicit_titles_skip_rules_and_plan(
    temporal_env, tmp_path, monkeypatch
):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
    split_result = {
        "target": "/D/Scan.pdf",
        "applied": True,
        "action": "split",
        "pages": 6,
        "parts": [],
        "original": "/D/.originals/pdf-split/Scan/Scan.pdf",
    }
    async with create_test_worker(
        temporal_env,
        [PdfSplitWorkflow],
        activities=[
            _fake_load(record_path, _loaded(["a"] * 6)),
            _fake_rules(record_path, "should not be loaded"),
            _make_plan(record_path, {"needs_input": True}),
            _make_split(record_path, split_result),
        ],
    ):
        raw = await temporal_env.client.execute_workflow(
            PdfSplitWorkflow.run,
            PdfSplitInput(
                target_path="/D/Scan.pdf",
                split_at=[3],
                titles=["A", "B"],
                apply_changes=True,
            ),
            id="test-split-manual-explicit",
            task_queue="test-task-queue",
            execution_timeout=timedelta(seconds=60),
        )
    raw = _unwrap_result(raw)
    assert raw == split_result
    calls = _read_calls(record_path)
    assert [c["activity"] for c in calls] == ["load", "split"]
    assert calls[1]["titles"] == ["A", "B"]


async def test_manual_mode_unsure_naming_falls_back(
    temporal_env, tmp_path, monkeypatch
):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
    plan = {
        "split_at": [9],
        "titles": [],
        "confidence": 0.5,
        "needs_input": True,
        "question": "?",
    }
    raw = await _run(
        temporal_env,
        record_path,
        PdfSplitInput(target_path="/D/Scan.pdf", split_at=[2]),
        plan,
        {"action": "split", "applied": True, "parts": []},
    )
    assert raw["action"] == "split"
    assert _read_calls(record_path)[-1]["titles"] is None


async def test_rejects_non_pdf_target(temporal_env, tmp_path, monkeypatch):
    record_path = str(tmp_path / "calls.jsonl")
    monkeypatch.setenv("SCANNER_TEST_CALLS", record_path)
    async with create_test_worker(
        temporal_env,
        [PdfSplitWorkflow],
        activities=_fakes(record_path, plan={}, split_result={}),
    ):
        raw = await temporal_env.client.execute_workflow(
            PdfSplitWorkflow.run,
            PdfSplitInput(target_path="X_FOLDER/bild.jpg", split_at=[2]),
            id="test-split-nonpdf",
            task_queue="test-task-queue",
            execution_timeout=timedelta(seconds=60),
        )
    raw = _unwrap_result(raw)
    assert raw["action"] == "error"
    assert "PDF" in raw["error"]
    assert not Path(record_path).exists()


def test_workflow_is_discovered():
    classes = workflows.discover_all_workflows_in_package("worker.workflows")
    names = [workflows.get_workflow_definition(c).name for c in classes]
    assert "pdf-split" in names


def test_all_activities_present():
    assert pdf_split.HEARTBEAT_TIMEOUT is not None
    assert callable(pdf_split.load_pages)
    assert callable(pdf_split.split_file)
    assert callable(pdf_split.load_naming_rules)
    assert callable(pdf_split.resolve_plan)
    assert callable(pdf_split.part_filename)
