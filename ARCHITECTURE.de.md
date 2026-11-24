> Sprache: Deutsch · [English version](ARCHITECTURE.md)

# Architektur

Dieses Repository enthaelt die Mistral-Workflows der Scan- und PDF-Automatisierung.
Ein Repository (`mistralai-workflows`), ein Managed Deployment (`mistralai-workflows`), mehrere Workflows.
Details zur Bedienung stehen in der README, die vollstaendige Workflow-Referenz
mit Ergebnis- und Action-Glossar in WORKFLOWS.md, Deployment-Betrieb in
OPERATIONS.md. Hier sind Konventionen und Arbeitsteilung festgehalten.

## Arbeitsteilung

Zwei Ebenen: drei spezialisierte Step-Workflows in `workflows/steps/` und ein
Orchestrator in `workflows/`, der sie als Child-Workflows aufruft.

| Workflow | Aufgabe | Trigger |
|---|---|---|
| pdf-scanner-processor | Orchestrator: OCR, Vorder-/Rueckseiten-Erkennung + Mischen, Benennung, Split ueber den Scanner-Eingang | manuell |
| pdf-ocr | PDFs ohne Textebene aufbereiten (eine Datei, ein Ordner, optional rekursiv) | manuell oder Child von pdf-scanner-processor |
| pdf-alternating-merge | Duplex-Paare alternierend mischen (pairs-Input oder Steuerdatei) | manuell oder Child von pdf-scanner-processor |
| pdf-split | PDF in Teile zerlegen (explizite Trennstellen oder LLM-Planung mit Rueckfrage), Original nach .originals/pdf-split/ | manuell oder Child von pdf-scanner-processor |

## Trigger-Philosophie

Alle Workflows werden ausschliesslich manuell gestartet (Studio oder Chat
bzw. MCP execute_workflow). Es gibt bewusst keinen Zeitplan und keinen
Webhook: Ein automatischer Start mitten im Scanvorgang wuerde unvollstaendige
Duplex-Saetze trennen und Mehrfachausfuehrungen an denselben Dateien
erzeugen. Sperrige Verarbeitung immer erst nach vollstaendigem Scan. Das
einzige "automatische" Ausloesen liegt innerhalb eines Orchestrator-Laufs:
pdf-scanner-processor startet die Steps als Child-Workflows mit
deterministischen Ausfuehrungs-IDs.

## Ablage- und Benennungskonventionen

- Benennungskonventionen sind NICHT Teil des Projekts: die naechstliegende
  AGENTS.md (der Ordner selbst oder ein Elternordner) definiert sie und hat
  Vorrang vor dem Basis-Prompt; ohne AGENTS.md waehlt das LLM selbst einen
  passenden Namen. Das Projekt bereinigt Dateinamen nur technisch (ASCII,
  Umlaute transliteriert, max. 80 Zeichen) und ueberschreibt nie
  (Zaehler-Suffixe).
- Ein gemischtes Vorder-/Rueckseiten-Paar bekommt zunaechst den Zwischennamen
  ScanMerged<N>.pdf (N = groessere Scan-Nummer des Paares); der Orchestrator
  benennt ihn im Benennungsschritt in den endgueltigen Titel um. ScanMerged*.pdf
  passt nicht auf das Scan-Namensmuster, spaetere Laeufe nehmen es also nicht
  als neuen Scan auf.
- Quelldateien gescannter Duplex-Paare werden vom Merge-Step unter
  .originals/pdf-alternating-merge/<Zielname>/ gesichert und heissen dort
  FrontPages.pdf und BackPages_reversed.pdf.
- Unsichere Benennungen (confidence < 0.7 im Orchestrator) bleiben unveraendert
  liegen; der Merge-Step verlangt dann einen expliziten Titel.
- Gescannt wird der Scanner-Eingang (Start-Parameter folder_path; Default aus der Umgebungsvariable SCAN_INBOX_FOLDER, im Deployment als Workspace-Secret gebunden), nie Unterordner.
- pdf-ocr verschiebt das Original mit keep_original (Standard true) vor dem
  Ersetzen nach .originals/pdf-ocr/<Name>. Im Ordner-Modus wird .originals nie
  betreten, Backups werden also nie erneut verarbeitet; existiert ein Backup bereits, bleibt die Datei mit
  Fehlermeldung liegen.
