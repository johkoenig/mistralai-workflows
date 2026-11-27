"""Tests for the pdf-vision step workflow (roadmap G).

The per-file I/O activities (vision-load-file, vision-ocr-pass,
vision-store-result) and the target listing are faked by activity name; the
workflow itself runs in the test worker.
"""

import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from mistralai import workflows
from mistralai.workflows.testing import create_test_worker
from worker.workflows.steps.pdf_vision import PdfVisionInput, PdfVisionWorkflow


def _write_calls(record_path: str, entry: dict) -> None:
    with open(record_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def _read_calls(record_path: str) -> list[dict]:
    path = Path(record_path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _make_fakes(record_path: str, load_action: str = "vision_pass") -> list:
    @workflows.activity(name="ocr-list-targets")
    async def fake_list(
        target_path: str, recursive: bool, connector: str = ""
    ) -> dict[str, Any]:
        _write_calls(record_path, {"activity": "list", "target": target_path})
        return {
            "kind": "folder",
            "files": [
                {"name": "Scan0001.pdf", "path": "/D/Scan0001.pdf", "size": 1000}
            ],
            "total": 1,
            "truncated": False,
        }

    @workflows.activity(name="vision-load-file")
    async def fake_load(
        entry: dict[str, Any],
        pages: list[int] | None,
        force: bool,
        connector: str = "",
    ) -> dict[str, Any]:
        _write_calls(
            record_path, {"activity": "load", "path": entry["path"], "pages": pages}
        )
        if load_action == "skipped":
            return {
                "path": entry["path"],
                "action": "skipped",
                "reason": "OCR quality ok",
                "ocr_quality": {"confidence": 0.95, "low_confidence": False},
            }
        return {
            "path": entry["path"],
            "name": entry["name"],
            "action": "vision_pass",
            "local_path": "/tmp/fake-scan.pdf",
            "workdir": "/tmp/fake-workdir",
            "page_count": 2,
            "has_text": False,
            "render_pages": [1, 2],
            "ocr_quality": {"confidence": 0.3, "low_confidence": True},
        }

    @workflows.activity(name="vision-ocr-pass")
    async def fake_vision(
        pdf_path: str, pages: list[int], model: str = ""
    ) -> dict[str, Any]:
        _write_calls(
            record_path,
            {
                "activity": "vision",
                "pdf_path": pdf_path,
                "pages": pages,
                "model": model,
            },
        )
        return {
            "pages_text": {
                "1": "Vision Text Seite Eins",
                "2": "Vision Text Seite Zwei",
            },
            "text": "Vision Text Seite Eins\n\nVision Text Seite Zwei",
            "model": model or "mistral-small-latest",
            "confidence": 0.9,
            "low_confidence": False,
            "pages": pages,
        }

    @workflows.activity(name="vision-store-result")
    async def fake_store(
        entry: dict[str, Any],
        workdir: str,
        local_path: str,
        vision: dict[str, Any],
        apply_changes: bool,
        connector: str = "",
    ) -> dict[str, Any]:
        _write_calls(
            record_path,
            {
                "activity": "store",
                "path": entry["path"],
                "apply_changes": apply_changes,
                "text_len": len(vision.get("text", "")),
            },
        )
        return {
            "path": entry["path"],
            "action": "vision_pass" if apply_changes else "planned",
            "pages": vision.get("pages", []),
            "model": vision.get("model"),
            "confidence": vision.get("confidence"),
            "low_confidence": vision.get("low_confidence"),
            "text": vision.get("text", ""),
            "sidecar": "/D/Scan0001.txt",
        }

    return [fake_list, fake_load, fake_vision, fake_store]


async def _run_vision(
    temporal_env, tmp_path, apply_changes=True, load_action="vision_pass"
):
    record_path = str(tmp_path / "calls.jsonl")
    fakes = _make_fakes(record_path, load_action)
    async with create_test_worker(temporal_env, [PdfVisionWorkflow], activities=fakes):
        raw = await temporal_env.client.execute_workflow(
            PdfVisionWorkflow.run,
            PdfVisionInput(target_path="/D", apply_changes=apply_changes),
            id=f"test-vision-{uuid.uuid4().hex[:8]}",
            task_queue="test-task-queue",
            execution_timeout=timedelta(seconds=60),
        )
        result = raw["result"] if isinstance(raw, dict) and "result" in raw else raw
    return result, _read_calls(record_path)


async def test_vision_pass_runs_and_stores(temporal_env, tmp_path):
    result, calls = await _run_vision(temporal_env, tmp_path, apply_changes=True)
    assert result["applied"] is True
    action = result["actions"][0]
    assert action["action"] == "vision_pass"
    assert action["sidecar"] == "/D/Scan0001.txt"
    assert action["text"].startswith("Vision Text Seite Eins")
    activities = [c["activity"] for c in calls]
    assert activities == ["list", "load", "vision", "store"]
    assert calls[2]["pages"] == [1, 2]
    assert calls[3]["apply_changes"] is True


async def test_dry_run_plans_without_writes(temporal_env, tmp_path):
    result, calls = await _run_vision(temporal_env, tmp_path, apply_changes=False)
    assert result["applied"] is False
    assert result["actions"][0]["action"] == "planned"
    # the store activity still runs (it reports the plan) but with apply off
    assert calls[-1]["activity"] == "store"
    assert calls[-1]["apply_changes"] is False


async def test_quality_ok_skips_vision(temporal_env, tmp_path):
    result, calls = await _run_vision(temporal_env, tmp_path, load_action="skipped")
    assert result["actions"][0]["action"] == "skipped"
    assert [c["activity"] for c in calls] == ["list", "load"]  # no vision, no store


def test_workflow_is_discovered():
    classes = workflows.discover_all_workflows_in_package("worker.workflows")
    names = {workflows.get_workflow_definition(c).name for c in classes}
    assert "pdf-vision" in names


def test_all_workflows_run_on_behalf_of_the_triggering_user():
    # on_behalf_of=True is what lets connector calls resolve the triggering
    # user's credentials (hardened deployment); without it the internal
    # connector_resolve activity gets 404 "Connector <name> not found".
    classes = workflows.discover_all_workflows_in_package("worker.workflows")
    for cls in classes:
        definition = workflows.get_workflow_definition(cls)
        assert definition.on_behalf_of is True, definition.name
