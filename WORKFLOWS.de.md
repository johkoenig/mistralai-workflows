> Sprache: Deutsch · [English version](WORKFLOWS.md)

# Workflow-Referenz

Vollständige Referenz der vier Workflows des Deployments `mistralai-workflows`: Zweck, Start-Input, Ergebnisform und Beispiele. Das Deployment ist ein zweistufiges System: `pdf-scanner-processor` (Orchestrator) steuert die drei spezialisierten Step-Workflows `pdf-ocr`, `pdf-alternating-merge` und `pdf-split` als Child-Workflows an — jeder Step ist auch einzeln startbar. KI-generierte Namen, Titel und Rückfragen folgen der Sprache des Dokuments (entsprechende Anweisung in den Prompts). Konventionen und Architektur stehen in [ARCHITECTURE.md](ARCHITECTURE.de.md), Bedienung und Deployment-Betrieb in [README.md](README.de.md) und [OPERATIONS.md](OPERATIONS.de.md). Jeder Start-Input akzeptiert außerdem ein optionales Feld `connector`: der Name eines Datei-Connectors aus `FILE_CONNECTOR_NAMES` (leer = Default), damit ein Aufruf gezielt einen der konfigurierten Connector ansprechen kann.

Alle Workflows:

- werden manuell gestartet (Studio oder Chat bzw. MCP `execute_workflow`); die Steps werden zusätzlich von `pdf-scanner-processor` als Child-Workflows aufgerufen;
- nutzen `apply_changes` (Standard `true`); `false` ist der Dry-Run: alles wird gelesen, geplant und geprüft, aber nichts geschrieben (auch keine Status-Rückmeldungen in Steuerdateien);
- lassen Dateinamen von der KI wählen — das Projekt selbst definiert kein Namensschema, es bereinigt nur technisch (ASCII, Umlaute transliteriert, max. 80 Zeichen);
- lesen die nächstliegende Benennungsregeln aus `AGENTS.md` (der Ordner selbst oder ein darüberliegender; max. 4000 Zeichen; existiert keine: wählt die KI selbst einen passenden Namen), wann immer die KI benennt; diese Regeln gehen vor dem Basis-Prompt und sind nicht Teil des Projekts;
- überschreiben niemals Dateien (Kollisionen erhalten `_2`, `_3`, ...);
- sichern Originale vor jeder Änderung unter `<Ordner>/.originals/<Workflow>/` (`pdf-alternating-merge` legt pro Misch-Ziel einen Unterordner an, alle anderen parken die Datei direkt); Backups sind in jedem Workflow standardmäßig an, und `.originals` wird nie durchsucht oder erneut verarbeitet.

## Ergebnis- und Action-Glossar

Jedes Laufergebnis enthält oben `applied` (true = geschrieben, false = nur geplant/gelesen). Sammel-Workflows melden eine `actions`-Liste, Ein-Datei-Workflows ein einzelnes `action`-Feld. Die `actions`-Einträge des Orchestrators tragen zusätzlich ein `step`-Feld (`backup`, `ocr`, `front-back`, `merge`, `name`, `split`). Einheitliches Action-Vokabular:

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
| `split` | PDF wurde aufgeteilt | split |
| `no_split` | alles ein zusammenhängendes Dokument, kein Split | split |
| `clarification` | unsicher, Rückfrage mit `question` und `proposal`, nichts geschrieben | split |
| `error` | Fehler mit `error`-Meldung | alle |

## pdf-scanner-processor

Orchestrator über einen Scanner-Eingangsordner (nur erste Ordnerebene): OCR, Vorder-/Rückseiten-Erkennung + alternierendes Mischen, LLM-Benennung und bei sicherer Erkennung ein Split pro Datei — komplett durchgereicht, aufgebaut aus den Step-Workflows `pdf-ocr`, `pdf-alternating-merge` und `pdf-split` (als Child-Workflows mit deterministischen Ausführungs-IDs `{run}-{step}-{item}` aufgerufen).

**Input**

