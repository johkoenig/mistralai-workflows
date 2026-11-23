"""Shared building blocks of all workflows of this app.

Pure logic (naming rules, duplex interleaving, pair detection) and the shared
name-document activity live here, so naming behavior and the merge algorithm
exist exactly once.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import re
import shutil
import tempfile
import unicodedata
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

from mistralai import workflows
from mistralai.workflows.plugins.mistralai import get_mistral_client
from mistralai.workflows.plugins.mistralai.connectors import (
    ToolCallClient,
    connector,
)

if TYPE_CHECKING:
    from mistralai.client.models import (
        AssistantMessage,
        ChatCompletionRequestMessageTypedDict,
    )


def _file_connector_names() -> list[str]:
    """Names of the file connectors from FILE_CONNECTOR_NAMES (comma list, default nextcloud)."""
    raw = os.environ.get("FILE_CONNECTOR_NAMES", "nextcloud")
    names = [name.strip() for name in raw.split(",") if name.strip()]
    return list(dict.fromkeys(names)) or ["nextcloud"]


FILE_CONNECTOR_NAMES = _file_connector_names()
DEFAULT_FILE_CONNECTOR = FILE_CONNECTOR_NAMES[0]
file_connectors = {name: connector(name) for name in FILE_CONNECTOR_NAMES}


def file_client(connector: str = "") -> ToolCallClient:
    """ToolCallClient for the selected file connector (empty = default).

    The name must match one of the slots declared via @uses_connectors
    (FILE_CONNECTOR_NAMES); unknown names raise ValueError.
    """
    name = connector or DEFAULT_FILE_CONNECTOR
    if name not in file_connectors:
        raise ValueError(
            f"unbekannter Datei-Connector {name!r}; konfiguriert via "
            f"FILE_CONNECTOR_NAMES: {FILE_CONNECTOR_NAMES}"
        )
    return ToolCallClient(name)


logger = logging.getLogger("worker.pdf_ops")


DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
TEXT_SNIPPET_CHARS = 6000
COMPARE_TEXT_CHARS = 4000
PAIR_SIZE_RATIO_MIN = 0.5
PAIR_SIZE_RATIO_MAX = 2.0
PAIR_MIN_SIMILARITY = 0.4
NAME_MAX_CHARS = 80
MIN_NAMING_CONFIDENCE = 0.7
# Memory guard: parallel OCR with -j 1 per file, uniform across all workflows.
MAX_CONCURRENT_OCR = 2
# Folder-specific naming rules (AGENTS.md): maximum number of characters loaded.
NAMING_RULES_MAX_CHARS = 4000
# Uniform storage location for secured originals of all workflows.
ORIGINALS_DIR_NAME = ".originals"
FRONT_FILE_NAME = "FrontPages.pdf"
BACK_FILE_NAME = "BackPages_reversed.pdf"
UMLAUT_MAP = {
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "Ä": "Ae",
    "Ö": "Oe",
    "Ü": "Ue",
    "ß": "ss",
}
SYSTEM_PROMPT = (
    "You receive the OCR text of a scanned document. Derive a file name "
    'following the pattern "YYYY-MM-DD short descriptive title.pdf". Rules: '
    "1) The date comes first and is taken from the document (preferably the date stamp "
    "at the top of the document, e.g. invoice or letter date); if no reliable date "
    'is evident, return "date": null. '
    '2) "title" is a short, meaningful name in the language of the document (and of '
    "the user's folder rules), with document type and, where evident, sender, "
    "organization or subject (max 80 characters). "
    '3) "confidence" (0.0 to 1.0) rates how certain you are about date and title overall; '
    "use a low value when unsure. "
    'Answer with JSON only: {"date": "YYYY-MM-DD" or null, "title": "...", '
    '"confidence": 0.0 to 1.0}.'
)


def sanitize_title(title: str) -> str:
    """Cleans a title for use as a file stem: transliterates umlauts, ASCII-only, length limit."""
    title = re.sub(r"\.pdf$", "", title.strip(), flags=re.IGNORECASE)
    for umlaut, replacement in UMLAUT_MAP.items():
        title = title.replace(umlaut, replacement)
    title = (
        unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    )
    title = re.sub(r'[\\/:*?"<>|]', " ", title)
    title = re.sub(r"\s+", " ", title).strip(" .")
    if len(title) > NAME_MAX_CHARS:
        title = title[:NAME_MAX_CHARS].strip(" .")
    return title or "Document"


def valid_date(date: str | None) -> str | None:
    """Accepts only plausible date values of the form YYYY-MM-DD, otherwise None."""
    if not date:
        return None
    match = DATE_RE.match(date)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    if not (1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31):
        return None
    return date


def build_filename(date: str | None, title: str) -> str:
    """Target file name from date and title; without a date only the cleaned title."""
    cleaned = sanitize_title(title)
    return f"{date} {cleaned}.pdf" if date else f"{cleaned}.pdf"


def unique_filename(base: str, taken: set[str]) -> str:
    """Adds counter suffixes (_2, _3, ...) as long as base is already taken."""
    if base not in taken:
        return base
    stem, _, ext = base.rpartition(".")
    counter = 2
    while f"{stem}_{counter}.{ext}" in taken:
        counter += 1
    return f"{stem}_{counter}.{ext}"


def normalize_text(text: str) -> str:
    """ASCII-normalized lowercase text as the basis for similarity comparisons."""
    normalized = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", " ", normalized.lower()).strip()


def text_similarity(a: str, b: str) -> float:
    """Similarity of two texts: maximum of sequence ratio and Jaccard, 0 to 1."""
    norm_a = normalize_text(a)[:COMPARE_TEXT_CHARS]
    norm_b = normalize_text(b)[:COMPARE_TEXT_CHARS]
    if not norm_a or not norm_b:
        return 0.0
    ratio = difflib.SequenceMatcher(None, norm_a, norm_b).ratio()
    tokens_a = set(norm_a.split())
    tokens_b = set(norm_b.split())
    union = tokens_a | tokens_b
    jaccard = len(tokens_a & tokens_b) / len(union) if union else 0.0
    return max(ratio, jaccard)


def interleave_order(page_count: int) -> list[tuple[str, int]]:
    """Front pages in scan order, each followed by its back page (reverse order)."""
    order: list[tuple[str, int]] = []
    for i in range(page_count):
        order.append(("front", i))
        order.append(("back", page_count - 1 - i))
    return order


def detect_pairs(
    files: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pair consecutive scans that look like front/back halves of one duplex document."""
    ordered = sorted(files, key=lambda f: f["order"])
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    consumed: set[str] = set()
    for i in range(len(ordered) - 1):
        first, second = ordered[i], ordered[i + 1]
        if first["path"] in consumed or second["path"] in consumed:
            continue
        if first["page_count"] == 0 or first["page_count"] != second["page_count"]:
            continue
        if first["size"] == 0 or second["size"] == 0:
            continue
        ratio = first["size"] / second["size"]
        if not PAIR_SIZE_RATIO_MIN <= ratio <= PAIR_SIZE_RATIO_MAX:
            continue
        if not first["has_text"] or not second["has_text"]:
            continue
        if text_similarity(first["text"], second["text"]) < PAIR_MIN_SIMILARITY:
            continue
        pairs.append((first, second))
        consumed.update({first["path"], second["path"]})
    return pairs


