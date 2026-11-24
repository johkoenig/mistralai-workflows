> Language: English · [Deutsche Version](ARCHITECTURE.de.md)

# Architecture

This repository contains the Mistral workflows for scan and PDF automation.
One repository (`mistralai-workflows`), one managed deployment
(`mistralai-workflows`), several workflows. Usage details are in the README, the complete workflow reference
including the result and action glossary in WORKFLOWS.md, deployment
operations in OPERATIONS.md. German version: [ARCHITECTURE.de.md](ARCHITECTURE.de.md).
This file records conventions and the division of work.

## Division of work

Two levels: three single-purpose step workflows in `workflows/steps/` and one
orchestrator in `workflows/` that calls them as child workflows.

| Workflow | Task | Trigger |
|---|---|---|
| pdf-scanner-processor | orchestrator: OCR, front/back detection + merge, naming, split over the scanner inbox | manual |
| pdf-ocr | process PDFs without a text layer (one file, one folder, optionally recursive) | manual or child of pdf-scanner-processor |
| pdf-alternating-merge | interleave duplex pairs alternately (pairs input or control file) | manual or child of pdf-scanner-processor |
| pdf-split | split a PDF into parts (explicit split points or LLM planning with a follow-up question), original moved to .originals/pdf-split/ | manual or child of pdf-scanner-processor |

## Trigger philosophy

All workflows are started manually only (Studio or chat, or MCP
execute_workflow). There is deliberately no schedule and no webhook: an
automatic start in the middle of a scan job would tear apart incomplete
duplex sets and cause repeated runs on the same files. Heavy processing
only ever starts after a completed scan. The only "automatic" triggering is
inside one orchestrator run: pdf-scanner-processor starts the steps as child
workflows with deterministic execution IDs.

## Storage and naming conventions

- Targets: YYYY-MM-DD short, descriptive title.pdf (ASCII, umlauts
  transliterated, max. 80 characters of title; without a reliable date
  just the title).
- A merged front/back pair first gets the intermediate name
  ScanMerged<N>.pdf (N = larger scan number of the pair); the orchestrator
  renames it to the final title in the naming step. ScanMerged*.pdf does not
  match the scan name pattern, so later runs do not pick it up as a new scan.
- Source files of scanned duplex pairs are backed up by the merge step under
  .originals/pdf-alternating-merge/<target name>/ where they are called
  FrontPages.pdf and BackPages_reversed.pdf.
- Uncertain naming (confidence < 0.7 in the orchestrator) is left untouched;
  the merge step then requires an explicit title.
- Scanned is the scanner inbox (start parameter folder_path; default from
  the environment variable SCAN_INBOX_FOLDER, bound as a workspace secret
  in the deployment), never subfolders.
- pdf-ocr with keep_original (default true) moves the original to
  .originals/pdf-ocr/<name> before replacing it. In folder mode .originals is
  never descended into, so backups are never reprocessed; if a backup already
  exists, the file is left behind with an error message.
- pdf-ocr processes PDFs only (any case), no images, and at most 200
  files per run (the rest is reported).

## Controlling pdf-alternating-merge

There are two equivalent ways:

- Variant A (direct): the pairs are passed as a "pairs" list in the start
  input (front/back/title per entry). Results and errors appear in the run
  result; no file is read back or written.
- Variant B (control file): JSON file in Nextcloud, e.g.
  /<scanner inbox>/steuerdatei.json, started with the path to the file:

    {
      "pairs": [
        {
          "front": "/<scanner inbox>/2026-01-15 Example A Front.pdf",
          "back":  "/<scanner inbox>/2026-01-15 Example A Back.pdf",
          "title": "2026-01-15 Example A"
        }
      ]
    }

Rules:

- front/back: absolute Nextcloud paths, both PDFs, same page count.
- title: optional. Without a title the LLM names from the combined OCR
  text; if the result is uncertain, the entry is marked with an error and
  the title must be supplied.
- After processing, the workflow writes back per entry:
  "status": "done" with "result" (to/archive/pages) or "status": "error"
  with an "error" message. Repeated runs process only entries without status.
- apply_changes=false (dry run): plans targets, writes nothing, not even
  a status.

## Planning of pdf-split

pdf-split has two AI steps, both individually switchable:

- Split-point planning (plan_splits, default true): without split_at the
  LLM proposes split points; a given split_at always wins. plan_splits
  false without split_at is an error (not: silently no split).
