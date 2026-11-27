> Sprache: Deutsch · [English version](WORKFLOWS.md)

# Workflow-Referenz

Vollständige Referenz der fünf Workflows des Deployments `mistralai-workflows`: Zweck, Start-Input, Ergebnisform und Beispiele. Das Deployment ist ein zweistufiges System: `pdf-scanner-processor` (Orchestrator) steuert die vier spezialisierten Step-Workflows `pdf-ocr`, `pdf-alternating-merge`, `pdf-split` und `pdf-vision` als Child-Workflows an — jeder Step ist auch einzeln startbar. KI-generierte Namen, Titel und Rückfragen folgen der Sprache des Dokuments (entsprechende Anweisung in den Prompts). Konventionen und Architektur stehen in [ARCHITECTURE.md](ARCHITECTURE.de.md), Bedienung und Deployment-Betrieb in [README.md](README.de.md) und [OPERATIONS.md](OPERATIONS.de.md). Jeder Start-Input akzeptiert außerdem ein optionales Feld `connector`: der Name eines Datei-Connectors aus `FILE_CONNECTOR_NAMES` (leer = Default), damit ein Aufruf gezielt einen der konfigurierten Connector ansprechen kann.

Alle Workflows:

- werden manuell gestartet (Studio oder Chat bzw. MCP `execute_workflow`); die Steps werden zusätzlich von `pdf-scanner-processor` als Child-Workflows aufgerufen;
- nutzen `apply_changes` (Standard `true`); `false` ist der Dry-Run: alles wird gelesen, geplant und geprüft, aber nichts geschrieben (auch keine Status-Rückmeldungen in Steuerdateien);
- lassen Dateinamen von der KI wählen — das Projekt selbst definiert kein Namensschema, es bereinigt nur technisch (ASCII, Umlaute transliteriert, max. 80 Zeichen);
- lesen die nächstliegende Benennungsregeln aus `AGENTS.md` (der Ordner selbst oder ein darüberliegender; max. 4000 Zeichen; existiert keine: wählt die KI selbst einen passenden Namen), wann immer die KI benennt; diese Regeln gehen vor dem Basis-Prompt und sind nicht Teil des Projekts;
- überschreiben niemals Dateien (Kollisionen erhalten `_2`, `_3`, ...);
- sichern Originale vor jeder Änderung unter `<Ordner>/.originals/<Workflow>/` (`pdf-alternating-merge` legt pro Misch-Ziel einen Unterordner an, alle anderen parken die Datei direkt); Backups sind in jedem Workflow standardmäßig an, und `.originals` wird nie durchsucht oder erneut verarbeitet.

## Ergebnis- und Action-Glossar

Jedes Laufergebnis enthält oben `applied` (true = geschrieben, false = nur geplant/gelesen). Sammel-Workflows melden eine `actions`-Liste, Ein-Datei-Workflows ein einzelnes `action`-Feld. Die `actions`-Einträge des Orchestrators tragen zusätzlich ein `step`-Feld (`backup`, `ocr`, `vision`, `front-back`, `merge`, `name`, `split`). Einheitliches Action-Vokabular:

| Action | Bedeutung | Workflow |
|---|---|---|
| `planned` | Dry-Run: dies würde passieren | alle |
| `renamed` | Datei umbenannt | Orchestrator (Step `name`) |
| `left_unchanged` | unsicher (confidence < 0.7 oder kein Titel), nichts geändert | Orchestrator (Step `name`) |
| `pair` / `no_pair` | Vorder-/Rückseiten-Entscheidung des LLM-Checks | Orchestrator (Step `front-back`) |
| `merged` | Duplex-Paar gemischt | Merge-Step (auch Orchestrator-Step `merge`) |
| `deferred` | Dry-Run: gemischte Datei ist nur geplant, Split käme nach einem echten Merge | Orchestrator (Step `split`) |
| `replaced` | PDF wurde (per OCR) ersetzt | ocr |
| `skipped` | Datei übersprungen (Textebene vorhanden, `force` nicht gesetzt; Backup-Datei) | ocr |
| `vision_pass` | Vision-OCR-Fallback lief (Sidecar-txt + PDF-Metadaten-Flag, Textebene unangetastet) | vision (auch Orchestrator-Step `vision`) |
| `split` | PDF wurde aufgeteilt | split |
| `no_split` | alles ein zusammenhängendes Dokument, kein Split | split |
| `clarification` | unsicher, Rückfrage mit `question` und `proposal`, nichts geschrieben | split |
| `error` | Fehler mit `error`-Meldung | alle |