```json
{"folder_path": "/<Scanner-Eingang>", "apply_changes": true, "model": "mistral-small-latest", "connector": "", "merge_front_back": true, "split_parts": true}
```

- `folder_path`: Scanner-Eingang; fehlt er, gilt das Workspace-Secret `SCAN_INBOX_FOLDER` (Umgebungsvariable im Deployment).
- `model`: LLM für die Prüfungen und die Benennung.
- `merge_front_back: false` überspringt Paar-Erkennung/Mischen, `split_parts: false` überspringt den Split-Step.

**Ablauf**: `Scan.pdf` / `ScanNNNN.pdf` finden → Kopie jedes Roh-Scans unter `.originals/pdf-scanner-processor/` parken → Child `pdf-ocr` auf den Ordner (eigene Backups unter `.originals/pdf-ocr/`) → Scans analysieren (Seitenzahl, Textebene, Textausschnitt) → Kandidaten für Vorder-/Rückseiten-Paare (aufeinanderfolgende Scan-Reihenfolge, gleiche Seitenzahl, Größenfaktor 0.5-2.0, beide mit Textebene) per LLM-Activity `front-back-check` bestätigen (Annahme ab confidence >= 0.7; die Prüfung ist namensunabhängig, Vorder- und Rückseite dürfen also völlig anders aussehen) → Child `pdf-alternating-merge` pro bestätigtem Paar mit dem Zwischen-Titel `ScanMerged<N>.pdf` (N = größere Scan-Nummer des Paares; Quellen archiviert unter `.originals/pdf-alternating-merge/<Ziel>/`) → jede Ergebnisdatei benennen (`name-document` mit den nächstliegenden AGENTS.md-Regeln — Eingangsordner oder ein Elternordner; gemischte Paare über den kombinierten Text) → nur bei sicherer Erkennung (>= 0.7) umbenennen, sonst `left_unchanged` → Child `pdf-split` pro Ergebnisdatei (teilt nur bei sicherer Erkennung).

**Ergebnis**: `{folder, applied, actions[]}`; jeder Eintrag trägt `step` (`backup` / `ocr` / `front-back` / `merge` / `name` / `split`) plus die step-spezifischen Felder (`files`, `action`, `to`, `backup`, `confidence`, `result`, ...).

## pdf-alternating-merge

Alternierendes Mischen von Duplex-Paaren, entweder direkt per `pairs` im Start-Input oder per JSON-Steuerdatei (Format: siehe ARCHITECTURE.md). Pro Eintrag: Seitenzahl prüfen, Vorder-/Rückseiten alternierend mischen (Rückseiten in umgekehrter Reihenfolge), benennen und neben den Quellen hochladen; die Quelldateien werden unter `.originals/pdf-alternating-merge/<Zielname>/` gesichert (als `FrontPages.pdf` / `BackPages_reversed.pdf`). Bei Steuerdatei schreibt der Workflow den Status je Eintrag zurück (`done`/`error`), wiederholte Läufe bearbeiten nur Einträge ohne Status. Der Orchestrator ruft diesen Step pro erkanntem Paar mit explizitem Zwischen-`title` auf (`ScanMerged<N>.pdf`).

**Input (Variante A, direkt)**

```json
{"pairs": [{"front": "/Ordner/Dok Vorderseite.pdf", "back": "/Ordner/Dok Rueckseite.pdf", "title": "2026-01-15 Rechnung A"}], "apply_changes": true, "model": "mistral-small-latest", "connector": ""}
```

**Input (Variante B, Steuerdatei)**

```json
{"control_file": "/Ordner/steuerdatei.json", "apply_changes": true, "model": "mistral-small-latest"}
```

Expliziter `title` gewinnt; sonst benennt die KI (`name-document` mit den nächstliegenden AGENTS.md-Regeln — Ordner der Vorderseite oder ein Elternordner). Unsichere Benennung markiert den Eintrag mit Fehler.

