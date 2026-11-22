# Third-party licenses

> English only (dev-facing file, like `AGENTS.md`). Inventory of the licenses of everything this
> project depends on or ships in its worker image. The repository itself is **AGPL-3.0-only**
> (see [LICENSE](LICENSE)). Generated from `uv.lock`/installed package metadata, `bun.lock`,
> and `deploy/docker/worker-packages/workflows.txt`.

## Summary

The strongest copyleft license in the dependency tree is **AGPL-3.0** (this repository plus the
`ghostscript` and `jbig2dec` system packages in the worker image). Everything else is permissive
(MIT/BSD/Apache-2.0/ISC/PSF) or **MPL-2.0 / LGPL-3.0**, which are compatible with AGPL-3.0
(MPL-2.0 §3.3 allows combination with AGPL-licensed code; LGPL-3.0 is GPL-3.0-based and its
additional permissions do not conflict with AGPL-3.0's §13). AGPL §13 (network use) applies to
the service as a whole and is stated in the [README](README.md): the source is this repository.

## Python (runtime, worker) — from installed package metadata

Direct runtime dependencies of the worker (`apps/worker/pyproject.toml`): `mistralai`
(Apache-2.0), `mistralai-workflows[mistralai]` (Apache-2.0), `pydantic` (MIT), `structlog`
(MIT OR Apache-2.0), `ocrmypdf` (MPL-2.0), `pypdf` (BSD-3-Clause), plus the workspace packages
`utils` and `env` (this repository, AGPL-3.0-only).

Transitive dependencies by license group:

- **MIT**: annotated-doc, annotated-types, anyio, asynciolimiter, attrs, eval-type-backport,
  fonttools, griffe, griffelib, h11, iniconfig, joserfc, jsonpatch, jsonpath-python, jsonschema,
  jsonschema-specifications, libcst, markdown-it-py, mdurl, mcp, mcp-types, orjson (also
  Apache-2.0/MIT), pillow (MIT-CMU), pluggy, pydantic-core, pydantic-settings, pyjwt, pytest,
  python-dateutil, python-dotenv, pyyaml, referencing, rich, rpds-py, structlog, tenacity,
  temporalio, truststore, typer, typing-inspection, tzdata, urllib3, watchfiles
- **BSD-2-Clause / BSD-3-Clause**: authlib, cffi (MIT-0), click, colorama, httpcore, httpx,
  idna, jsonpointer, lxml, packaging, pycparser, pygments, pypdf, pypdfium2, sse-starlette,
  starlette, uvicorn, wrapt
- **Apache-2.0**: cryptography (Apache-2.0 OR BSD-3-Clause), googleapis-common-protos,
  mistralai, mistralai-capabilities-backend-workflows, mistralai-capabilities-deployment-apps,
  mistralai-workflows, mistralai-workflows-plugins-mistralai, mistralai-workflows-* extras,
  opentelemetry-* (api/sdk/exporters/instrumentation), protobuf (3-Clause BSD), python-multipart,
  requests, uharfbuzz
- **MPL-2.0**: certifi, ocrmypdf, pikepdf
- **LGPL-3.0+**: img2pdf (transitive via ocrmypdf), fpdf2 (transitive)
- **ISC**: griffecli, shellingham
- **PSF-2.0**: typing-extensions
- **BSD (upstream, wheel lacks SPDX classifier)**: aiocache

## npm / bun (`package.json`, `bun.lock`)

- Runtime: `@mistralai-capabilities/backend-workflows`, `@mistralai-capabilities/base-core`,
  `@mistralai-capabilities/deployment-apps` (Apache-2.0)
- Dev-only: `nx` (MIT), `typescript` (Apache-2.0), `@types/bun` (MIT)

## System packages in the worker image (`deploy/docker/worker-packages/workflows.txt`)

- `ghostscript` — **AGPL-3.0-or-later**
- `tesseract-ocr-all` (incl. deu/eng/fra/ita/spa language packs) — Apache-2.0
- `jbig2dec` — **AGPL-3.0-or-later**

These run as separate processes invoked by OCRmyPDF; they are not linked into the Python code.
The AGPL-3.0 obligations therefore attach to the image/service, which is why the repository
itself is AGPL-3.0-only and the README states the §13 source offer.

## Notes

- License texts: each installed package carries its license in its `dist-info` (`licenses/`);
  the canonical texts are on [SPDX](https://spdx.org/licenses/) and the vendors' sites.
- If dependencies are added or upgraded, re-run this inventory (the source data is the lockfiles
  plus the installed metadata) and update this file in the same PR.