## pdf-scanner-processor

Orchestrator über einen Scanner-Eingangsordner (nur erste Ordnerebene): OCR, Vorder-/Rückseiten-Erkennung + alternierendes Mischen, LLM-Benennung und bei sicherer Erkennung ein Split pro Datei — komplett durchgereicht, aufgebaut aus den Step-Workflows `pdf-ocr`, `pdf-alternating-merge`, `pdf-split` und `pdf-vision` (als Child-Workflows mit deterministischen Ausführungs-IDs `{run}-{step}-{item}` aufgerufen).

**Input**

```json
{"folder_path": "/<Scanner-Eingang>", "apply_changes": true, "model": "mistral-small-latest", "connector": "", "merge_front_back": true, "split_parts": true, "vision_fallback": true, "vision_model": "", "ocr_languages": "deu+eng"}
```

- `folder_path`: Scanner-Eingang; fehlt er, gilt das Workspace-Secret `SCAN_INBOX_FOLDER` (Umgebungsvariable im Deployment).
- `model`: LLM für die Prüfungen und die Benennung.
- `merge_front_back: false` überspringt Paar-Erkennung/Mischen, `split_parts: false` überspringt den Split-Step `vision_fallback: false` überspringt den Vision-Fallback (Schritt 2.5), `vision_model` überschreibt die Umgebungsvariable `PDF_VISION_MODEL`. `ocr_languages` setzt die OCR-Sprachen für den pdf-ocr-Child (Default `deu+eng`; Scanner-Eingangs-Läufe können `deu` setzen und so die OCR-Zeit etwa halbieren) — die Benennung läuft dann direkt auf jede Einheit.

**Ablauf**: `Scan.pdf` / `ScanNNNN.pdf` finden → Kopie jedes Roh-Scans unter `.originals/pdf-scanner-processor/` parken → Child `pdf-ocr` auf den Ordner (`with_pages_text=true`; eigene Backups unter `.originals/pdf-ocr/`; das Ergebnis trägt pro Datei Seitenzahl, `has_text`, Textausschnitt, seitenweise Texte und das `ocr_quality`-Flag (`low_confidence` bei schwachem OCR), es wird also nichts erneut heruntergeladen) → Vision-Fallback für `low_confidence`-OCR-Ergebnisse via Child `pdf-vision` (Schritt 2.5; Vision-Text fließt nur in Erkennung/Split/Benennung ein, nie in die PDF-Textebene) → Kandidaten für Vorder-/Rückseiten-Paare (aufeinanderfolgende Scan-Reihenfolge, gleiche Seitenzahl, Größenfaktor 0.5-2.0, beide mit Textebene) per LLM-Activity `front-back-check` bestätigen (Annahme ab confidence >= 0.7; die Prüfung ist namensunabhängig, Vorder- und Rückseite dürfen also völlig anders aussehen) → Child `pdf-alternating-merge` pro bestätigtem Paar mit dem Zwischen-Titel `ScanMerged<N>.pdf` (N = größere Scan-Nummer des Paares; Quellen archiviert unter `.originals/pdf-alternating-merge/<Ziel>/`) → Split-Planung INLINE pro Einheit über die Activity `plan-split` (VOR der Benennung; inhaltsbasiert, Teile werden aus dem Plan benannt) — der `pdf-split`-Child wird nur bei tatsächlich bestätigtem Split gestartet, dann mit festem `split_at` + `titles` → jede nicht gesplittete Einheit benennen (`name-document` mit den nächstliegenden AGENTS.md-Regeln — Eingangsordner oder ein Elternordner; gemischte Paare über den kombinierten Text; Split-Teile werden als `named_by_split` reported) → nur bei sicherer Erkennung (>= 0.7) umbenennen, sonst `left_unchanged`.

**Ergebnis**: `{folder, applied, actions[]}`; jeder Eintrag trägt `step` (`backup` / `ocr` / `front-back` / `merge` / `name` / `split`) plus die step-spezifischen Felder (`files`, `action`, `to`, `backup`, `confidence`, `result`, ...).

