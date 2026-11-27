"""Tests for the pdf-scanner-processor orchestrator.

Pure helpers (scan_order, merged_scan_name, detect_front_back prefilter) and
full runs with faked I/O activities. The orchestrator calls the step workflows
as child workflows BY NAME (STEPS registry -> @workflow.define name), so the
tests register fake step classes under the real step names (pdf-ocr,
pdf-alternating-merge, pdf-split) in the test worker; the child executions then
run the fakes. The fakes derive everything from their (serialized) input, since
module-level monkeypatching is not visible inside the workflow sandbox.
"""

import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from mistralai import workflows
from mistralai.workflows.testing import create_test_worker
from worker import pdf_ops
from worker.workflows import pdf_scanner_processor
from worker.workflows.pdf_scanner_processor import (
    PdfScannerProcessorInput,
    PdfScannerProcessorWorkflow,
    list_scan_files,
)
from worker.workflows.steps.pdf_alternating_merge import PdfAlternatingMergeInput
from worker.workflows.steps.pdf_ocr import PdfOcrInput
from worker.workflows.steps.pdf_split import PdfSplitInput
from worker.workflows.steps.pdf_vision import PdfVisionInput


@workflows.workflow.define(name="pdf-ocr")
class FakeOcrStep:
    @workflows.workflow.entrypoint
    async def run(self, input: PdfOcrInput) -> dict[str, Any]:
        # Returns per-file text metadata (like the real step with
        # with_pages_text=true), so the orchestrator needs no analyze download.
        # The scans come from the fake list-scan-files activity (module state is
        # not visible inside the workflow sandbox).
        listing = await list_scan_files(input.target_path, input.connector)
        actions = [
            {
                "path": s["path"],
                "pages": s["page_count"],
                "action": "skipped",
                "has_text": s["has_text"],
                "text": s["text"],
                "pages_text": [s["text"]],
                "ocr_quality": {
                    "confidence": 0.3 if s.get("low_confidence") else 0.95,
                    "low_confidence": bool(s.get("low_confidence", False)),
                },
            }
            for s in listing["scans"]
        ]
        return {
            "target": input.target_path,
            "kind": "folder",
            "applied": input.apply_changes,
            "languages": input.languages,
            "actions": actions,
        }


@workflows.workflow.define(name="pdf-alternating-merge")
class FakeMergeStep:
    @workflows.workflow.entrypoint
    async def run(self, input: PdfAlternatingMergeInput) -> dict[str, Any]:
        actions = []
        for index, pair in enumerate(input.pairs or []):
            front = str(pair.get("front", ""))
            title = str(pair.get("title") or "Merged")
            folder = front.rsplit("/", 1)[0] if "/" in front else ""
            actions.append(
                {
                    "index": index,
                    "front": front,
                    "back": pair.get("back"),
                    "action": "merged" if input.apply_changes else "planned",
                    "to": f"{folder}/{title}",
                    "naming_rules_seen": input.naming_rules,
                }
            )
        return {
            "source": "pairs",
            "applied": input.apply_changes,
            "processed": len(actions),
            "actions": actions,
        }


@workflows.workflow.define(name="pdf-split")
class FakeSplitStep:
    @workflows.workflow.entrypoint
    async def run(self, input: PdfSplitInput) -> dict[str, Any]:
        # Only called with a CONFIRMED plan (explicit split_at + titles):
        # the orchestrator plans inline via the plan-split activity first.
        split_at = input.split_at or []
        titles = input.titles or [f"Teil {i + 1}" for i in range(len(split_at) + 1)]
        folder = input.target_path.rsplit("/", 1)[0] if "/" in input.target_path else ""
        parts = [
            {"to": f"{folder}/{title}.pdf", "count": i + 1}
            for i, title in enumerate(titles)
        ]
        return {
            "target": input.target_path,
            "applied": input.apply_changes and bool(split_at),
            "action": "split" if input.apply_changes else "planned",
            "parts": parts,
            "naming_rules_seen": input.naming_rules,
        }