- pdf-ocr verarbeitet nur PDFs (Schreibweise egal), keine Bilder, und pro
  Lauf maximal 200 Dateien (der Rest wird gemeldet).

## Steuerung von pdf-alternating-merge

Es gibt zwei gleichwertige Wege:

- Variante A (direkt): die Paare werden als "pairs"-Liste im Start-Input
  uebergeben (front/back/title je Eintrag). Ergebnisse und Fehler erscheinen im
  Lauf-Ergebnis; es wird keine Datei gegengelesen oder zurueckgeschrieben.
- Variante B (Steuerdatei): JSON-Datei in Nextcloud, z. B.
  /<Scanner-Eingang>/steuerdatei.json, gestartet mit dem Pfad zur Datei:

    {
      "pairs": [
        {
          "front": "/<Scanner-Eingang>/2026-01-15 Beispiel A Vorderseite.pdf",
          "back":  "/<Scanner-Eingang>/2026-01-15 Beispiel A Rueckseite.pdf",
          "title": "2026-01-15 Beispiel A"
        }
      ]
    }

Regeln:

- front/back: absolute Nextcloud-Pfade, beide PDFs, gleiche Seitenzahl.
- title: optional. Ohne Titel benennt das LLM aus dem kombinierten OCR-Text;
  ist das Ergebnis unsicher, wird der Eintrag mit Fehler markiert und der
  Titel muss nachgetragen werden.
- Nach der Bearbeitung schreibt der Workflow je Eintrag zurueck:
  "status": "done" mit "result" (to/archive/pages) oder "status": "error"
  mit "error"-Meldung. Erneute Laeufe verarbeiten nur Eintraege ohne Status.
- apply_changes=false (Dry-Run): plant Ziele, schreibt nichts, auch keinen
  Status.

## Planung von pdf-split

pdf-split kennt zwei AI-Schritte, beide einzeln abschaltbar:

- Trennstellen-Planung (plan_splits, Default true): ohne split_at schlaegt das
  LLM Trennstellen vor; ein angegebenes split_at gewinnt immer. plan_splits
  false ohne split_at ist ein Fehler (nicht: stille keine Trennung).
- Teil-Benennung (name_parts, Default true): das LLM liefert Teil-Titel
  (im AI-Modus im selben plan-split-Aufruf wie die Trennstellen, im manuellen
  Modus als separater Aufruf gegen feste Trennstellen); false => Fallback
  <Stamm> Teil-N. Explizite titles schalten die AI-Benennung ab.
- Ordnerspezifische Benennungsregeln gelten fuer jede AI-Benennung: die
  gemeinsame load-naming-rules-Activity in worker/pdf_ops.py liest die
  naechstliegende AGENTS.md (<Ordner> oder ein Elternordner; max. 4000
  Zeichen, fehlt sie: leer) und haengt sie im Prompt an (hat Vorrang vor
  dem Basis-Prompt). pdf-split nutzt den Ordner der
  Ziel-PDF, pdf-scanner-processor laedt die Regeln einmal pro Lauf aus dem
  Eingangsordner, pdf-alternating-merge ohne expliziten title aus dem Ordner
  der Vorderseite (name_document nimmt naming_rules auf).
- AI-benannte Teile heissen <Titel>.pdf ohne Praefix des Original-Stamms
  (part_filename); Kontext bleibt ueber das Original in .originals/pdf-split/.
- Ungueltige Vorschlaege werden als Rueckfrage gemeldet (parse_split_plan
  validiert gegen part_ranges), nie blind angewendet. Unsicher
  (confidence < 0.7 oder needs_input) => action "clarification" mit Frage und
  Vorschlag, ohne Schreibvorgang. Alles ein Dokument => action "no_split".
- Struktur: load-split-pages laedt und liest, der Workflow-Entrypoint plant
  und entscheidet (resolve_plan, reine Logik, testbar), split-file schreibt.

## Code-Konventionen