## pdf-alternating-merge

Alternierendes Mischen von Duplex-Paaren, entweder direkt per `pairs` im Start-Input oder per JSON-Steuerdatei (Format: siehe ARCHITECTURE.md). Pro Eintrag: Seitenzahl prüfen, Vorder-/Rückseiten alternierend mischen (Rückseiten in umgekehrter Reihenfolge), benennen und neben den Quellen hochladen; die Quelldateien werden unter `.originals/pdf-alternating-merge/<Zielname>/` gesichert (als `FrontPages.pdf` / `BackPages_reversed.pdf`). Bei Steuerdatei schreibt der Workflow den Status je Eintrag zurück (`done`/`error`), wiederholte Läufe bearbeiten nur Einträge ohne Status. Der Orchestrator ruft diesen Step pro erkanntem Paar mit explizitem Zwischen-`title` auf (`ScanMerged<N>.pdf`).

**Input (Variante A, direkt)**

```json
{"pairs": [{"front": "/Ordner/Dok Vorderseite.pdf", "back": "/Ordner/Dok Rueckseite.pdf", "title": "2026-01-15 Rechnung A"}], "apply_changes": true, "model": "mistral-small-latest", "connector": "", "naming_rules": ""}
```

**Input (Variante B, Steuerdatei)**

```json
{"control_file": "/Ordner/steuerdatei.json", "apply_changes": true, "model": "mistral-small-latest"}
```

Expliziter `title` gewinnt; sonst benennt die KI (`name-document` mit den nächstliegenden AGENTS.md-Regeln — Ordner der Vorderseite oder ein Elternordner). Unsichere Benennung markiert den Eintrag mit Fehler.

**Ergebnis**: `{source, control_file, applied, processed, actions[]}` mit `action` pro Eintrag aus `planned` / `merged` / `error`.

## pdf-ocr

Generischer OCR-Prozessor für eine einzelne PDF oder einen Ordner (`recursive: true` auch Unterordner, aber nie `.originals`). PDFs ohne Textebene werden mit OCRmyPDF (Tesseract `deu+eng`, Deskew, automatische Seitenrotation) aufbereitet und ersetzt; PDFs mit Textebene werden übersprungen (`force: true` erzwingt Neuaufbereitung). `keep_original` (Standard `true`) verschiebt das Original vor dem Ersetzen nach `.originals/pdf-ocr/<Name>`; existiert bereits ein Backup, bleibt die Datei mit Fehlermeldung stehen, und ein Backup ohne Zieldatei setzt einen unterbrochenen Lauf fort. Höchstens 200 Dateien pro Lauf.
- `with_pages_text` (Standard `false`): jede Ergebnis-Action enthält dann zusätzlich `has_text`, einen `text`-Ausschnitt (max. 6000 Zeichen) und `pages_text` (pro Seite gedeckelt) — Pipeline-Caller (pdf-scanner-processor) brauchen dann keinen zweiten Download. Standard aus, damit Ordner-Läufe schlanke Ergebnisse liefern. Dateien, die per OCR aufbereitet wurden (`replaced` / `planned` mit Text), tragen außerdem `ocr_quality` (`confidence`, `low_confidence`, `metrics`) aus der Heuristik `assess-ocr-quality` — `low_confidence` markiert schwache OCR-Ergebnisse (Schwelle `OCR_QUALITY_MIN_CONFIDENCE`, Default `0.6`).

**Input**

```json
{"target_path": "/Ordner", "recursive": false, "force": false, "keep_original": true, "apply_changes": true, "languages": "deu+eng", "connector": "", "with_pages_text": false}
```

**Ergebnis**: `{target, kind, applied, actions[]}` mit `action` pro Datei aus `planned` / `replaced` / `skipped` / `error`; `note` meldet Limits und leere Ergebnisse.

## pdf-split

Teilt eine PDF in Teile und benennt sie; Teile bleiben im Ordner der Originaldatei, das Original wird nach `.originals/pdf-split/<Name>` gesichert.

**Input**

```json
{"target_path": "/Ordner/Dokument.pdf", "split_at": [2, 4], "plan_splits": true, "titles": null, "name_parts": true, "model": "mistral-small-latest", "apply_changes": true, "connector": "", "naming_rules": ""}
```

