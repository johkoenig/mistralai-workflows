> Sprache: Deutsch · [English version](OPERATIONS.md)

# Betrieb (Operations)

Deployment und Betriebsregeln für `mistralai-workflows`. Bedienung der Workflows: [README.md](README.de.md) und [WORKFLOWS.md](WORKFLOWS.de.md); Architektur: [ARCHITECTURE.md](ARCHITECTURE.de.md).

## Deployment

Ein Repository, ein Managed Deployment. Konkret gerundete Parameter des Deployments `mistralai-workflows`:

| Parameter | Wert |
|---|---|
| Deployment-Name | `mistralai-workflows` |
| GitHub-Repository | `https://github.com/johkoenig/mistralai-workflows` |
| Build-Context | `.` |
| Dockerfile | `deploy/docker/Dockerfile.worker` |
| Hardened | `true` (Pflicht, weil alle Workflows `on_behalf_of=True` nutzen) |
| Secrets | `scan_inbox_folder` gebunden als Umgebungsvariable `SCAN_INBOX_FOLDER`; optionale Umgebungsvariablen `MAX_CONCURRENT_OCR` (parallele OCR-/Scan-Aktivitäten, Default `2` — nur auf stärkerer Hardware auf 3-4 erhöhen, das ist der OOM-Schutz), `OCR_QUALITY_MIN_CONFIDENCE` (low_confidence-Schwelle der OCR-Quality-Heuristik in den pdf-ocr-Ergebnissen, Default `0.6`); `file_connector_names` gebunden als `FILE_CONNECTOR_NAMES` (Komma-Liste der Connector-Namen, z. B. `<Dein-Connector-Name>`; Default `nextcloud`) |

Das Worker-Image installiert systemseitig `ghostscript`, `tesseract-ocr-all` und `jbig2dec` (Paketliste: `deploy/docker/worker-packages/workflows.txt`).

Erkennung nach jedem Start: der Worker registriert automatisch alle Workflow-Module aus `apps/worker/src/worker/workflows/` (rekursiv, inklusive `workflows/steps/`) per `discover_all_workflows_in_package`. Im Normalbetrieb müssen die vier Workflows `pdf-scanner-processor`, `pdf-ocr`, `pdf-alternating-merge`, `pdf-split` und alle Activities aus der [WORKFLOWS.md](WORKFLOWS.de.md) in den Logs auftauchen, ohne OOM- oder Importfehler.

## Qualitaets-Gates (CI)

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) ist das einzige Gate. Es läuft auf jeden Pull-Request und jeden Push auf `main`, und jede Prüfung ist **blockierend**: `uv sync --all-packages --frozen`, ruff lint, ruff format, semgrep (Fähigkeits-Regeln für Workflows), striktes mypy, die Test-Suiten worker/cli/utils sowie eine Coverage-Untergrenze von **55 %** für den Worker (`--cov-fail-under=55`; aktuelles Niveau 58 %, bewusst nach oben zu schrauben). Tool-Versionen sind im Workflow gepinnt (ruff 0.17.6, mypy 2.4.1, semgrep 1.176.0), damit Läufe reproduzierbar sind. Das Ergebnis jeder Stufe landet als genau ein PR-Kommentar (Marker `<!--ci-quality-report-->`) und auf main in der Job-Summary.

## Update-Prozess

1. Branch vom `main` anlegen, Änderungen pushen (Code samt Tests, Doku mitpflegen).
2. Pull-Request erstellen; das CI-Gate muss vor dem Merge grün sein - auch bei selbst-approved PRs (AGENTS.md). Nach `main` mergen.
3. Deployment neu starten mit dem Merge-Commit-SHA (Parameter wie oben).
4. Auf Bereitschaft warten, dann Worker-Logs prüfen: alle Activities registriert, keine Fehler.
5. Verifikationslauf: empfohlen ein Dry-Run (`apply_changes: false`) auf einem echten Dokument; danach erst ein echter Lauf.

## Logs

- Runtime-Logs im Studio (Deployment → Logs) bzw. per MCP `get_workflow_deployment_logs`.
- `worker/ocr.py` protokolliert Start, Ende, Dauer und Fehler jedes OCR-Laufs; Fortschritt langer Läufe ist dort sichtbar.
- Heartbeats (alle 30 s bei OCR/Download/Upload) erkennen Worker-Abstürze binnen 2 Minuten und lösen Activity-Wiederholungen aus (je Activity 2 Versuche).

## Konventionen im Betrieb

- **Dry-Run zuerst**: neue oder geänderte Workflows immer zuerst mit `apply_changes: false` gegen ein echtes Dokument prüfen.
- **Manuelle Starts nur**: kein Zeitplan, kein Webhook; Begründung in ARCHITECTURE.md. Sperrige Verarbeitung erst nach vollständigem Scanvorgang.
- **Originale bleiben**: jedes Workflow sichert Originale standardmäßig unter `<Ordner>/.originals/<Workflow>/` (alternierendes Mischen: Unterordner `<Zielname>/`; Split, OCR und pdf-scanner-processor: die Datei selbst). Aufzuräumen ist von Hand, wenn die Ergebnisse geprüft sind; die Workflows löschen nie.

## Benennungsregeln (AGENTS.md)

Ordnerspezifische Benennungsregeln liegen als `AGENTS.md` im jeweiligen Ordner oder einem Elternordner — die nächstliegende gewinnt (z. B. eine AGENTS.md im Scanner-Eingang oder darüber). Jeder AI-benannte Workflow (`pdf-scanner-processor`, `pdf-alternating-merge` ohne expliziten `title`, `pdf-split`) lädt sie über die Activity `load-naming-rules` (max. 4000 Zeichen) und gibt sie dem Modell mit Vorrang vor dem Basis-Prompt. Ohne AGENTS.md wählt das LLM selbst passende Namen. Das Projekt definiert kein eigenes Namensschema, und AGENTS.md-Inhalt ist privat — er wird weder in diesem Repository noch in dessen Doku gespiegelt. Änderungen an der AGENTS.md wirken ohne Redeploy ab dem nächsten Lauf.

## Bekannte Einschränkungen

- **Kein Verschieben zwischen Team-Speichern**: Der Nextcloud-Connector unterstützt `files_move` nur innerhalb eines Dateibereichs. Dateien, die aus dem Scanner-Eingang (`<Scanner-Eingang>`) in einen anderen Bereich (`<Zielordner>`) einsortiert werden sollen, werden per `files_copy` übernommen; die Originale im Scanner-Eingang sind danach von Hand zu löschen. Ein späterer Arbeitsschritt (eine Erweiterung von pdf-scanner-processor) kann das Einsortieren im Worker direkt übernehmen.
- **Kein Löschen über den Connector**: `files_delete` existiert nicht. Was endgültig entfernt werden muss (dublette Teile, verbrauchte Originale), räumt der Nutzer von Hand.
- **OCR-Limits**: `ocrmypdf -j 1` je Datei und `MAX_CONCURRENT_OCR = 2` parallel (Speicherschutz der Managed-Instanz); pro pdf-ocr-Lauf maximal 200 Dateien. Große Konvolute besser lokal verarbeiten.
- **OCR bei handschriftlichen Inhalten** (z. B. handschriftliche Nummern auf Belegen) ist unzuverlässig; ordnerspezifische AGENTS.md-Regeln können solche Fälle abfangen.
