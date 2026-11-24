> Sprache: Deutsch · [English version](CHANGELOG.md)

# Changelog

Alle wesentlichen Änderungen an diesem Projekt sind in dieser Datei dokumentiert.

Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), und das Projekt hält sich an [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Lizenz: AGPL-3.0-only, siehe [LICENSE](LICENSE).

## [Unreleased]

### Changed

- `CHANGELOG.md` / `CHANGELOG.de.md` folgen jetzt dem Format [Keep a Changelog](https://keepachangelog.com/de/1.1.0/): versionierte Abschnitte mit Unterabschnitten `Added` / `Changed` / `Removed` / `Fixed` und Link-Definitionen am Ende.

## [0.2.0] - 2026-11-24

Zweistufige Architektur: drei spezialisierte Step-Workflows plus ein Orchestrator, der den kompletten Scan-Inbox-Prozess von vorne bis hinten fährt.

### Added

- `pdf-scanner-processor` — Orchestrator über einen Scanner-Eingangsordner: parkt die Roh-Scans unter `.originals/pdf-scanner-processor/`, führt OCR aus, erkennt Vorder-/Rückseiten-Paare (struktureller Vorfilter + LLM-Activity `front-back-check`, namensunabhängig), mischt jedes Paar via `pdf-alternating-merge` zu `ScanMerged<N>.pdf`, benennt die Ergebnisse per LLM und führt zuletzt `pdf-split` pro Datei aus (nur bei sicherer Erkennung). Die Steps laufen als Child-Workflows mit deterministischen Ausführungs-IDs; `apply_changes: false` bleibt ein kompletter Dry-Run.
- Step-Workflows in `apps/worker/src/worker/workflows/steps/`: `pdf-ocr`, `pdf-split`, `pdf-alternating-merge` — jeder macht genau eine Sache und ist einzeln sowie vom Orchestrator aus triggerbar.
- LLM-Activity `front-back-check`: namensunabhängige Inhaltsprüfung für Vorder-/Rückseiten-Kandidaten (Annahme ab confidence >= 0.7).
- `load-naming-rules` liest jetzt die nächstliegende `AGENTS.md` im Ordner oder einem Elternordner; ohne eine solche wählt das LLM selbst einen passenden Namen.

### Changed

- `pdf-merge-duplex` umbenannt zu `pdf-alternating-merge` (Modul, Klasse, Workflow-Name; Studio-Eintrag „Alternating merge PDF duplex“).
- Benennungskonventionen sind nicht mehr Teil des Projekts: Der Basis-Prompt ist generisch, AGENTS.md-Regeln haben Vorrang. AGENTS.md-Inhalt ist privat und wird weder in diesem Repository noch in dessen Doku gespiegelt.
- Das Wort „manuell“ ist aus allen Studio-sichtbaren Workflow-Titeln/-Beschreibungen entfernt; die Konvention zum manuellen Start bleibt in den Repo-Docs dokumentiert.
- README überarbeitet: H1 auf eine Zeile, Orchestrator/Steps-Trennung in der Übersicht, menschenlesbare Optionen-Tabelle statt JSON-Start-Inputs, Konventionen aufgeteilt in „im Projekt eingebaut“ vs. AGENTS.md, Lizenz-Hinweis (ein Link auf dieses Repository genügt der AGPL-§13-Pflicht), Entwickler-Doku als Tabelle.
- Produkt-Pakete (`workflows-app`, `cli`, `env`, `utils`) auf 0.2.0 angehoben.

### Removed

- `pdf-scan-inbox-triage` — ersetzt durch `pdf-scanner-processor`. Hinweis für bestehende Deployments: nach einem Redeploy verschwinden die Studio-Einträge `pdf-scan-inbox-triage` und `pdf-merge-duplex`; die Nachfolger heißen `pdf-scanner-processor` und `pdf-alternating-merge`.
- Lexikalische Textähnlichkeits-Paarerkennung und die Vorder-/Rückseiten-Suffixe des alten Triage-Workflows.

## [0.1.0] - 2026-11-22

Erstes öffentliches Release von `mistralai-workflows`. Mistral-Workflows für Scanner- und PDF-Automatisierung auf Nextcloud — ein Managed Deployment, vier manuell gestartete Workflows (`on_behalf_of`):

### Added

- `pdf-scan-inbox-triage` — OCR + LLM-Benennung neuer Scans in einem Scanner-Eingangsordner; Duplex-Paare werden erkannt und mit Vorder-/Rückseiten-Suffix in der Dokumentsprache benannt, aber nicht gemischt; jeder Scan wird vor der Änderung unter `.originals/pdf-scan-inbox-triage/` geparkt.
- `pdf-merge-duplex` — mischt Duplex-Paare (Vorder-/Rückseiten alternierend, Rückseiten umgekehrt) per JSON-Steuerdatei oder direktem `pairs`-Input; Quelldateien werden unter `.originals/pdf-merge-duplex/` gesichert.
- `pdf-ocr` — generischer PDF-OCR-Prozessor für eine einzelne PDF oder einen Ordner (optional rekursiv); `keep_original` (Standard an) sichert das Original unter `.originals/pdf-ocr/`.
- `pdf-split` — teilt eine PDF an angegebenen Seitenzahlen oder LLM-geplanten Trennstellen (Rückfrage bei Unsicherheit); das Original wird unter `.originals/pdf-split/` gesichert.
- Dry-Run für jeden Workflow per `apply_changes: false`: alles wird gelesen, OCRed und geplant, das Ergebnis zeigt, was passieren würde — geschrieben wird nichts.
- Originale werden immer unter `<Ordner>/.originals/<Workflow>/` gesichert (in allen vier Workflows standardmäßig an, nie überschrieben); die Workflows löschen nie — nach der Prüfung von Hand aufräumen.
- Ordnerspezifische Benennungsregeln per `AGENTS.md` im Ordner, Datei-Connector pro Aufruf wählbar (`connector`-Start-Input), und das LLM benennt und antwortet in der Dokumentsprache.
- Heartbeats bei OCR/Download/Upload erkennen einen abgestürzten Worker binnen ~2 Minuten und wiederholen die Aktivität automatisch.
- AGPL §13 (Netzwerknutzung): wird diese Software als Netzwerkdienst angeboten, ist der Quellcode in diesem Repository verfügbar.
- Unter der Haube: Worker-Image auf Python 3.15, ein blockierendes CI-Gate pro PR, Dependabot mit wöchentlichen Updates und Auto-Merge-Queue (menschliche Freigabe + grüne CI sperren den Merge weiterhin).

Die Git-Historie wurde bei der Neuerstellung des Repositories für die Veröffentlichung auf einen einzigen Commit squashed; frühere PR-Referenzen lösen nicht mehr auf.

[Unreleased]: https://github.com/johkoenig/mistralai-workflows/compare/d2e3984...HEAD
[0.2.0]: https://github.com/johkoenig/mistralai-workflows/compare/a2db23a...d2e3984
[0.1.0]: https://github.com/johkoenig/mistralai-workflows/tree/a2db23a
