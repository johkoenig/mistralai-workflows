> Language: English · [Deutsche Version](ARCHITECTURE.de.md)

# Architecture

This repository contains the Mistral workflows for scan and PDF automation.
One repository (`mistralai-workflows`), one managed deployment
(`mistralai-workflows`), several workflows. Usage details are in the README, the complete workflow reference
including the result and action glossary in WORKFLOWS.md, deployment
operations in OPERATIONS.md. German version: [ARCHITECTURE.de.md](ARCHITECTURE.de.md).
This file records conventions and the division of work.

## Division of work

| Workflow | Task | Trigger |
|---|---|---|
| pdf-scan-inbox-triage | OCR and naming of new scans in the inbox | manual |
| pdf-merge-duplex | interleave duplex pairs alternately (control file) | manual |
| pdf-ocr | process PDFs without a text layer in general (one file, one folder, optionally recursive) | manual |
| pdf-split | split a PDF into parts (manual split points or LLM planning with a follow-up question), original moved to .originals/pdf-split/ | manual |

## Trigger philosophy

All workflows are started manually only (Studio or chat, or MCP
execute_workflow). There is deliberately no schedule and no webhook: an
automatic start in the middle of a scan job would tear apart incomplete
duplex sets and cause repeated runs on the same files. Heavy processing
only ever starts after a completed scan.

## Storage and naming conventions

- Targets: YYYY-MM-DD short, descriptive title.pdf (ASCII, umlauts
  transliterated, max. 80 characters of title; without a reliable date
  just the title).
- Duplex halves from the triage run additionally carry a front/back suffix in the
  document's language ("Vorderseite"/"Rueckseite" for German documents, "Front"/"Back"
  otherwise), on the same title.
- Source files of scanned duplex pairs are moved by the merge workflow to
  merged/<target name>/ where they are called FrontPages.pdf
  and BackPages_reversed.pdf.
- Uncertain naming (confidence < 0.7 in triage) is left untouched; the
  merge workflow then requires an explicit title.
- Scanned is the scanner inbox (start parameter folder_path; default from
  the environment variable SCAN_INBOX_FOLDER, bound as a workspace secret
  in the deployment), never subfolders.
- pdf-ocr with keep_original=true renames the original to <name>_orig.pdf
  before replacing it. Such _orig backups are never processed again in
  folder mode; if a backup already exists, the file is left behind with
  an error message.
- pdf-ocr processes PDFs only (any case), no images, and at most 200
  files per run (the rest is reported).

## Controlling pdf-merge-duplex

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
  uses the folder of the target PDF, pdf-scan-inbox-triage loads the
  rules once per run from the inbox folder, pdf-merge-duplex without an
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

- One workflow = one module in apps/worker/src/worker/workflows/ plus
  tests in apps/worker/tests/ (file names mirror the workflow name). The
  worker registers the package's modules automatically
  (discover_all_workflows_in_package).
- Shared logic (naming rules, duplex interleaving, text similarity,
  Nextcloud transfer, the name-document and load-naming-rules activities)
  lives in worker/pdf_ops.py and is imported, never duplicated. The
  constants MAX_CONCURRENT_OCR and NAMING_RULES_MAX_CHARS also live only
  there.
- The OCR pipeline (OCRmyPDF call, page rotation, deskew, text-layer
  check) lives in worker/ocr.py and is imported, never duplicated.
  Rotate and deskew are options of a single ocrmypdf call; separate passes
  would be a quality loss without benefit.
- Repository is `mistralai-workflows`, deployment is `mistralai-workflows`; workflow names
  follow the schema <domain>-<action> (pdf-scan-inbox-triage,
  pdf-merge-duplex, pdf-ocr, pdf-split).
- Managed deployments: no self-scaling; parallelism within a worker via
  asyncio.gather (MAX_CONCURRENT_OCR).

## Robustness

- Long activities (OCR, download, upload) send an activity heartbeat every 30 s (heartbeat_timeout 120 s) and have retry_policy_max_attempts=2. A worker restart during a run (rollout, crash) is detected in under two minutes and the activity retried; without a heartbeat Temporal would notice the task loss only after start_to_close_timeout (up to 45 min) and the run would sit silently.
- worker/ocr.py logs start, end, duration and errors of every OCR run to the runtime logs; progress is visible, also in dry runs.
- keep_original attempts are resumable: if the _orig backup already exists but the target file is missing, a previously interrupted run is resumed at the next start instead of duplicating.

## Original-file custody (.originals)

Secured originals are stored uniformly under `<folder>/.originals/<workflow>/<stem>/`. pdf-split parks the original under `.originals/pdf-split/<stem>/`; the folder is created per file, repeated runs create counter suffixes instead of overwriting. Benefit: one place to inspect and clean up ("what may go away at some point?"). Older processes still store elsewhere (duplex merge: `merged/`, OCR backup: `<name>_orig.pdf`); a later migration is possible.
