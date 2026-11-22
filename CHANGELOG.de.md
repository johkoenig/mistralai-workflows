> Sprache: Deutsch · [English version](CHANGELOG.md)

# Changelog

## 2026-11: Initiales Release

Erstes öffentliches Release von `mistralai-workflows` (Lizenz: AGPL-3.0-only, siehe [LICENSE](LICENSE)). Mistral-Workflows für Scanner- und PDF-Automatisierung auf Nextcloud — ein Managed Deployment, vier manuell gestartete Workflows (`on_behalf_of`):

- `pdf-scan-inbox-triage` — OCR + LLM-Benennung neuer Scans in einem Scanner-Eingangsordner; Duplex-Paare werden erkannt und mit Vorder-/Rückseiten-Suffix in der Dokumentsprache benannt, aber nicht gemischt.
- `pdf-merge-duplex` — mischt Duplex-Paare (Vorder-/Rückseiten alternierend, Rückseiten umgekehrt) per JSON-Steuerdatei oder direktem `pairs`-Input; Quelldateien werden unter `merged/` archiviert.
- `pdf-ocr` — generischer PDF-OCR-Prozessor für eine einzelne PDF oder einen Ordner (optional rekursiv); `keep_original` bewahrt das Original als `<Name>_orig.pdf`.
- `pdf-split` — teilt eine PDF an angegebenen Seitenzahlen oder LLM-geplanten Trennstellen (Rückfrage bei Unsicherheit); das Original wird unter `.originals/pdf-split/` gesichert.

Highlights:

- Der Datei-Connector ist pro Workflow-Aufruf wählbar (`FILE_CONNECTOR_NAMES` Env + `connector`-Start-Input) — keine Connector-ID im Quellcode.
- Englischer Codebase; das LLM benennt und antwortet in der Dokumentsprache (deutsches Dokument → `Vorderseite`/`Rueckseite`, sonst `Front`/`Back`).
- Originale bleiben immer erhalten (`.originals/<Workflow>/`, `merged/`, `<Name>_orig.pdf`); die Workflows überschreiben und löschen nie etwas.
- Dry-Runs über `apply_changes: false`; Heartbeats bei OCR/Download/Upload erkennen einen abgestürzten Worker binnen ~2 Minuten und wiederholen die Aktivität.
- AGPL §13 (Netzwerknutzung): wird diese Software als Netzwerkdienst angeboten, ist der Quellcode in diesem Repository verfügbar.
- Die Dokumentation bezeichnet das Projekt durchgängig als `mistralai-workflows`.
- Bugfix: Die LLM-Aktivitäten `name-document` und `plan-split` senden jetzt `top_p=1` zusammen mit `temperature=0.0`; die Chat-API lehnt Greedy-Sampling mit einem anderen `top_p` ab (HTTP 400 `invalid_request_greedy_sampling`).
- `pdf-split`: Die Cut-After-Semantik von `split_at` steht jetzt in der Feldbeschreibung des Input-Schemas, in der Workflow-Beschreibung und in WORKFLOWS.de.md — `split_at` nennt die Seitenzahlen, NACH denen geschnitten wird; die angegebene Seite bleibt im vorherigen Teil (Beispiel: `[2, 4]` bei 8 Seiten ergibt Teile 1-2, 3-4, 5-8).
- `.github/dependabot.yml` ergänzt (wöchentliche Updates für `uv`, `bun`, `github-actions` und das Worker-`docker`-Image) und `SECURITY.md` mit privatem Vulnerability-Reporting über GitHub Security Advisories.
- Neues CI-Gate `check_docker_base.py`: jedes `FROM python:X.Y` im `deploy/docker/Dockerfile.worker` muss `requires-python` in pyproject.toml erfüllen und zu `.python-version` passen — ein inkompatibles Base-Image-Update schlägt jetzt in der CI fehl, statt erst den Deployment-Build zu zerlegen. Dependabot ignoriert semver-major-Bumps des `python`-Base-Images; ein Python-Major-Upgrade muss Dockerfile + `requires-python` + `.python-version` in einem bewussten PR zusammen anheben.
- Dependabot rebased seine PRs jetzt automatisch (`rebase-strategy: "auto"`), und jeder Dependabot-PR wird bei Erstellung in die Auto-Merge-Queue gelegt (`.github/workflows/dependabot-automerge.yml`): nach menschlicher Freigabe und grüner CI merged er von selbst. Braucht „Allow auto-merge" in den Repo-Settings.

Die Git-Historie wurde bei der Neuerstellung des Repositories für die Veröffentlichung auf einen einzigen Commit squashed; frühere PR-Referenzen lösen nicht mehr auf.