**Ergebnis**: `{source, control_file, applied, processed, actions[]}` mit `action` pro Eintrag aus `planned` / `merged` / `error`.

## pdf-ocr

Generischer OCR-Prozessor für eine einzelne PDF oder einen Ordner (`recursive: true` auch Unterordner, aber nie `.originals`). PDFs ohne Textebene werden mit OCRmyPDF (Tesseract `deu+eng`, Deskew, automatische Seitenrotation) aufbereitet und ersetzt; PDFs mit Textebene werden übersprungen (`force: true` erzwingt Neuaufbereitung). `keep_original` (Standard `true`) verschiebt das Original vor dem Ersetzen nach `.originals/pdf-ocr/<Name>`; existiert bereits ein Backup, bleibt die Datei mit Fehlermeldung stehen, und ein Backup ohne Zieldatei setzt einen unterbrochenen Lauf fort. Höchstens 200 Dateien pro Lauf.

**Input**

```json
{"target_path": "/Ordner", "recursive": false, "force": false, "keep_original": true, "apply_changes": true, "languages": "deu+eng", "connector": ""}
```

**Ergebnis**: `{target, kind, applied, actions[]}` mit `action` pro Datei aus `planned` / `replaced` / `skipped` / `error`; `note` meldet Limits und leere Ergebnisse.

## pdf-split

Teilt eine PDF in Teile und benennt sie; Teile bleiben im Ordner der Originaldatei, das Original wird nach `.originals/pdf-split/<Name>` gesichert.

**Input**

```json
{"target_path": "/Ordner/Dokument.pdf", "split_at": [2, 4], "plan_splits": true, "titles": null, "name_parts": true, "model": "mistral-small-latest", "apply_changes": true, "connector": ""}
```

- `split_at`: Seitenzahlen (1-basiert), NACH denen getrennt wird — die angegebene Seite bleibt im vorherigen Teil; gewinnt immer über die Planung. Beispiel: `[2, 4]` bei 8 Seiten ergibt die Teile 1-2, 3-4, 5-8. Dieselbe Semantik steht in der Input-Schema-Beschreibung des Feldes `split_at` und in der Workflow-Beschreibung. Ein Dry-Run (`apply_changes: false`) liefert `action: "planned"` mit dem genauen `pages`-Bereich jedes Teils, bevor etwas geschrieben wird.
- `plan_splits` (Standard `true`): AI-Planung der Trennstellen; `false` ohne `split_at` ist ein Fehler.
- `titles`: explizite Teilnamen (Anzahl muss passen); schalten die AI-Benennung ab.
- `name_parts` (Standard `true`): AI-Benennung; `false` => `<Stamm> Teil-N.pdf`.

Heuristiken der AI-Planung (im Split-Prompt): Ein NEUES Dokument wird angenommen, wenn sich das Datum ändert, wenn sich eine (laufende) Seitenzahl ändert (z. B. ein neuer Lauf 'Seite 1 von 2' beginnt) oder wenn oben auf einer Seite ein neuer Firmenname/Briefkopf steht — eine geänderte Firma oder ein geänderter Vertragspartner bedeutet fast immer ein neues Dokument (z. B. Rechnung). Ausnahme: Ein Firmenname, der nur erscheint, weil eine Fremdleistung im Namen von jemandem abgerechnet oder durchgerechnet wurde ('Fremdleistung im Namen von XY' / 'on behalf of XY'), ist KEIN neues Dokument. Das Modell prüft außerdem jede geplante Trennstelle vor dem Finalisieren und entfernt Trennstellen (führt betroffene Teile wieder zusammen), die mitten durch ein kohärentes Dokument schneiden würden.

AI-benannte Teile heißen `<Titel>.pdf` (kein Stamm-Präfix), benannt nach den nächstliegenden AGENTS.md-Regeln.

**Ergebnis**: `{target, applied, action, pages, parts[], original?}`; `action` aus `planned` / `split` / `no_split` / `clarification` / `error`, `parts` mit `pages` (z. B. `"1-2"`), `count`, `to`.
