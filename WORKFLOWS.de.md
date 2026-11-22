> Sprache: Deutsch · [English version](WORKFLOWS.md)

# Workflow-Referenz

Vollständige Referenz der vier Workflows des Deployments `mistral-automation`: Zweck, Start-Input, Ergebnisform und Beispiele. KI-generierte Namen, Titel und Rückfragen folgen der Sprache des Dokuments (entsprechende Anweisung in den Prompts). Konventionen und Architektur stehen in [ARCHITECTURE.md](ARCHITECTURE.de.md), Bedienung und Deployment-Betrieb in [README.md](README.de.md) und [OPERATIONS.md](OPERATIONS.de.md). Jeder Start-Input akzeptiert außerdem ein optionales Feld `connector`: der Name eines Datei-Connectors aus `FILE_CONNECTOR_NAMES` (leer = Default), damit ein Aufruf gezielt einen der konfigurierten Connector ansprechen kann.

Alle Workflows:

- werden ausschließlich manuell gestartet (Studio oder Chat bzw. MCP `execute_workflow`);
- nutzen `apply_changes` (Standard `true`); `false` ist der Dry-Run: alles wird gelesen, geplant und geprüft, aber nichts geschrieben (auch keine Status-Rückmeldungen in Steuerdateien);
- benennen Dateien nach dem Schema `YYYY-MM-DD kurze, beschreibende Bezeichnung.pdf` (ASCII, Umlaute transliteriert), personenbezogene Dokumente mit `(Vorname)` am Ende;
- lesen ordnerspezifische Benennungsregeln aus `<Ordner>/AGENTS.md` (max. 4000 Zeichen; fehlt die Datei: nur der Basis-Prompt), wann immer die KI benennt;
- überschreiben niemals Dateien (Kollisionen erhalten `_2`, `_3`, ...).

## Ergebnis- und Action-Glossar

Jede Lauf-Ergebnis enthält oben `applied` (true = geschrieben, false = nur geplant/gelesen). Sammel-Workflows melden eine `actions`-Liste, Ein-Datei-Workflows ein einzelnes `action`-Feld. Einheitliches Action-Vokabular:

| Action | Bedeutung | Workflow |
|---|---|---|
| `planned` | Dry-Run: dies würde passieren | alle |
| `planned_pair` | Dry-Run: Duplex-Paar würde so benannt | triage |
| `renamed` | Datei umbenannt | triage |
| `renamed_pair` | Duplex-Paar umbenannt | triage |
| `left_unchanged` | unsicher (confidence < 0.7 oder kein Titel), nichts geändert | triage |
| `merged` | Duplex-Paar gemischt | merge |
| `replaced` | PDF wurde (per OCR) ersetzt | ocr |
| `skipped` | Datei übersprungen (Textebene vorhanden, `force` nicht gesetzt; Backup-Datei) | ocr |
| `split` | PDF wurde aufgeteilt | split |
| `no_split` | alles ein zusammenhängendes Dokument, kein Split | split |
| `clarification` | unsicher, Rückfrage mit `question` und `proposal`, nichts geschrieben | split |
| `error` | Fehler mit `error`-Meldung | alle |

## pdf-scan-inbox-triage

OCR und Benennung neuer Scans im Nextcloud-Scanner-Eingang (nur erste Ordnerebene).

**Input**

```json
{"folder_path": "/<Scanner-Eingang>", "apply_changes": true, "model": "mistral-small-latest", "connector": ""}
```

- `folder_path`: Scanner-Eingang; fehlt er, gilt das Workspace-Secret `SCAN_INBOX_FOLDER` (Umgebungsvariable im Deployment).
- `model`: LLM für die Benennung.

**Ablauf**: `Scan.pdf` / `ScanNNNN.pdf` finden → parallel analysieren (OCR, falls keine Textebene; Ergebnis ersetzt die Datei in Nextcloud) → Duplex-Paare erkennen (gleiche Seitenzahl, Größe Faktor 0.5-2.0, Textüberlappung >= 0.4) → benennen (`name-document` mit den AGENTS.md-Regeln des Eingangsordners) → nur Umbenennen. Paare werden nicht gemischt, sondern mit einem Suffix in der Dokumentsprache versehen (`Vorderseite`/`Rueckseite` bei deutschen Dokumenten, sonst `Front`/`Back`); das Mischen übernimmt pdf-merge-duplex.

