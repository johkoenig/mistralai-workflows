> Sprache: Deutsch · [English version](README.md)

# mistralai-workflows

[![CI](https://img.shields.io/github/actions/workflow/status/johkoenig/mistralai-workflows/ci.yml?branch=main&label=CI)](https://github.com/johkoenig/mistralai-workflows/actions/workflows/ci.yml)

Mistral-Workflows fuer Scanner- und PDF-Automatisierung auf dem eigenen Datei-Speicher (Nextcloud). Ein Repository (`mistralai-workflows`), ein Managed Deployment (`mistralai-workflows`), vier Workflows, die auf den Dokumenten **im eigenen Speicher** laufen — nichts verlaesst die Nextcloud, ausser den OCR-/Benennungs-Prompts an die Mistral-API.

| Workflow | Was er tut |
|---|---|
| `pdf-scan-inbox-triage` | OCR und LLM-Benennung neuer Scans in einem Scanner-Eingangsordner; erkennt Duplex-Paare und benennt sie mit Vorder-/Rueckseiten-Suffix (mischt aber nicht) |
| `pdf-merge-duplex` | Mischt Duplex-Paare (Vorder-/Rueckseiten alternierend) per JSON-Steuerdatei oder direktem `pairs`-Start-Input; archiviert die Quellen unter `merged/` |
| `pdf-split` | Teilt eine PDF in Teile — an angegebenen Seitenzahlen oder per LLM geplant (Rueckfrage bei Unsicherheit); Original wird unter `.originals/pdf-split/` gesichert |
| `pdf-ocr` | Generischer PDF-OCR-Prozessor fuer eine einzelne PDF oder einen Ordner (optional rekursiv); `keep_original` bewahrt das Original als `<Name>_orig.pdf` |

Alle Workflows werden ausschliesslich manuell gestartet (Studio, Chat oder MCP `execute_workflow`) — nie zeit- oder hook-gesteuert, damit ein Lauf nie einen unvollstaendigen Scan-Job auseinanderreisst.

Die vollstaendige Workflow-Referenz (Inputs, Ergebnis-Shapes, Aktions-Glossar, Beispiele) steht in [WORKFLOWS.de.md](WORKFLOWS.de.md); Deployment und Betrieb in [OPERATIONS.de.md](OPERATIONS.de.md); Architektur und Konventionen in [ARCHITECTURE.de.md](ARCHITECTURE.de.md); Aenderungshistorie in [CHANGELOG.de.md](CHANGELOG.de.md).

## Voraussetzungen

- Ein Datei-Connector (z. B. Nextcloud), im Mistral-Workspace registriert, mit gueltigem Credential. Die Connector-Namen liest der Worker aus der Umgebungsvariable `FILE_CONNECTOR_NAMES` (Komma-Liste, Default `nextcloud`); das Deployment bindet das Workspace-Secret `file_connector_names` als diese Variable. Pro Workflow-Aufruf waehlt das optionale Start-Input-Feld `connector` den Ziel-Connector — bei mehreren aktiven Connectoren zielt ein Aufruf auf einen beliebigen davon.
- Das Workspace-Secret `scan_inbox_folder` (gebunden als `SCAN_INBOX_FOLDER`): Default-Scanner-Eingang fuer `pdf-scan-inbox-triage`; ein beim Start angegebenes `folder_path` ueberschreibt ihn.
- Die Mistral-GitHub-App auf diesem Repository (fuer das Managed Deployment).
- Das Worker-Image installiert systemseitig `ghostscript`, `tesseract-ocr-all`, `jbig2dec` (siehe `deploy/docker/worker-packages/workflows.txt`).

## Start eines Workflows

- **Studio**: Workflows → Start, oder aus einem Chat / per MCP `execute_workflow` — alle vier Workflows unterstuetzen Chat-Starts (`on_behalf_of`).
- **Beispiel-Inputs**:

```json
{"folder_path": "/<Scanner-Eingang>", "apply_changes": true, "model": "mistral-small-latest", "connector": ""}
```

```json
{"control_file": "/<Scanner-Eingang>/steuerdatei.json", "apply_changes": true, "model": "mistral-small-latest", "connector": ""}
```

```json
{"target_path": "/Ordner/ oder/datei.pdf", "recursive": false, "force": false, "keep_original": false, "apply_changes": true, "connector": ""}
```

```json
{"target_path": "/<Scanner-Eingang>/Dokument.pdf", "split_at": [2, 4], "apply_changes": true, "connector": ""}
```

- `apply_changes: false` ist immer ein **Dry-Run**: alles wird gelesen, OCRed und geplant, das Ergebnis meldet, was passieren wuerde (`planned`-Aktionen) — aber es wird nichts geschrieben, auch kein Status in Steuerdateien.
- `model` waehlt das LLM fuer Benennung/Planung (Default `mistral-small-latest`).

## Ordner- und Datei-Konventionen

- KI-benannte Dateien folgen dem Schema `YYYY-MM-DD kurze, beschreibende Bezeichnung.pdf` (ASCII, Umlaute transliteriert); Kollisionen erhalten `_2`, `_3`, ... Dateien werden nie ueberschrieben.
- **Benennungsregeln pro Ordner**: eine `AGENTS.md`-Datei im jeweiligen Ordner (z. B. dem Scanner-Eingang) wird vor jedem KI-Benennungsschritt gelesen und dem Modell mit Vorrang vor dem Basis-Prompt uebergeben (max. 4000 Zeichen). Dort stehen das Datumsschema, Ausnahmen und Qualifikatoren.
- **Sprache**: das LLM titelt, benennt und fragt in der Sprache des Dokuments (deutsches Dokument → `Vorderseite`/`Rueckseite`, sonst `Front`/`Back`, und deutsche Titel).
- **Originale bleiben immer erhalten**: Split → `.originals/pdf-split/<Stamm>/`, Duplex-Mischung → `merged/<Zielname>/` (mit `FrontPages.pdf`/`BackPages_reversed.pdf`), OCR mit `keep_original` → `<Name>_orig.pdf`. Die Workflows loeschen nie; nach der Pruefung von Hand aufraeumen.

## Robustheit

- OCR, Download und Upload senden alle 30 s Aktivitaets-Heartbeats (`heartbeat_timeout` 120 s, je Aktivitaet 2 Versuche): Stirbt der Worker mitten im Lauf (Rollout, Crash), wird das binnen ~2 Minuten erkannt und die Aktivitaet automatisch neu versucht, statt bis zum 45-Minuten-`start_to_close_timeout` stumm zu warten.
- Jeder OCR-Lauf wird in den Runtime-Logs des Deployments protokolliert (Start, Ende, Dauer, Fehler).
- Dry-Runs fuehren die OCR vollstaendig aus und melden deshalb echte Fehler; zurueckgeschrieben wird nichts.

## Datenschutz

- Alle Dateioperationen laufen ueber den Datei-Connector des Workspace **im Auftrag von dir** (`on_behalf_of=True`); das Deployment laeuft deshalb gehaertet (`hardened=true`).
- Dokumente werden nur im eigenen Speicher ueber den Connector gelesen und geschrieben. Die einzigen externen Aufrufe sind die Mistral-API-Aufrufe fuer OCR-Textextraktion und LLM-Benennung/-Planung.
- Secrets (`SCAN_INBOX_FOLDER`, `FILE_CONNECTOR_NAMES`) liegen als Workspace-Secrets und werden als Umgebungsvariablen gebunden — nie im Repository oder im Image.

## Lizenz

Dieses Projekt steht unter der **GNU Affero General Public License v3.0 (AGPL-3.0-only)** — siehe [LICENSE](LICENSE). Die "Netzwerknutzung"-Klausel der AGPL (§13) greift: Wer diese Software ueber ein Netzwerk als Dienst anbietet, muss den Nutzern des Netzwerks den korrespondierenden Quellcode zur Verfuegung stellen.

## Fuer Entwickler

Repository-Struktur, lokales Setup, Tests und die CI-Quality-Gates stehen in [CONTRIBUTING.md](CONTRIBUTING.md); die verbindlichen Arbeitsregeln (Gates, Typing, Lint, PR-Konventionen) in [AGENTS.md](AGENTS.md).
