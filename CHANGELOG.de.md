> Sprache: Deutsch · [English version](CHANGELOG.md)

# Changelog

Alle wesentlichen Änderungen an diesem Projekt sind in dieser Datei dokumentiert.

Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), und das Projekt hält sich an [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Lizenz: AGPL-3.0-only, siehe [LICENSE](LICENSE).

Einträge sind Einzeiler; der Pull-Request ist am Ende in Klammern manuell verlinkt. `Unreleased` trägt keine Link-Definition (die müsste bei jedem Push aktualisiert werden); freigegebene Versionen verlinken über die Definitionen am Dateiende auf Tag oder PR.

## Unreleased

### Added

### Changed

- Release- und Changelog-Prozess ist in `AGENTS.md` kodifiziert: Inhaltliche PRs, sechs Standardkategorien, dedizierter Release-PR, „Bump to Next Version“-Commit, annotierte Tags auf `main`, kein Dev-Suffix. ([#37](https://github.com/johkoenig/mistralai-workflows/pull/37))
- Changelog-Einträge sind Einzeiler mit in Klammern gesetztem PR-Link; bestehende Einträge entsprechend überarbeitet. ([#37](https://github.com/johkoenig/mistralai-workflows/pull/37))

### Deprecated

### Removed

### Fixed

### Security

## [0.2.1] - 2026-11-24

### Changed

- `pdf-scanner-processor` führt `pdf-split` vor der Benennung aus; nur nicht gesplittete Einheiten werden umbenannt. ([#27](https://github.com/johkoenig/mistralai-workflows/pull/27))
- `pdf-split` parkt das Original direkt unter `.originals/pdf-split/<Name>`. ([#27](https://github.com/johkoenig/mistralai-workflows/pull/27))
- Changelogs folgen Keep a Changelog mit versionierten Abschnitten und Link-Definitionen. ([#25](https://github.com/johkoenig/mistralai-workflows/pull/25))
- Produkt-Pakete (`workflows-app`, `cli`, `env`, `utils`) auf 0.2.1 angehoben. ([#27](https://github.com/johkoenig/mistralai-workflows/pull/27))

### Fixed

- Der `pdf-split`-Planungs-Prompt enthält explizite Dokumentgrenzen-Heuristiken (Datum, laufende Seitenzahl, Briefkopf; Fremdleistungs-Namen ausgenommen). ([#28](https://github.com/johkoenig/mistralai-workflows/pull/28))

## [0.2.0] - 2026-11-24

Zweistufige Architektur: drei spezialisierte Step-Workflows plus ein Orchestrator für den kompletten Scan-Inbox-Prozess.

### Added

- `pdf-scanner-processor`-Orchestrator: Backups, OCR, Vorder-/Rückseitenerkennung, Mischung, Benennung, Split; Child-Workflows mit deterministischen IDs; voller Dry-Run. ([#24](https://github.com/johkoenig/mistralai-workflows/pull/24))
- Step-Workflows unter `workflows/steps/`: `pdf-ocr`, `pdf-split`, `pdf-alternating-merge`, je genau eine Aufgabe und einzeln triggerbar. ([#24](https://github.com/johkoenig/mistralai-workflows/pull/24))
- LLM-Activity `front-back-check`: namensunabhängige Inhaltsprüfung für Vorder-/Rückseiten-Kandidaten. ([#24](https://github.com/johkoenig/mistralai-workflows/pull/24))
- `load-naming-rules` liest die nächstliegende `AGENTS.md`; ohne eine wählt das LLM selbst einen passenden Namen. ([#24](https://github.com/johkoenig/mistralai-workflows/pull/24))
- Einheitliche Original-Backups unter `.originals/<Workflow>`. ([#23](https://github.com/johkoenig/mistralai-workflows/pull/23))

### Changed

- `pdf-merge-duplex` umbenannt zu `pdf-alternating-merge`. ([#24](https://github.com/johkoenig/mistralai-workflows/pull/24))
- Benennungskonventionen aus dem Projekt herausgezogen; AGENTS.md-Regeln haben Vorrang. ([#24](https://github.com/johkoenig/mistralai-workflows/pull/24))
- Studio-sichtbare Titel und Beschreibungen enthalten kein „manuell“ mehr. ([#24](https://github.com/johkoenig/mistralai-workflows/pull/24))
- README überarbeitet: Orchestrator/Steps-Trennung, Optionen-Tabelle, Konventionen und AGPL-§13-Lizenzhinweis. ([#24](https://github.com/johkoenig/mistralai-workflows/pull/24))
- Produkt-Pakete auf 0.2.0 angehoben. ([#24](https://github.com/johkoenig/mistralai-workflows/pull/24))

### Removed

- `pdf-scan-inbox-triage`, ersetzt durch `pdf-scanner-processor` (alte Studio-Einträge verschwinden beim Redeploy). ([#24](https://github.com/johkoenig/mistralai-workflows/pull/24))
- Lexikalische Textähnlichkeits-Paarerkennung und Vorder-/Rückseiten-Suffixe. ([#24](https://github.com/johkoenig/mistralai-workflows/pull/24))

## [0.1.0] - 2026-11-22

Erstes öffentliches Release von `mistralai-workflows` — Mistral-Workflows für Scanner- und PDF-Automatisierung auf Nextcloud: `pdf-scan-inbox-triage`, `pdf-merge-duplex`, `pdf-ocr`, `pdf-split` in einem Managed Deployment.

### Added

- Voller Dry-Run für jeden Workflow per `apply_changes: false`.
- Originale immer unter `.originals/<Workflow>/` gesichert, nie überschrieben; die Workflows löschen nie.
- Ordnerspezifische Benennung per `AGENTS.md`, wählbarer Datei-Connector, LLM antwortet in der Dokumentsprache.
- Heartbeats erkennen einen abgestürzten Worker binnen ~2 Minuten und wiederholen automatisch.
- Worker-Image auf Python 3.15, ein blockierendes CI-Gate pro PR, Dependabot mit Auto-Merge-Queue.
- AGPL §13: Der Quellcode ist für Netzwerknutzung in diesem Repository verfügbar.

Die Git-Historie wurde bei der Neuerstellung des Repositories für die Veröffentlichung auf einen Commit squasht; frühere PR-Referenzen lösen nicht mehr auf.

[0.2.1]: https://github.com/johkoenig/mistralai-workflows/pull/27
[0.2.0]: https://github.com/johkoenig/mistralai-workflows/compare/a2db23a...d2e3984
[0.1.0]: https://github.com/johkoenig/mistralai-workflows/tree/a2db23a