**Ergebnis**: `{folder, applied, actions[]}` mit `action` pro Einheit aus `planned` / `planned_pair` / `renamed` / `renamed_pair` / `left_unchanged`.

## pdf-merge-duplex

Manuelles Duplex-Mischen, entweder direkt per `pairs` im Start-Input oder per JSON-Steuerdatei (Format: siehe ARCHITECTURE.md). Pro Eintrag: Seitenzahl prüfen, Vorder-/Rückseiten alternierend mischen (Rückseiten in umgekehrter Reihenfolge), benennen und neben den Quellen hochladen; Quelldateien wandern nach `merged/<Zielname>/`. Bei Steuerdatei schreibt der Workflow den Status je Eintrag zurück (`done`/`error`), wiederholte Läufe bearbeiten nur Einträge ohne Status.

**Input (Variante A, direkt)**

```json
{"pairs": [{"front": "/Ordner/Dok Vorderseite.pdf", "back": "/Ordner/Dok Rueckseite.pdf", "title": "2026-01-15 Rechnung A"}], "apply_changes": true, "model": "mistral-small-latest", "connector": ""}
```

**Input (Variante B, Steuerdatei)**

```json
{"control_file": "/Ordner/steuerdatei.json", "apply_changes": true, "model": "mistral-small-latest"}
```

Expliziter `title` gewinnt; sonst benennt die KI (`name-document` mit den AGENTS.md-Regeln des Ordners der Vorderseite). Unsichere Benennung markiert den Eintrag mit Fehler.

**Ergebnis**: `{source, control_file, applied, processed, actions[]}` mit `action` pro Eintrag aus `planned` / `merged` / `error`.

## pdf-ocr

Generischer OCR-Prozessor für eine einzelne PDF oder einen Ordner (`recursive: true` auch Unterordner). PDFs ohne Textebene werden mit OCRmyPDF (Tesseract `deu+eng`, Deskew, automatische Seitenrotation) aufbereitet und ersetzt; PDFs mit Textebene werden übersprungen (`force: true` erzwingt Neuaufbereitung). `keep_original: true` sichert das Original als `<Name>_orig.pdf`; vorhandene `*_orig.pdf` werden nie angefasst. Höchstens 200 Dateien pro Lauf.

**Input**

```json
{"target_path": "/Ordner", "recursive": false, "force": false, "keep_original": false, "apply_changes": true, "languages": "deu+eng", "connector": ""}
```

**Ergebnis**: `{target, kind, applied, actions[]}` mit `action` pro Datei aus `planned` / `replaced` / `skipped` / `error`; `note` meldet Limits und leere Ergebnisse.

## pdf-split

Teilt eine PDF in Teile und benennt sie; Teile bleiben im Ordner der Originaldatei, das Original wird nach `.originals/pdf-split/<Stamm>/` gesichert.

**Input**

```json
{"target_path": "/Ordner/Dokument.pdf", "split_at": [2, 4], "plan_splits": true, "titles": null, "name_parts": true, "model": "mistral-small-latest", "apply_changes": true, "connector": ""}
```

- `split_at`: Seitenzahlen (1-basiert), NACH denen getrennt wird; gewinnt immer über die Planung.
- `plan_splits` (Standard `true`): AI-Planung der Trennstellen; `false` ohne `split_at` ist ein Fehler.
- `titles`: explizite Teilnamen (Anzahl muss passen); schalten die AI-Benennung ab.
- `name_parts` (Standard `true`): AI-Benennung; `false` => `<Stamm> Teil-N.pdf`.

AI-benannte Teile heißen `<Titel>.pdf` (kein Stamm-Präfix), benannt nach den AGENTS.md-Regeln des Ordners.

**Ergebnis**: `{target, applied, action, pages, parts[], original?}`; `action` aus `planned` / `split` / `no_split` / `clarification` / `error`, `parts` mit `pages` (z. B. `"1-2"`), `count`, `to`.
