> Sprache: Deutsch · [English version](README.md)

# mistralai-workflows: PDF-Scan-Verarbeitung als Mistral-Workflows

[![CI](https://img.shields.io/github/actions/workflow/status/johkoenig/mistralai-workflows/ci.yml?branch=main&label=CI)](https://github.com/johkoenig/mistralai-workflows/actions/workflows/ci.yml)

Ein Repository, ein Managed Deployment in deinem Mistral-Workspace, fünf Workflows, die auf deinen Dokumenten **in deinem eigenen Speicher** laufen. Das Deployment hat zwei Ebenen: Ein **Orchestrator** fährt den kompletten Scan-Inbox-Prozess von vorne bis hinten und ruft die vier spezialisierten **Steps** als Child-Workflows auf — jeder Step ist auch einzeln startbar. Nichts verlässt den verbundenen Speicher, außer den OCR-/Benennungs-Prompts an die Mistral-API.

## Workflows

**Orchestrator**

| Workflow | Was er tut |
|---|---|
| `pdf-scanner-processor` | Kompletter Durchlauf über einen Scanner-Eingangsordner: OCR neuer Scans, Vorder-/Rückseiten-Erkennung + alternierendes Mischen (Zwischenname `ScanMerged<N>.pdf`), LLM-Benennung gemäß nächster AGENTS.md, Split pro Datei nur bei sicherer Erkennung |

**Steps** — jeder macht genau eine Sache und ist auch einzeln startbar

| Workflow | Was er tut |
|---|---|
| `pdf-ocr` | OCR für eine einzelne PDF oder einen ganzen Ordner (optional rekursiv) |
| `pdf-alternating-merge` | Mischt Duplex-Paare (Vorder-/Rückseiten alternierend) per JSON-Steuerdatei oder direktem `pairs`-Start-Input |
| `pdf-split` | Teilt eine PDF an angegebenen Seitenzahlen oder per LLM geplanten Trennstellen (Rückfrage bei Unsicherheit) |
| `pdf-vision` | Vision-OCR-Fallback für schwache OCR-Ergebnisse (Sidecar `.txt` + PDF-Metadaten-Flag; Textebene unangetastet) |

Alle fünf werden **ausschließlich manuell** gestartet (Studio, Chat oder MCP `execute_workflow`) — nie per Schedule oder Webhook, damit ein Lauf nie einen unvollständigen Scan-Job auseinanderreißt.

## Schnellstart

1. **Deployment**: Lege ein Managed-Workflow-Deployment namens `mistralai-workflows` aus diesem Repository an — Dockerfile `deploy/docker/Dockerfile.worker`, Build-Kontext `.`, `hardened=true` (die Workflows laufen `on_behalf_of` dir). Die Deployment-Parameter stehen in [OPERATIONS.de.md](OPERATIONS.de.md).
2. **Secrets**: Workspace-Secret `scan_inbox_folder` (als Umgebungsvariable `SCAN_INBOX_FOLDER` gebunden) = Standard-Scanner-Eingang für den Workflow `pdf-scanner-processor`; Workspace-Secret `file_connector_names` (als `FILE_CONNECTOR_NAMES` gebunden, kommagetrennt, Default `nextcloud`) = deine Datei-Connector(s).
3. **Workflow aufrufen** in Studio/Chat oder per MCP `execute_workflow`. Die wichtigsten Start-Optionen im Überblick (vollständige Referenz mit JSON-Beispielen und Ergebnis-Shapes: [WORKFLOWS.de.md](WORKFLOWS.de.md)):

| Option | Bedeutung | Default |
|---|---|---|
| `folder_path` | Scanner-Eingangsordner (`pdf-scanner-processor`) | Wert des Secrets `SCAN_INBOX_FOLDER` |
| `target_path` | PDF-Datei oder Ordner (`pdf-ocr`, `pdf-split`) | erforderlich |
| `pairs` / `control_file` | Duplex-Paare für `pdf-alternating-merge`: Inline-Liste oder Pfad einer JSON-Steuerdatei | eines von beiden erforderlich |
| `split_at` | Explizite Trennstellen für `pdf-split`: Seitenzahlen (1-basiert), nach denen geschnitten wird | LLM-geplante Trennstellen |
| `merge_front_back` | Orchestrator: Vorder-/Rückseiten-Paare erkennen und mischen | `true` |
| `split_parts` | Orchestrator: Split-Step vor der Benennung auf jede Einheit anwenden | `true` |
| `ocr_languages` | Orchestrator: OCR-Sprachen für den pdf-ocr-Child (Scanner-Eingang: `deu`) | `deu+eng` |
| `vision_fallback` / `vision_model` | Orchestrator: low-confidence-OCR-Ergebnisse durch `pdf-vision` routen; Modell-Override (`PDF_VISION_MODEL`-Env-Default) | `true` / `""` |
| `apply_changes` | `false` = Dry-Run: alles wird gelesen, OCRed und geplant, geschrieben wird nichts | `true` |
| `model` | LLM für Benennung, Planung und den Vorder-/Rückseiten-Check | `mistral-small-latest` |
| `connector` | Datei-Connector aus `FILE_CONNECTOR_NAMES`, den dieser Aufruf ansprechen soll | Default-Connector |

- `apply_changes: false` ist immer ein **Dry-Run**: alles wird gelesen, OCRed und geplant, das Ergebnis zeigt, was passieren würde — geschrieben wird nichts, auch keine Status-Rückmeldungen in Steuerdateien.

## Konventionen

Im Projekt eingebaut (Code und Basis-Prompt):

- Das Projekt selbst definiert **kein Dateinamensschema** — Namen wählt das LLM. Das Projekt bereinigt Namen nur technisch (ASCII, Umlaute transliteriert, max. 80 Zeichen) und überschreibt nie Dateien (Kollisionen bekommen `_2`, `_3`, ...). Gemischte Vorder-/Rückseiten-Paare bekommen zuerst den Zwischennamen `ScanMerged<N>.pdf` (N = größere Scan-Nummer des Paares) und werden danach in den endgültigen Namen umbenannt.
- Das LLM benennt und fragt in der Sprache des Dokuments.
- **Originale werden immer gesichert** unter `<Ordner>/.originals/<Workflow>/` — in allen fünf Workflows standardmäßig an (Merge legt pro Ziel einen Unterordner an, die anderen parken die Datei direkt). Die Workflows löschen nie etwas; `.originals/` räumst du nach der Prüfung von Hand auf.
- OCR-, Download- und Upload-Aktivitäten senden Heartbeats: ein abgestürzter Worker wird binnen ~2 Minuten erkannt und die Aktivität automatisch wiederholt.

Benennungskonventionen kommen aus deiner `AGENTS.md` — **nicht Teil dieses Repositories**:

- Dateinamenskonventionen (Schema wie ein Datumpräfix, Ausnahmen, Qualifizierer) liegen in einer `AGENTS.md` im jeweiligen Ordner **oder einem darüberliegenden Ordner** — die nächstliegende gewinnt. Sie geht mit Priorität vor dem Basis-Prompt an das LLM (max. 4000 Zeichen). Existiert nirgendwo in der Kette eine `AGENTS.md`, wählt das LLM selbst einen passenden Namen. Der Inhalt der `AGENTS.md` ist privat und wird weder in diesem Repository noch in dessen Dokumentation gespiegelt.

## Datenschutz

Alle Dateioperationen laufen über den Datei-Connector deines Workspace **in deinem Namen** (`on_behalf_of=True`, daher das gehärtete Deployment). Dokumente werden nur in deinem eigenen Speicher gelesen und geschrieben; die einzigen externen Aufrufe sind die Mistral-API-Calls für OCR-Textextraktion und LLM-Benennung. Secrets (`SCAN_INBOX_FOLDER`, `FILE_CONNECTOR_NAMES`) liegen als Workspace-Secrets und werden als Umgebungsvariablen gebunden — nie im Repository oder im Image.

## Lizenz

GNU Affero General Public License v3.0 only (AGPL-3.0-only), siehe [LICENSE](LICENSE). Die §13 der AGPL (Netzwerknutzung) greift: Wer diese Software als Netzwerkdienst anbietet, muss den entsprechenden Quelltext den Netzwerknutzern verfügbar machen. Ein Link auf dieses Repository genügt dafür: Der vollständige Corresponding Source ist hier öffentlich verfügbar und kostenlos abrufbar.

## Für Entwickler

| Datei | Inhalt |
|---|---|
| [CONTRIBUTING.md](CONTRIBUTING.md) | Lokales Setup, Tests, CI-Gates |
| [AGENTS.md](AGENTS.md) | Die verbindlichen Arbeitsregeln |
| [ARCHITECTURE.de.md](ARCHITECTURE.de.md) | Konventionen und Arbeitsteilung |
| [OPERATIONS.de.md](OPERATIONS.de.md) | Deployment und laufender Betrieb |
| [CHANGELOG.de.md](CHANGELOG.de.md) | Änderungshistorie |
