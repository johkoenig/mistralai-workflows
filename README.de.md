> Sprache: Deutsch · [English version](README.md)

# mistralai-workflows: PDF-Scan-Triage, Duplex-Mischung, Split und OCR als Mistral-Workflows

[![CI](https://img.shields.io/github/actions/workflow/status/johkoenig/mistralai-workflows/ci.yml?branch=main&label=CI)](https://github.com/johkoenig/mistralai-workflows/actions/workflows/ci.yml)

Ein Repository, ein Managed Deployment in deinem Mistral-Workspace, vier Workflows, die auf deinen Dokumenten **in deinem eigenen Speicher** laufen. Nichts verlässt den verbundenen Speicher, außer den OCR-/Benennungs-Prompts an die Mistral-API.

| Workflow | Was er tut |
|---|---|
| `pdf-scan-inbox-triage` | OCR und LLM-Benennung neuer Scans (`Scan.pdf` / `ScanNNNN.pdf`) im Scanner-Eingangsordner; Duplex-Paare bekommen ein Vorder-/Rückseiten-Suffix in der Dokumentsprache — werden aber nicht gemischt |
| `pdf-merge-duplex` | Mischt Duplex-Paare (Vorder-/Rückseiten alternierend) per JSON-Steuerdatei oder direktem `pairs`-Start-Input |
| `pdf-split` | Teilt eine PDF an angegebenen Seitenzahlen oder per LLM geplanten Trennstellen (Rückfrage bei Unsicherheit) |
| `pdf-ocr` | OCR für eine einzelne PDF oder einen ganzen Ordner (optional rekursiv) |

Alle vier werden **ausschließlich manuell** gestartet (Studio, Chat oder MCP `execute_workflow`) — nie per Schedule oder Webhook, damit ein Lauf nie einen unvollständigen Scan-Job auseinanderreißt.

## Schnellstart

1. **Deployment**: Lege ein Managed-Workflow-Deployment namens `mistralai-workflows` aus diesem Repository an — Dockerfile `deploy/docker/Dockerfile.worker`, Build-Kontext `.`, `hardened=true` (die Workflows laufen `on_behalf_of` dir). Die Deployment-Parameter stehen in [OPERATIONS.de.md](OPERATIONS.de.md).
2. **Secrets**: Workspace-Secret `scan_inbox_folder` (als Umgebungsvariable `SCAN_INBOX_FOLDER` gebunden) = Standard-Scanner-Eingang für den Triage-Workflow; Workspace-Secret `file_connector_names` (als `FILE_CONNECTOR_NAMES` gebunden, kommagetrennt, Default `nextcloud`) = deine Datei-Connector(s).
3. **Workflow aufrufen** in Studio/Chat oder per MCP `execute_workflow`. Beispiel-Start-Inputs:

```json
{"folder_path": "/<Scanner-Eingang>", "apply_changes": false}
```

```json
{"control_file": "/<Scanner-Eingang>/steuerdatei.json", "apply_changes": true, "model": "mistral-small-latest"}
```

```json
{"target_path": "/Ordner/Datei.pdf", "recursive": false, "force": false, "apply_changes": true, "languages": "deu+eng"}
```

```json
{"target_path": "/Ordner/Dokument.pdf", "split_at": [2, 4], "apply_changes": true}
```

- `apply_changes: false` ist immer ein **Dry-Run**: alles wird gelesen, OCRed und geplant, das Ergebnis zeigt, was passieren würde — geschrieben wird nichts, auch keine Status-Rückmeldungen in Steuerdateien.
- Jeder Start-Input akzeptiert außerdem `connector` (Name eines der konfigurierten Datei-Connector, leer = Default) und `model` (LLM für Benennung/Planung, Default `mistral-small-latest`).
- Vollständige Referenz zu Inputs, Ergebnis-Shapes und Beispielen: [WORKFLOWS.de.md](WORKFLOWS.de.md).

## Konventionen

- Dateien heißen `YYYY-MM-DD kurze, beschreibende Bezeichnung.pdf` (ASCII, Umlaute transliteriert); Dateien werden nie überschrieben (Kollisionen bekommen `_2`, `_3`, ...).
- Ordnerspezifische Benennungsregeln (Datumsschema, Ausnahmen, Qualifizierer) liegen in einer `AGENTS.md`-Datei im jeweiligen Ordner und gehen mit Priorität vor dem Basis-Prompt an das LLM (max. 4000 Zeichen).
- Das LLM benennt, verschiebt und fragt in der Sprache des Dokuments (deutsches Dokument → `Vorderseite`/`Rueckseite`, deutsche Titel, sonst `Front`/`Back`).
- **Originale werden immer gesichert** unter `<Ordner>/.originals/<Workflow>/` — in allen vier Workflows standardmäßig an (Split und Merge legen pro Datei/Ziel einen Unterordner an). Die Workflows löschen nie etwas; `.originals/` räumst du nach der Prüfung von Hand auf.
- OCR-, Download- und Upload-Aktivitäten senden Heartbeats: ein abgestürzter Worker wird binnen ~2 Minuten erkannt und die Aktivität automatisch wiederholt.

## Datenschutz

Alle Dateioperationen laufen über den Datei-Connector deines Workspace **in deinem Namen** (`on_behalf_of=True`, daher das gehärtete Deployment). Dokumente werden nur in deinem eigenen Speicher gelesen und geschrieben; die einzigen externen Aufrufe sind die Mistral-API-Calls für OCR-Textextraktion und LLM-Benennung. Secrets (`SCAN_INBOX_FOLDER`, `FILE_CONNECTOR_NAMES`) liegen als Workspace-Secrets und werden als Umgebungsvariablen gebunden — nie im Repository oder im Image.

## Lizenz

GNU Affero General Public License v3.0 only (AGPL-3.0-only), siehe [LICENSE](LICENSE). Die §13 der AGPL (Netzwerknutzung) greift: Wer diese Software als Netzwerkdienst anbietet, muss den entsprechenden Quelltext den Netzwerknutzern verfügbar machen.

## Für Entwickler

Du willst die Workflows weiterentwickeln oder an diesem Repo arbeiten? Die Einstiegspunkte sind [CONTRIBUTING.md](CONTRIBUTING.md) (lokales Setup, Tests, CI-Gates), [AGENTS.md](AGENTS.md) (die verbindlichen Arbeitsregeln) und [ARCHITECTURE.de.md](ARCHITECTURE.de.md) (Konventionen und Arbeitsteilung). Deployment und laufender Betrieb: [OPERATIONS.de.md](OPERATIONS.de.md). Änderungshistorie: [CHANGELOG.de.md](CHANGELOG.de.md).