- `split_at`: Seitenzahlen (1-basiert), NACH denen getrennt wird — die angegebene Seite bleibt im vorherigen Teil; gewinnt immer über die Planung. Beispiel: `[2, 4]` bei 8 Seiten ergibt die Teile 1-2, 3-4, 5-8. Dieselbe Semantik steht in der Input-Schema-Beschreibung des Feldes `split_at` und in der Workflow-Beschreibung. Ein Dry-Run (`apply_changes: false`) liefert `action: "planned"` mit dem genauen `pages`-Bereich jedes Teils, bevor etwas geschrieben wird.
- `plan_splits` (Standard `true`): AI-Planung der Trennstellen; `false` ohne `split_at` ist ein Fehler.
- `titles`: explizite Teilnamen (Anzahl muss passen); schalten die AI-Benennung ab.
- `name_parts` (Standard `true`): AI-Benennung; `false` => `<Stamm> Teil-N.pdf`.
- `naming_rules`: vom Caller übergebene Benennungsregeln (z. B. vom Orchestrator pdf-scanner-processor); leer = die nächstliegende AGENTS.md wird selbst geladen.

Heuristiken der AI-Planung (im Split-Prompt): Ein NEUES Dokument wird angenommen, wenn sich das Datum ändert, wenn sich eine (laufende) Seitenzahl ändert (z. B. ein neuer Lauf 'Seite 1 von 2' beginnt) oder wenn oben auf einer Seite ein neuer Firmenname/Briefkopf steht — eine geänderte Firma oder ein geänderter Vertragspartner bedeutet fast immer ein neues Dokument (z. B. Rechnung). Ausnahme: Ein Firmenname, der nur erscheint, weil eine Fremdleistung im Namen von jemandem abgerechnet oder durchgerechnet wurde ('Fremdleistung im Namen von XY' / 'on behalf of XY'), ist KEIN neues Dokument. Das Modell prüft außerdem jede geplante Trennstelle vor dem Finalisieren und entfernt Trennstellen (führt betroffene Teile wieder zusammen), die mitten durch ein kohärentes Dokument schneiden würden.

AI-benannte Teile heißen `<Titel>.pdf` (kein Stamm-Präfix), benannt nach den nächstliegenden AGENTS.md-Regeln.

**Ergebnis**: `{target, applied, action, pages, parts[], original?}`; `action` aus `planned` / `split` / `no_split` / `clarification` / `error`, `parts` mit `pages` (z. B. `"1-2"`), `count`, `to`.

## pdf-vision

Vision-OCR-Fallback für gescannte PDFs: rendert Seiten (pypdfium2, max. Kante 1540 px) und liest sie mit einem vision-fähigen Chat-Modell (base64-PNG-Teile, max. 8 Bilder pro Request). Der Vision-Text fließt in Duplex-Erkennung, Split-Planung und Benennung im Orchestrator ein - die durchsuchbare Textebene der PDF wird nie ersetzt.

**Input**

```json
{"target_path": "/Ordner", "recursive": false, "pages": null, "force": false, "apply_changes": true, "model": "", "languages": "deu+eng", "connector": ""}
```

- `pages`: explizite 1-basierte Seitenzahlen; leer = bei `force=true` alle Seiten, sonst nur Dateien mit `low_confidence` laut OCR-Quality-Heuristik (assess-ocr-quality). Pro Datei gedeckelt durch `MAX_VISION_PAGES` (Default 8).
- `model`: leer = Umgebungsvariable `PDF_VISION_MODEL` (Default `mistral-small-latest`, vision-fähig).
- `apply_changes=false` (Dry-Run) plant alles und schreibt nichts.

**Ergebnis-Speicherung** (`apply_changes=true`): Sidecar `<Name>.txt` neben der PDF (nie überschreiben - Kollision bekommt `_2`-Suffix) plus Verarbeitungs-Flag in den PDF-Metadaten (`/VisionOcrPass`: Modell, Confidence, Datum, Seiten); die Textebene bleibt unangetastet.

**Ergebnis**: `{target, kind, applied, actions[]}`; `action` pro Datei aus `vision_pass` / `planned` / `skipped` / `error`, mit `pages`, `model`, `confidence`, `low_confidence`, `text`, `sidecar`.