GERMAN_HINT_WORDS = (
    " der ",
    " die ",
    " und ",
    " nicht ",
    " ist ",
    " mit ",
    " rechnung",
    " datum",
    " absender",
)


def front_back_suffixes(text: str) -> tuple[str, str]:
    """Front/back page suffixes in the document's language.

    German umlauts or at least two typical German words in the text mean a
    German document ("Vorderseite"/"Rueckseite"); everything else gets the
    English "Front"/"Back". The LLM titles already follow the document
    language via the naming prompt; this covers the code-added suffixes.
    """
    lowered = f" {text.lower()} "
    if (
        any(char in text for char in "äöüß")
        or sum(1 for word in GERMAN_HINT_WORDS if word in lowered) >= 2
    ):
        return "Vorderseite", "Rueckseite"
    return "Front", "Back"


def merge_texts(front_text: str, back_text: str) -> str:
    """Combines front and back page text for joint naming.

    In a duplex scan the back pages are in reverse page order; their lines are
    therefore inverted before both texts are concatenated. Empty pages drop out.
    """
    back_lines = back_text.strip().splitlines()[::-1]
    parts = [front_text.strip(), "\n".join(back_lines)]
    return "\n\n".join(part for part in parts if part)


def unwrap_tool_result(result: Any) -> Any:
    """Unwraps tool responses (JSON string, structuredContent, text blocks) into Python data."""
    if isinstance(result, str):
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return result
    if isinstance(result, dict):
        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if (
                    isinstance(item, dict)
                    and item.get("type") == "text"
                    and "text" in item
                ):
                    try:
                        return json.loads(item["text"])
                    except (json.JSONDecodeError, TypeError):
                        return item["text"]
        return result
    return result