- Part naming (name_parts, default true): the LLM provides part titles
  (in AI mode in the same plan-split call as the split points, in manual
  mode as a separate call against fixed split points); false => fallback
  <stem> Teil-N. Explicit titles disable AI naming.
- Folder-specific naming rules apply to every AI naming: the shared
  load-naming-rules activity in worker/pdf_ops.py reads
  <folder>/AGENTS.md (max. 4000 characters; if missing: empty) and
  appends it to the prompt (it has priority in title selection). pdf-split
  uses the folder of the target PDF, pdf-scanner-processor loads the
  rules once per run from the inbox folder, pdf-alternating-merge without an
  explicit title from the folder of the front page (name_document accepts
  naming_rules).
- AI-named parts are called <title>.pdf without the prefix of the
  original stem (part_filename); context remains via the original in
  .originals/pdf-split/.
- Invalid proposals are reported as a follow-up question
  (parse_split_plan validates against part_ranges), never applied
  blindly. Uncertain (confidence < 0.7 or needs_input) => action
  "clarification" with question and proposal, without writing. All one
  document => action "no_split".
- Structure: load-split-pages loads and reads, the workflow entrypoint
  plans and decides (resolve_plan, pure logic, testable), split-file
  writes.

## Code conventions

- One workflow = one module in apps/worker/src/worker/workflows/ (orchestrator)
  or apps/worker/src/worker/workflows/steps/ (step workflows), plus tests in
  apps/worker/tests/ (file names mirror the workflow name). The worker
  discovers the package recursively (discover_all_workflows_in_package), so
  the steps subpackage needs no entrypoint change.
- Shared logic (naming rules, duplex interleaving, front/back detection,
  scan-name handling, Nextcloud transfer, the name-document, front-back-check
  and load-naming-rules activities) lives in worker/pdf_ops.py and is
  imported, never duplicated. The constants MAX_CONCURRENT_OCR and
  NAMING_RULES_MAX_CHARS also live only there.
- The OCR pipeline (OCRmyPDF call, page rotation, deskew, text-layer
  check) lives in worker/ocr.py and is imported, never duplicated.
  Rotate and deskew are options of a single ocrmypdf call; separate passes
  would be a quality loss without benefit.
- Repository is `mistralai-workflows`, deployment is `mistralai-workflows`; workflow names
  follow the schema <domain>-<action> (pdf-scanner-processor,
  pdf-alternating-merge, pdf-ocr, pdf-split).
- Orchestrator mechanics: the step classes live in the STEPS registry of
  pdf_scanner_processor.py; child workflows are started by name via
  workflows.workflow.execute_workflow with deterministic execution IDs
  ({run}-{step}-{item}, run = uuid4 of the run, stable on replay), per-file
  children in parallel via wait=False + asyncio.Semaphore. Because children
  dispatch by workflow name, tests register fake step classes under the step
  names in the test worker.
- Managed deployments: no self-scaling; parallelism within a worker via
  asyncio.gather (MAX_CONCURRENT_OCR).

## Robustness

- Long activities (OCR, download, upload) send an activity heartbeat every 30 s (heartbeat_timeout 120 s) and have retry_policy_max_attempts=2. A worker restart during a run (rollout, crash) is detected in under two minutes and the activity retried; without a heartbeat Temporal would notice the task loss only after start_to_close_timeout (up to 45 min) and the run would sit silently.
- worker/ocr.py logs start, end, duration and errors of every OCR run to the runtime logs; progress is visible, also in dry runs.
- keep_original attempts are resumable: if the backup in .originals/pdf-ocr already exists but the target file is missing, a previously interrupted run is resumed at the next start instead of duplicating.

## Original-file custody (.originals)

Secured originals are stored uniformly under `<folder>/.originals/<workflow>/`, on by default in every workflow. pdf-split parks the original under `.originals/pdf-split/<stem>/` (one folder per file, repeated runs create counter suffixes instead of overwriting); pdf-alternating-merge backs the pair sources up under `.originals/pdf-alternating-merge/<target name>/`; pdf-ocr moves the original to `.originals/pdf-ocr/<name>`; pdf-scanner-processor parks a copy of each raw scan under `.originals/pdf-scanner-processor/` before anything modifies it. Benefit: one place to inspect and clean up ("what may go away at some point?").
