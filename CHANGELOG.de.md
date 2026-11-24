> Sprache: Deutsch · [English version](CHANGELOG.md)

# Changelog

## 0.2.0 (2026-11): Zweistufige Architektur — Step-Workflows + Orchestrator

Die Workflows sind jetzt ein zweistufiges System: drei spezialisierte Step-Workflows plus ein Orchestrator, der den kompletten Scan-Inbox-Prozess von vorne bis hinten fährt.

- `pdf-scanner-processor` (neu, ersetzt `pdf-scan-inbox-triage`) — Orchestrator über einen Scanner-Eingangsordner: parkt die Roh-Scans unter `.originals/pdf-scanner-processor/`, führt OCR aus, erkennt Vorder-/Rückseiten-Paare (struktureller Vorfilter + LLM `front-back-check`, namensunabhängig), mischt jedes Paar via `pdf-alternating-merge` zu `ScanMerged<N>.pdf`, benennt die Ergebnisse per LLM als `YYYY-MM-DD Titel.pdf` und führt zuletzt `pdf-split` pro Datei aus (nur bei sicherer Erkennung). Die Steps laufen als Child-Workflows mit deterministischen Ausführungs-IDs; `apply_changes: false` bleibt ein kompletter Dry-Run.
- `pdf-ocr`, `pdf-split`, `pdf-alternating-merge` (umbenannt von `pdf-merge-duplex`) — liegen jetzt in `workflows/steps/`; jeder macht genau eine Sache und ist einzeln sowie vom Orchestrator aus triggerbar.
- Das Wort „manuell“ ist aus allen Studio-sichtbaren Workflow-Titeln/-Beschreibungen entfernt; die Konvention zum manuellen Start bleibt in den Repo-Docs dokumentiert.
- Hinweis für bestehende Deployments: nach einem Redeploy verschwinden die Studio-Einträge `pdf-scan-inbox-triage` und `pdf-merge-duplex`; die Nachfolger heißen `pdf-scanner-processor` und `pdf-alternating-merge`.

Die Produkt-Pakete (`workflows-app`, `cli`, `env`, `utils`) stehen jetzt auf Version 0.2.0.

## 2026-11: Initiales Release

Erstes öffentliches Release von `mistralai-workflows` (Lizenz: AGPL-3.0-only, siehe [LICENSE](LICENSE)). Mistral-Workflows für Scanner- und PDF-Automatisierung auf Nextcloud — ein Managed Deployment, vier manuell gestartete Workflows (`on_behalf_of`):

- `pdf-scan-inbox-triage` — OCR + LLM-Benennung neuer Scans in einem Scanner-Eingangsordner; Duplex-Paare werden erkannt und mit Vorder-/Rückseiten-Suffix in der Dokumentsprache benannt, aber nicht gemischt; jeder Scan wird vor der Änderung unter `.originals/pdf-scan-inbox-triage/` geparkt.
- `pdf-merge-duplex` — mischt Duplex-Paare (Vorder-/Rückseiten alternierend, Rückseiten umgekehrt) per JSON-Steuerdatei oder direktem `pairs`-Input; Quelldateien werden unter `.originals/pdf-merge-duplex/` gesichert.
- `pdf-ocr` — generischer PDF-OCR-Prozessor für eine einzelne PDF oder einen Ordner (optional rekursiv); `keep_original` (Standard an) sichert das Original unter `.originals/pdf-ocr/`.
- `pdf-split` — teilt eine PDF an angegebenen Seitenzahlen oder LLM-geplanten Trennstellen (Rückfrage bei Unsicherheit); das Original wird unter `.originals/pdf-split/` gesichert.

Highlights:

- Dry-Run für jeden Workflow per `apply_changes: false`: alles wird gelesen, OCRed und geplant, das Ergebnis zeigt, was passieren würde — geschrieben wird nichts.
- Originale werden immer unter `<Ordner>/.originals/<Workflow>/` gesichert (in allen vier Workflows standardmäßig an, nie überschrieben); die Workflows löschen nie — nach der Prüfung von Hand aufräumen.
- Ordnerspezifische Benennungsregeln per `AGENTS.md` im Ordner, Datei-Connector pro Aufruf wählbar (`connector`-Start-Input), und das LLM benennt und antwortet in der Dokumentsprache.
- Heartbeats bei OCR/Download/Upload erkennen einen abgestürzten Worker binnen ~2 Minuten und wiederholen die Aktivität automatisch.
- AGPL §13 (Netzwerknutzung): wird diese Software als Netzwerkdienst angeboten, ist der Quellcode in diesem Repository verfügbar.
- Unter der Haube: Worker-Image auf Python 3.15, ein blockierendes CI-Gate pro PR, Dependabot mit wöchentlichen Updates und Auto-Merge-Queue (menschliche Freigabe + grüne CI sperren den Merge weiterhin).

Die Git-Historie wurde bei der Neuerstellung des Repositories für die Veröffentlichung auf einen einzigen Commit squashed; frühere PR-Referenzen lösen nicht mehr auf.