async def nc_call(nc: ToolCallClient, tool: str, arguments: dict[str, Any]) -> Any:
    """Runs a Nextcloud tool call and unwraps its response."""
    return unwrap_tool_result(await nc.call_tool(tool_name=tool, arguments=arguments))


def pick_url(payload: dict[str, Any], *keys: str) -> str:
    """Returns the first non-empty URL field from the payload, case-insensitive."""
    for key in keys:
        if payload.get(key):
            return cast("str", payload[key])
    lowered = {str(k).lower(): v for k, v in payload.items()}
    for key in keys:
        if lowered.get(key.lower()):
            return cast("str", lowered[key.lower()])
    raise ValueError(f"no URL field {keys} in {sorted(payload)}")


def http_get(url: str, dest: str) -> None:
    """Streams url to dest (timeout 300 s)."""
    import shutil
    import urllib.request

    with urllib.request.urlopen(url, timeout=300) as response, open(dest, "wb") as out:
        shutil.copyfileobj(response, out)


def http_put(url: str, src: str) -> None:
    """Uploads src as an octet stream via PUT to url (timeout 600 s)."""
    import urllib.request

    with open(src, "rb") as handle:
        data = handle.read()
    request = urllib.request.Request(
        url,
        data=data,
        method="PUT",
        headers={"Content-Type": "application/octet-stream"},
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        response.read()


def extract_texts(pdf_path: str) -> tuple[list[str], int]:
    """Page texts and page count of a PDF; encrypted PDFs raise ValueError."""
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    if reader.is_encrypted:
        raise ValueError("PDF is encrypted")
    pages = [page.extract_text() or "" for page in reader.pages]
    return pages, len(reader.pages)


def merge_pdf_files(front_path: str, back_path: str, out_path: str) -> int:
    """Interleave front pages (in order) with back pages (reversed); returns page count."""
    from pypdf import PdfReader, PdfWriter

    front_reader = PdfReader(front_path)
    back_reader = PdfReader(back_path)
    page_count = len(front_reader.pages)
    writer = PdfWriter()
    for side, index in interleave_order(page_count):
        reader = front_reader if side == "front" else back_reader
        writer.add_page(reader.pages[index])
    with open(out_path, "wb") as handle:
        writer.write(handle)
    return 2 * page_count


def originals_dir(folder: str, workflow: str) -> str:
    """Uniform storage location for secured originals.

    <folder>/.originals/<workflow>. Whether a further subfolder per file is
    created below it depends on the workflow (pdf-split and pdf-merge-duplex
    use one, pdf-ocr and pdf-scan-inbox-triage park the file directly), so
    repeated runs never overwrite anything.
    """
    return f"{folder.rstrip('/')}/{ORIGINALS_DIR_NAME}/{workflow}"


def listing_entries(result: Any) -> list[Any]:
    """Extracts the file list (first level) from a files_list result."""
    return (
        result
        if isinstance(result, list)
        else next((v for v in result.values() if isinstance(v, list)), [])
    )


async def ensure_folder(nc: ToolCallClient, path: str) -> None:
    """Creates path (including intermediate folders); an existing folder is fine."""
    try:
        await nc_call(
            nc, "files_mkdir", {"path": path, "confirm": True, "confirm_shared": True}
        )
    except Exception as exc:
        logger.debug("files_mkdir %s skipped (probably exists): %s", path, exc)


async def list_names(nc: ToolCallClient, folder: str) -> set[str]:
    """Names of the first level of folder; empty set when the folder does not exist (yet)."""
    try:
        listing = await nc_call(nc, "files_list", {"path": folder})
    except Exception:
        return set()
    return {
        cast("str", entry.get("name"))
        for entry in listing_entries(listing)
        if isinstance(entry, dict) and entry.get("name")
    }


async def park_original(nc: ToolCallClient, from_path: str, originals_root: str) -> str:
    """Copies from_path into originals_root under its own name; returns the backup path.

    Never overwrites: on a name collision the backup gets a _2, _3, ... suffix.
    Used by workflows that keep the source in place (rename, OCR overwrite),
    so the original is copied, not moved.
    """
    await ensure_folder(nc, originals_root)
    name = os.path.basename(from_path)
    taken = await list_names(nc, originals_root)
    dest = f"{originals_root.rstrip('/')}/{unique_filename(name, taken)}"
    await nc_call(
        nc,
        "files_copy",
        {"from": from_path, "to": dest, "confirm": True, "confirm_shared": True},
    )
    return dest


def part_ranges(page_count: int, split_at: list[int]) -> list[tuple[int, int]]:
    """1-based inclusive page ranges per part from split points.

    split_at contains page numbers AFTER which to split; [2, 4] yields parts
    1-2, 3-4, 5-8 for 8 pages. Invalid input (empty, not ascending, duplicates,
    outside the document) raises ValueError.
    """
    if not isinstance(split_at, (list, tuple)) or not split_at:
        raise ValueError("split_at missing: provide at least one split point")
    cuts = [int(cut) for cut in split_at]
    if sorted(set(cuts)) != cuts:
        raise ValueError("split_at must be ascending and free of duplicates")
    for cut in cuts:
        if cut < 1 or cut >= page_count:
            raise ValueError(
                "split points must lie between page 1 and page "
                f"{page_count - 1} (PDF has {page_count} pages)"
            )
    ranges: list[tuple[int, int]] = []
    start = 1
    for cut in cuts:
        ranges.append((start, cut))
        start = cut + 1
    ranges.append((start, page_count))
    return ranges


def split_pdf_into_ranges(
    pdf_path: str, ranges: list[tuple[int, int]], out_paths: list[str]
) -> None:
    """Writes one PDF per range (pages 1-based, inclusive end)."""
    from pypdf import PdfReader, PdfWriter

    if len(ranges) != len(out_paths):
        raise ValueError("ranges and out_paths must have the same length")
    reader = PdfReader(pdf_path)
    if reader.is_encrypted:
        raise ValueError("PDF is encrypted")
    for (start, end), out_path in zip(ranges, out_paths):
        writer = PdfWriter()
        for index in range(start - 1, end):
            writer.add_page(reader.pages[index])
        with open(out_path, "wb") as handle:
            writer.write(handle)


@workflows.activity(
    name="name-document",
    start_to_close_timeout=timedelta(minutes=5),
    retry_policy_max_attempts=2,
)
async def name_document(
    text: str, model: str, naming_rules: str = ""
) -> dict[str, Any]:
    """Names a document via LLM: date, cleaned title and confidence."""
    system = SYSTEM_PROMPT
    if naming_rules and naming_rules.strip():
        system += (
            "\n\nAdditional naming rules for the titles "
            "(from the folder's AGENTS.md; these rules take precedence in "
            "title selection):\n---\n" + naming_rules.strip() + "\n---"
        )
    client = get_mistral_client()
    messages: list[ChatCompletionRequestMessageTypedDict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Document text:\n{text[:TEXT_SNIPPET_CHARS]}"},
    ]
    response = client.chat.complete(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0.0,
        top_p=1,
    )
    content = cast("AssistantMessage", response.choices[0].message).content
    try:
        data: Any = json.loads(content) if isinstance(content, str) else content
        date = valid_date(data.get("date"))
        title = sanitize_title(str(data.get("title") or ""))
        confidence = float(data.get("confidence") or 0.0)
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        date, title, confidence = None, "", 0.0
    return {"date": date, "title": title, "confidence": max(0.0, min(1.0, confidence))}


SPLIT_TEXT_PER_PAGE = 1200
SPLIT_SYSTEM_PROMPT = (
    "You receive the texts of all pages of a scanned PDF, each marked with "
    "'=== page N ==='. Check whether the PDF contains several independent "
    "documents (e.g. several letters or cover letters including attachments). "
    "Rules: "
    '1) "split_at" contains the page numbers AFTER which to split '
    "(ascending, no duplicates, between page 1 and the second-to-last page). "
    "If everything is exactly one coherent document, return an empty "
    "array []. "
    '2) "titles" contains one short, meaningful title per resulting part in the '
    "language of the document (max 80 characters; date, sender or subject if "
    'evident); the number of "titles" must match the number of parts '
    "(exactly one title when split_at is empty). "
    '3) "confidence" (0.0 to 1.0) rates how certain you are about the split '
    "points and titles. "
    "4) If the texts are ambiguous or a split point could fall in the middle of a "
    'document, set "needs_input": true and put a concrete, briefly answerable '
    'question in "question" (in the language of the document). '
    'Answer with JSON only: {"split_at": [n], "titles": ["..."], '
    '"confidence": 0.0, "needs_input": false, "question": ""}.'
)


def parse_split_plan(
    content: Any, page_count: int, fixed_split_at: list[int] | None = None
) -> dict[str, Any]:
    """Evaluates the model answer of the split-point/title planning.

    fixed_split_at set = only plan titles, the split points are fixed.
    Invalid proposals are reported as a follow-up question (needs_input) instead
    of being passed on unchecked.
    """

    def question(message: str) -> dict[str, Any]:
        return {
            "split_at": [],
            "titles": [],
            "confidence": 0.0,
            "needs_input": True,
            "question": message,
        }

    data = content
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, dict):
        return question("model response was not parseable JSON")
    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    needs_input = bool(data.get("needs_input"))
    return_question = " ".join(str(data.get("question") or "").split())

    raw_cuts = fixed_split_at if fixed_split_at is not None else data.get("split_at")
    if raw_cuts in (None, [], ""):
        split_at: list[int] = []
        ranges: list[tuple[int, int]] = []
    else:
        if not isinstance(raw_cuts, (list, tuple)):
            return question("proposed split points are not a list")
        try:
            split_at = [int(cut) for cut in raw_cuts]
            ranges = part_ranges(page_count, split_at)
        except (TypeError, ValueError) as exc:
            return question(f"proposed split points invalid: {exc}")

    part_count = len(ranges) if ranges else 1
    titles: list[str] = []
    if isinstance(data.get("titles"), (list, tuple)):
        titles = [str(title).strip() for title in data["titles"] if str(title).strip()]
    if len(titles) != part_count:
        if fixed_split_at is None:
            needs_input = True
            return_question = (
                return_question
                or f"title count ({len(titles)}) does not match the number of parts ({part_count})"
            )
        titles = []
    return {
        "split_at": split_at,
        "titles": titles,
        "confidence": max(0.0, min(1.0, confidence)),
        "needs_input": needs_input,
        "question": return_question,
    }


