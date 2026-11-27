# Roadmap

Geplante Verbesserungen der pdf-scanner-processor-Pipeline und ihrer Einzelschritte.
Status: Entwurf, Stand 2026-11-27. Punkte wandern bei Freigabe in die CHANGELOG.md.

## v0.3.0 - Geschwindigkeit (Punkte A + B)

Ziel: Laufzeit pro Lauf senken durch Weglassen redundanter I/O-Aufrufe und sequenzieller Wartezeiten.

- **A. Text-Metadaten durch die Pipeline reichen** statt neu herunterzuladen:
  - pdf-ocr-Schritt liefert pro Datei: Seitenzahl, has_text-Flag, Textschnipsel
  - analyze-scan-file-Download entfällt für OCR-verarbeitete Dateien
  - Split-Planung inline über die bestehende plan_split-Aktivität;
    pdf-split-Kind-Workflow nur noch bei tatsächlich bestätigtem Split
- **B. Sequenzielle Schleifen im Orchestrator parallelisieren**:
  - name_document-LLM-Aufrufe (aktuell einfache for-Schleife)
  - backup_scan_file-Ablagevorgänge (bestehendes Semaphore-Muster nutzen)

## v0.3.1 - Konfiguration (Punkte C + D + E)

- **C. Namensregeln an Kind-Workflows durchreichen**: optionales rules-Feld
  in Kind-Inputs; Kinder behalten das eigenständige Laden als Fallback
- **D. OCR-Sprachen als Orchestrator-Eingabe** (Default bleibt deu+eng;
  der Scanner-Eingang läuft mit deu)
- **E. MAX_CONCURRENT_OCR als Umgebungsvariable** (aktuell hart codiert 2)

## v0.4.0 - Qualität (Punkte F + G)

- **F. OCR-Qualitätsheuristik** nach dem OCR: alphanumerischer Anteil,
  Wortlängen-Verteilung -> low_confidence-Kennzeichnung im Lauf-Report
  (noch ohne Vision-Aufruf)
- **G. Vision-Fallback als eigener Einzelschritt `pdf-vision`**:
  - neue Aktivitäten assess_ocr_quality und vision_ocr_pass (pdf_ops.py)
  - rendert markierte Seiten (pypdfium2) und liest sie mit einem Vision-Modell
  - Orchestrator-Schritt 2.5 routet low-confidence-Dateien, begrenzt über
    max_vision_pages
  - Vision-Text fließt nur in Duplex-Erkennung, Split-Planung und Benennung;
    er ersetzt nie die PDF-Textebene
  - Ergebnis als Sidecar <Name>.txt plus Verarbeitungs-Kennzeichen in den
    PDF-Metadaten (Modell, Konfidenz, Datum)
  - Einzeln startbar: target_path (einzelne Datei oder Ordner), pages,
    force, apply_changes (Trockenlauf), model, languages
  - Env-Knobs: Modellname, Vision-Schwellwert, Seitenlimit

## Nicht-Ziele

- Kein Monolith-Refactoring: Kind-Workflow-Architektur, deterministische IDs,
  Replaybarkeit und STEPS-Map-Tests bleiben erhalten
- Keine vision-basierte PDF-Textebene: Chat-Vision liefert Text ohne
  Koordinaten und Orientierung; die durchsuchbare Textebene bleibt
  Tesseraces Aufgabe
- Keine Rotation nach dem Vision-Lauf: Seiten werden bereits im OCR-Durchgang
  gedreht und entzerrt (ocrmypdf --rotate-pages --deskew); Vision-Modelle
  lesen rotierte Bilder nativ