@workflows.workflow.define(name="pdf-vision")
class FakeVisionStep:
    @workflows.workflow.entrypoint
    async def run(self, input: PdfVisionInput) -> dict[str, Any]:
        name = input.target_path.rsplit("/", 1)[-1]
        return {
            "target": input.target_path,
            "applied": input.apply_changes,
            "actions": [
                {
                    "path": input.target_path,
                    "action": "vision_pass" if input.apply_changes else "planned",
                    "text": f"VISION TEXT fuer {name}",
                    "pages_text": {"1": f"VISION TEXT fuer {name}"},
                    "model": "mistral-small-latest",
                    "confidence": 0.9,
                    "low_confidence": False,
                }
            ],
        }


def _file(
    name: str,
    order: int,
    size: int = 1000,
    page_count: int = 1,
    text: str = "",
    has_text: bool = True,
    low_confidence: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "path": f"SCANNER_INBOX/{name}",
        "size": size,
        "etag": None,
        "order": order,
        "page_count": page_count,
        "has_text": has_text,
        "text": text,
        "low_confidence": low_confidence,
    }


class TestScanHelpers:
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
            ("ScanMerged0007.pdf", None),
            ("2026-11-09 Rechnung.pdf", None),
        ],
    )
    def test_scan_order(self, name, expected):
        assert pdf_ops.scan_order(name) == expected

    def test_merged_scan_name_uses_larger_scan_number(self):
        assert pdf_ops.merged_scan_name([-1, 7]) == "ScanMerged0007.pdf"
        assert pdf_ops.merged_scan_name([3, 4]) == "ScanMerged0004.pdf"
        # merged intermediates are not picked up as scans on later runs
        assert pdf_ops.scan_order(pdf_ops.merged_scan_name([-1, 7])) is None


class TestDetectFrontBack:
    def test_consecutive_candidates_pair_despite_different_content(self):
        # Name/content-independent: the back page of a letter can look totally
        # different (only remaining text + signature) - the LLM check decides.
        files = [
            _file(
                "Scan0000.pdf",
                0,
                size=1000,
                page_count=2,
                text="Briefkopf Datum 18.11.2026 Rechnungsnummer 4711",
            ),
            _file(
                "Scan0001.pdf",
                1,
                size=1100,
                page_count=2,
                text="mit freundlichen Gruessen Unterschrift Praxis",
            ),
        ]
        pairs = pdf_ops.detect_front_back(files)
        assert len(pairs) == 1
        assert pairs[0][0]["name"] == "Scan0000.pdf"
        assert pairs[0][1]["name"] == "Scan0001.pdf"

    def test_different_page_count_not_paired(self):
        files = [
            _file("Scan0000.pdf", 0, size=1000, page_count=1, text="aaa bbb ccc"),
            _file("Scan0001.pdf", 1, size=1000, page_count=2, text="aaa bbb ccc"),
        ]
        assert pdf_ops.detect_front_back(files) == []

    def test_size_out_of_range_not_paired(self):
        files = [
            _file("Scan0000.pdf", 0, size=100, page_count=2, text="gleicher Inhalt"),
            _file("Scan0001.pdf", 1, size=10000, page_count=2, text="gleicher Inhalt"),
        ]
        assert pdf_ops.detect_front_back(files) == []

    def test_missing_text_layer_not_paired(self):
        files = [
            _file(
                "Scan0000.pdf", 0, size=1000, page_count=2, text="aaa", has_text=False
            ),
            _file("Scan0001.pdf", 1, size=1000, page_count=2, text="bbb"),
        ]
        assert pdf_ops.detect_front_back(files) == []

    def test_third_file_not_consumed_by_earlier_pair(self):
        files = [
            _file(
                "Scan0000.pdf", 0, size=1000, page_count=1, text="gemeinsamer Briefkopf"
            ),
            _file(
                "Scan0001.pdf", 1, size=1000, page_count=1, text="gemeinsamer Briefkopf"
            ),
            _file("Scan0002.pdf", 2, size=5000, page_count=3, text="anderer Inhalt"),
        ]
        pairs = pdf_ops.detect_front_back(files)
        assert len(pairs) == 1
        assert pairs[0][1]["name"] == "Scan0001.pdf"