@workflows.activity(
    name="plan-split",
    start_to_close_timeout=timedelta(minutes=5),
    retry_policy_max_attempts=2,
)
async def plan_split(
    page_texts: list[str],
    page_count: int,
    fixed_split_at: list[int] | None,
    model: str,
    naming_rules: str,
) -> dict[str, Any]:
    """Lets the LLM propose split points and part titles (pdf-split).

    Without fixed_split_at, split points and titles are planned; with
    fixed_split_at only the titles for already fixed split points.
    naming_rules contains the content of an optional AGENTS.md from the
    target file's folder (folder-specific naming rules, empty when the
    file is missing).
    """
    pages = "\n\n".join(
        f"=== page {number} ===\n{(text or '')[:SPLIT_TEXT_PER_PAGE]}"
        for number, text in enumerate(page_texts, start=1)
    )
    system = SPLIT_SYSTEM_PROMPT
    if naming_rules and naming_rules.strip():
        system += (
            "\n\nAdditional naming rules for the part titles "
            "(from the folder's AGENTS.md; these rules take precedence in "
            "title selection):\n---\n" + naming_rules.strip() + "\n---"
        )
    client = get_mistral_client()
    messages: list[ChatCompletionRequestMessageTypedDict] = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Page count: {page_count}\n\nDocument texts:\n"
                f"{pages[:TEXT_SNIPPET_CHARS]}"
            ),
        },
    ]
    response = client.chat.complete(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0.0,
        top_p=1,
    )
    return parse_split_plan(
        cast("AssistantMessage", response.choices[0].message).content,
        page_count,
        fixed_split_at,
    )