- Ein Workflow = ein Modul in apps/worker/src/worker/workflows/ (Orchestrator)
  oder apps/worker/src/worker/workflows/steps/ (Step-Workflows), plus Tests in
  apps/worker/tests/ (Dateinamen spiegeln den Workflow-Namen). Der Worker
  entdeckt das Package rekursiv (discover_all_workflows_in_package), das
  Steps-Unterpackage braucht also keine Entrypoint-Aenderung.
- Gemeinsame Logik (Benennungsregeln, Duplex-Interleaving, Vorder-/Rueckseiten-
  Erkennung, Scan-Namen-Behandlung, Nextcloud-Transfer, die Activities
  name-document, front-back-check und load-naming-rules) liegt in
  worker/pdf_ops.py und wird importiert, nie dupliziert. Auch die Konstanten
  MAX_CONCURRENT_OCR und NAMING_RULES_MAX_CHARS liegen nur dort.
- Die OCR-Pipeline (OCRmyPDF-Aufruf, Seitenrotation, Deskew, Pruefung der
  Textebene) liegt in worker/ocr.py und wird importiert, nie dupliziert.
  Rotate und Deskew sind Optionen eines einzigen ocrmypdf-Aufrufs; separate
  Durchlaeufe waeren Qualitaetsverlust ohne Nutzen.
- Repositorium heisst `mistralai-workflows`, Deployment `mistralai-workflows`; Workflow-Namen
  folgen dem Schema <Domae>-<Aktion> (pdf-scanner-processor,
  pdf-alternating-merge, pdf-ocr, pdf-split).
- Orchestrator-Mechanik: die Step-Klassen liegen in der STEPS-Registry von
  pdf_scanner_processor.py; Child-Workflows werden per Name ueber
  workflows.workflow.execute_workflow mit deterministischen Ausfuehrungs-IDs
  ({run}-{step}-{item}, run = uuid4 des Laufs, stabil beim Replay) gestartet,
  Datei-pro-Step-Kinder parallel via wait=False + asyncio.Semaphore. Weil
  Kinder per Workflow-Namen dispatchet werden, registrieren Tests Fake-Step-
  Klassen unter den Step-Namen im Test-Worker.
- Manage Deployments: kein eigenes Hochskalieren, Parallelitaet innerhalb eines
  Workers via asyncio.gather (MAX_CONCURRENT_OCR).

## Robustheit

- Lange Aktivitaeten (OCR, Download, Upload) senden alle 30 s einen Aktivitaets-Heartbeat (heartbeat_timeout 120 s) und haben retry_policy_max_attempts=2. Ein Worker-Neustart waehrend eines Laufs (Rollout, Crash) wird dadurch in unter zwei Minuten erkannt und die Aktivitaet neu versucht; ohne Heartbeat wuerde Temporal den Aufgabenverlust erst nach start_to_close_timeout (bis zu 45 min) bemerken und der Lauf stuende stumm still.
- worker/ocr.py loggt Start, Ende, Dauer und Fehler jedes OCR-Laufs in die Runtime-Logs; Fortschritt ist damit sichtbar, auch in Dry-Runs.
- keep_original-Versuche sind fortsetzbar: Existiert das Backup in .originals/pdf-ocr schon, aber die Zieldatei fehlt, wird ein frueher unterbrochener Lauf beim naechsten Start fortgesetzt, statt doppelt anzulegen.

## Originale-Verwahrung (.originals)

Gesicherte Originale landen einheitlich unter `<Ordner>/.originals/<Workflow>/`, in jedem Workflow standardmäßig an. pdf-split parkt das Original unter `.originals/pdf-split/<Stamm>/` (pro Datei ein Ordner, wiederholte Läufe erzeugen Zähler-Suffixe statt zu überschreiben); pdf-alternating-merge sichert die Paar-Quellen unter `.originals/pdf-alternating-merge/<Zielname>/`; pdf-ocr verschiebt das Original nach `.originals/pdf-ocr/<Name>`; pdf-scanner-processor parkt vor jeder Änderung eine Kopie jedes Roh-Scans unter `.originals/pdf-scanner-processor/`. Vorteil: ein Ort zum Prüfen und Aufräumen („was darf irgendwann weg?“).
