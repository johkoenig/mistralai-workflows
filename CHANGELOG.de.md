> Sprache: Deutsch · [English version](CHANGELOG.md)

# Changelog

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