def read_rules_text(path: str) -> str:
    """Reads the rules file synchronously, limited to NAMING_RULES_MAX_CHARS."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read(NAMING_RULES_MAX_CHARS)


@workflows.activity(
    name="load-naming-rules",
    start_to_close_timeout=timedelta(minutes=5),
    retry_policy_max_attempts=2,
)
async def load_naming_rules(folder: str, connector: str = "") -> dict[str, Any]:
    """Reads naming rules from <folder>/AGENTS.md; if missing: empty.

    Folder-specific naming rules (schemas, persons, exceptions such as
    health-insurance receipts) live per folder as AGENTS.md and are passed
    to all AI naming steps (name-document, plan-split).
    """
    nc = file_client(connector)
    path = f"{folder.rstrip('/')}/AGENTS.md"
    workdir = tempfile.mkdtemp(prefix="rules-")
    try:
        try:
            checkout = await nc_call(
                nc,
                "files_checkout",
                {"path": path, "confirm": True, "confirm_shared": True},
            )
            local = os.path.join(workdir, "AGENTS.md")
            http_get(pick_url(checkout, "download_url", "downloadUrl"), local)
            rules = await asyncio.to_thread(read_rules_text, local)
        except Exception:
            return {"rules": "", "source": None}
        logger.info("naming rules loaded: %s (%d chars)", path, len(rules))
        return {"rules": rules, "source": path}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