def _write_calls(record_path: str, entry: dict) -> None:
    with open(record_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def _make_fakes(
    record_path: str,
    scans: list[dict[str, Any]],
    naming_by_marker: dict[str, dict[str, Any]],
    front_back: dict[str, Any],
) -> list:
    @workflows.activity(name="list-scan-files")
    async def fake_list(folder_path: str, connector: str = "") -> dict[str, Any]:
        return {
            "folder": folder_path,
            "scans": scans,
            "existing_names": ["AGENTS.md"] + [s["name"] for s in scans],
        }

    @workflows.activity(name="analyze-scan-file")
    async def fake_analyze(
        entry: dict[str, Any], connector: str = ""
    ) -> dict[str, Any]:
        _write_calls(record_path, {"activity": "analyze", "path": entry["path"]})
        return entry

    @workflows.activity(name="name-document")
    async def fake_name(
        text: str, model: str, naming_rules: str = ""
    ) -> dict[str, Any]:
        for marker, naming in naming_by_marker.items():
            if marker in text:
                return naming
        return {"date": None, "title": "", "confidence": 0.0}

    @workflows.activity(name="plan-split")
    async def fake_plan_split(
        page_texts: list[str],
        page_count: int,
        fixed_split_at: list[int] | None,
        model: str,
        naming_rules: str = "",
    ) -> dict[str, Any]:
        joined = "\n".join(page_texts or [])
        if fixed_split_at is not None:
            return {
                "split_at": fixed_split_at,
                "titles": [f"Titel {i + 1}" for i in range(len(fixed_split_at) + 1)],
                "confidence": 0.9,
                "needs_input": False,
                "question": "",
            }
        if "SPLITME" in joined:
            return {
                "split_at": [1],
                "titles": ["Teil A", "Teil B"],
                "confidence": 0.9,
                "needs_input": False,
                "question": "",
            }
        return {
            "split_at": [],
            "titles": ["Ganzes Dokument"],
            "confidence": 0.9,
            "needs_input": False,
            "question": "",
        }

    @workflows.activity(name="front-back-check")
    async def fake_front_back(
        front_text: str, back_text: str, model: str
    ) -> dict[str, Any]:
        _write_calls(record_path, {"activity": "front-back-check"})
        return front_back

    @workflows.activity(name="load-naming-rules")
    async def fake_rules(folder: str, connector: str = "") -> dict[str, Any]:
        _write_calls(record_path, {"activity": "rules", "folder": folder})
        return {"rules": "FAKE_RULES", "source": "AGENTS.md"}

    @workflows.activity(name="backup-scan-file")
    async def fake_backup(from_path: str, connector: str = "") -> dict[str, Any]:
        backup = f"SCANNER_INBOX/.originals/pdf-scanner-processor/{from_path.rsplit('/', 1)[-1]}"
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

    return [
        fake_list,
        fake_analyze,
        fake_name,
        fake_plan_split,
        fake_front_back,
        fake_rules,
        fake_backup,
        fake_rename,
    ]


async def _run_processor(
    temporal_env,
    tmp_path,
    scans,
    naming_by_marker,
    front_back,
    apply_changes=True,
    **input_kwargs,
):
    record_path = str(tmp_path / "calls.jsonl")
    fakes = _make_fakes(record_path, scans, naming_by_marker, front_back)
    input_kwargs.setdefault("folder_path", "SCANNER_INBOX")
    input_kwargs.setdefault("apply_changes", apply_changes)
    async with create_test_worker(
        temporal_env,
        [
            PdfScannerProcessorWorkflow,
            FakeOcrStep,
            FakeMergeStep,
            FakeSplitStep,
            FakeVisionStep,
        ],
        activities=fakes,
    ):
        raw = await temporal_env.client.execute_workflow(
            PdfScannerProcessorWorkflow.run,
            PdfScannerProcessorInput(**input_kwargs),
            id=f"test-processor-{uuid.uuid4().hex[:8]}",
            task_queue="test-task-queue",
            execution_timeout=timedelta(seconds=120),
        )
        result = raw["result"] if isinstance(raw, dict) and "result" in raw else raw
    record = Path(record_path)
    calls = (
        [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
        if record.exists()
        else []
    )
    return result, calls


def _by_step(result: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for action in result["actions"]:
        grouped.setdefault(action["step"], []).append(action)
    return grouped


PAIR_FRONT = "PAIR Briefkopf Beispielpraxis Datum 18.11.2026 Rechnungsnummer 4711"
PAIR_BACK = "PAIR Rueckseite mit freundlichen Gruessen und Unterschrift"
SINGLE = "SINGLE ganz anderes Dokument ohne Datum"
SPLITTABLE = "SPLITTABLE Dokument mit SPLITME Marker fuer zwei Teile"

NAMING = {
    "PAIR": {
        "date": "2026-11-18",
        "title": "Rechnung Beispielpraxis",
        "confidence": 0.9,
    },
    "SINGLE": {"date": "2026-11-19", "title": "Brief Praxis", "confidence": 0.9},
}
FRONT_BACK_YES = {
    "is_front_back": True,
    "confidence": 0.9,
    "reason": "gleiche Rechnungsnummer",
}
FRONT_BACK_NO = {
    "is_front_back": False,
    "confidence": 0.95,
    "reason": "unterschiedliche Dokumente",
}


async def test_pair_merged_then_split_before_naming(temporal_env, tmp_path):
    scans = [
        _file("Scan.pdf", -1, size=1000, page_count=2, text=PAIR_FRONT),
        _file("Scan0007.pdf", 7, size=1100, page_count=2, text=PAIR_BACK),
        _file("Scan0009.pdf", 9, size=900, page_count=2, text=SPLITTABLE),
    ]
    result, calls = await _run_processor(
        temporal_env, tmp_path, scans, NAMING, FRONT_BACK_YES
    )
    assert result["applied"] is True
    by_step = _by_step(result)

    # raw scans parked under the orchestrator's own .originals subfolder
    backups = [c for c in calls if c["activity"] == "backup"]
    assert {c["from"] for c in backups} == {
        "SCANNER_INBOX/Scan.pdf",
        "SCANNER_INBOX/Scan0007.pdf",
        "SCANNER_INBOX/Scan0009.pdf",
    }
    assert all(
        c["backup"].startswith("SCANNER_INBOX/.originals/pdf-scanner-processor/")
        for c in backups
    )

    # OCR step ran on the whole folder
    assert by_step["ocr"][0]["result"]["target"] == "SCANNER_INBOX"

    # front/back accepted for the pair (N = larger scan number = 7)
    pair_actions = [a for a in by_step["front-back"] if a["action"] == "pair"]
    assert len(pair_actions) == 1
    assert pair_actions[0]["files"] == ["Scan.pdf", "Scan0007.pdf"]
    merge = by_step["merge"][0]
    assert merge["files"] == ["Scan.pdf", "Scan0007.pdf"]
    assert merge["action"] == "merged"
    assert merge["to"] == "SCANNER_INBOX/ScanMerged0007.pdf"

    # no second download: text metadata comes from the pdf-ocr result
    assert not [c for c in calls if c["activity"] == "analyze"]

    # split planned inline per unit BEFORE naming, on the original paths; the
    # pdf-split child runs only for the confirmed split (Scan0009, SPLITME)
    splits = {s["target"]: s["action"] for s in by_step["split"]}
    assert splits == {
        "SCANNER_INBOX/ScanMerged0007.pdf": "no_split",
        "SCANNER_INBOX/Scan0009.pdf": "split",
    }
    split_plans = {s["target"]: s["plan"]["split_at"] for s in by_step["split"]}
    assert split_plans["SCANNER_INBOX/Scan0009.pdf"] == [1]
    steps_in_order = [a["step"] for a in result["actions"]]
    assert steps_in_order.index("split") < steps_in_order.index("name")

    # naming: only the unit that was NOT split is renamed; the split unit's
    # parts are named from the split plan (named_by_split)
    renames = {c["from"]: c["to"] for c in calls if c["activity"] == "rename"}
    assert renames == {
        "SCANNER_INBOX/ScanMerged0007.pdf": (
            "SCANNER_INBOX/2026-11-18 Rechnung Beispielpraxis.pdf"
        )
    }
    name_actions = {
        a.get("path") or a.get("from"): a["action"] for a in by_step["name"]
    }
    assert name_actions == {
        "SCANNER_INBOX/ScanMerged0007.pdf": "renamed",
        "SCANNER_INBOX/Scan0009.pdf": "named_by_split",
    }
    named_by_split = next(a for a in by_step["name"] if a["action"] == "named_by_split")
    assert named_by_split["parts"] == ["Teil A.pdf", "Teil B.pdf"]


async def test_dry_run_writes_nothing(temporal_env, tmp_path):
    scans = [
        _file("Scan0001.pdf", 1, size=1000, page_count=2, text=PAIR_FRONT),
        _file("Scan0002.pdf", 2, size=1100, page_count=2, text=PAIR_BACK),
    ]
    result, calls = await _run_processor(
        temporal_env, tmp_path, scans, NAMING, FRONT_BACK_YES, apply_changes=False
    )
    assert result["applied"] is False
    assert not [c for c in calls if c["activity"] in ("backup", "rename")]
    by_step = _by_step(result)
    assert by_step["ocr"][0]["result"]["applied"] is False
    assert by_step["merge"][0]["action"] == "planned"
    assert by_step["merge"][0]["to"] == "SCANNER_INBOX/ScanMerged0002.pdf"
    name_actions = by_step["name"]
    assert len(name_actions) == 1
    assert name_actions[0]["action"] == "planned"
    assert (
        name_actions[0]["to"] == "SCANNER_INBOX/2026-11-18 Rechnung Beispielpraxis.pdf"
    )
    # split planned inline from the pair's texts (no SPLITME -> no_split);
    # in a dry run no split child is spawned at all
    assert {s["action"] for s in by_step["split"]} == {"no_split"}
    assert all("result" not in s for s in by_step["split"])


async def test_llm_rejects_pair_then_both_named_as_singles(temporal_env, tmp_path):
    scans = [
        _file("Scan0001.pdf", 1, size=1000, page_count=2, text=PAIR_FRONT),
        _file("Scan0002.pdf", 2, size=1100, page_count=2, text=PAIR_BACK),
    ]
    result, calls = await _run_processor(
        temporal_env, tmp_path, scans, NAMING, FRONT_BACK_NO
    )
    by_step = _by_step(result)
    assert "merge" not in by_step
    assert by_step["front-back"][0]["action"] == "no_pair"
    renames = {c["from"]: c["to"] for c in calls if c["activity"] == "rename"}
    assert set(renames) == {"SCANNER_INBOX/Scan0001.pdf", "SCANNER_INBOX/Scan0002.pdf"}
    # same title for both -> counter suffix on the second
    assert sorted(renames.values()) == [
        "SCANNER_INBOX/2026-11-18 Rechnung Beispielpraxis.pdf",
        "SCANNER_INBOX/2026-11-18 Rechnung Beispielpraxis_2.pdf",
    ]


async def test_low_naming_confidence_left_unchanged(temporal_env, tmp_path):
    scans = [_file("Scan0005.pdf", 5, size=900, page_count=1, text=SINGLE)]
    naming = {"SINGLE": {"date": None, "title": "", "confidence": 0.4}}
    result, calls = await _run_processor(
        temporal_env, tmp_path, scans, naming, FRONT_BACK_NO
    )
    by_step = _by_step(result)
    name_action = by_step["name"][0]
    assert name_action["action"] == "left_unchanged"
    assert name_action["path"] == "SCANNER_INBOX/Scan0005.pdf"
    assert not [c for c in calls if c["activity"] == "rename"]
    # split still ran on the unchanged file
    assert by_step["split"][0]["target"] == "SCANNER_INBOX/Scan0005.pdf"


async def test_merge_front_back_disabled_skips_detection(temporal_env, tmp_path):
    scans = [
        _file("Scan0001.pdf", 1, size=1000, page_count=2, text=PAIR_FRONT),
        _file("Scan0002.pdf", 2, size=1100, page_count=2, text=PAIR_BACK),
    ]
    result, calls = await _run_processor(
        temporal_env,
        tmp_path,
        scans,
        NAMING,
        FRONT_BACK_YES,
        merge_front_back=False,
    )
    by_step = _by_step(result)
    assert "front-back" not in by_step
    assert "merge" not in by_step
    assert not [c for c in calls if c["activity"] == "front-back-check"]
    assert len(by_step["name"]) == 2


async def test_missing_folder_returns_error(temporal_env, tmp_path):
    result, _ = await _run_processor(
        temporal_env, tmp_path, [], {}, FRONT_BACK_NO, folder_path=""
    )
    assert "folder_path missing" in result["error"]
    assert result["actions"] == []


async def test_unknown_connector_returns_error(temporal_env, tmp_path):
    result, _ = await _run_processor(
        temporal_env, tmp_path, [], {}, FRONT_BACK_NO, connector="bogus"
    )
    assert "unknown connector" in result["error"]


async def test_no_scans_reports_note(temporal_env, tmp_path):
    result, _ = await _run_processor(temporal_env, tmp_path, [], {}, FRONT_BACK_NO)
    assert result["note"] == "no scan files found"
    assert result["actions"] == []


def test_all_five_workflows_are_discovered():
    classes = workflows.discover_all_workflows_in_package("worker.workflows")
    names = {workflows.get_workflow_definition(c).name for c in classes}
    assert names == {
        "pdf-ocr",
        "pdf-split",
        "pdf-alternating-merge",
        "pdf-vision",
        "pdf-scanner-processor",
    }


def test_step_registry_points_at_the_step_workflows():
    assert pdf_scanner_processor.STEPS["ocr"].__name__ == "PdfOcrWorkflow"
    assert (
        pdf_scanner_processor.STEPS["merge"].__name__ == "PdfAlternatingMergeWorkflow"
    )
    assert pdf_scanner_processor.STEPS["split"].__name__ == "PdfSplitWorkflow"
    assert pdf_scanner_processor.STEPS["vision"].__name__ == "PdfVisionWorkflow"
    names = {
        workflows.get_workflow_definition(cls).name
        for cls in pdf_scanner_processor.STEPS.values()
    }
    assert names == {"pdf-ocr", "pdf-alternating-merge", "pdf-split", "pdf-vision"}


async def test_orchestrator_passes_ocr_languages_and_naming_rules_to_children(
    temporal_env, tmp_path
):
    # D: ocr_languages reaches the pdf-ocr child; C: the loaded naming rules are
    # passed to the pdf-split child instead of re-loading the AGENTS.md there.
    scans = [_file("Scan0001.pdf", 1, size=1000, page_count=2, text=SPLITTABLE)]
    result, calls = await _run_processor(
        temporal_env, tmp_path, scans, NAMING, FRONT_BACK_NO, ocr_languages="deu"
    )
    by_step = _by_step(result)
    assert by_step["ocr"][0]["result"]["languages"] == "deu"
    split = by_step["split"][0]
    assert split["action"] == "split"  # SPLITTABLE -> confirmed, child spawned
    assert split["result"]["naming_rules_seen"] == "FAKE_RULES"
    # rules were loaded exactly once (by the orchestrator), not per child
    assert len([c for c in calls if c["activity"] == "rules"]) == 1


async def test_low_confidence_scan_is_routed_through_vision(temporal_env, tmp_path):
    # G: a scan flagged low_confidence by the OCR quality heuristic goes through
    # the pdf-vision child (step 2.5), and the vision text feeds naming: the
    # SINGLE marker is gone, so the namer is not confident -> left_unchanged.
    scans = [
        _file(
            "Scan0001.pdf", 1, size=1000, page_count=2, text=SINGLE, low_confidence=True
        )
    ]
    result, calls = await _run_processor(
        temporal_env, tmp_path, scans, NAMING, FRONT_BACK_NO
    )
    by_step = _by_step(result)
    assert "vision" in by_step
    vision = by_step["vision"][0]
    assert vision["target"] == "SCANNER_INBOX/Scan0001.pdf"
    assert vision["action"] == "vision_pass"
    steps_in_order = [a["step"] for a in result["actions"]]
    assert steps_in_order.index("vision") < steps_in_order.index("name")
    # vision text replaced the OCR text for naming (no SINGLE marker anymore)
    assert by_step["name"][0]["action"] == "left_unchanged"
    assert not [c for c in calls if c["activity"] == "rename"]


async def test_vision_fallback_disabled_skips_step(temporal_env, tmp_path):
    scans = [
        _file(
            "Scan0001.pdf", 1, size=1000, page_count=1, text=SINGLE, low_confidence=True
        )
    ]
    result, _ = await _run_processor(
        temporal_env, tmp_path, scans, NAMING, FRONT_BACK_NO, vision_fallback=False
    )
    by_step = _by_step(result)
    assert "vision" not in by_step
    # without vision override the SINGLE marker names the file as usual
    assert by_step["name"][0]["action"] == "renamed"
