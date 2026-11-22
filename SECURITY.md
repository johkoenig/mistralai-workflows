# Security Policy

## Reporting a vulnerability

Please do **not** open a public issue, discussion, or pull request for a security vulnerability.

Report privately via GitHub's private vulnerability reporting on this repository:
**Security → Advisories → "Report a vulnerability"**
(<https://github.com/johkoenig/mistralai-workflows/security/advisories/new>).

Please include, where possible:

- the affected commit/branch (only `main` is served),
- a description of the issue and its security impact,
- steps to reproduce or a proof of concept,
- any suggested mitigation.

You can expect an acknowledgement as soon as possible, typically within a few days. We keep you
informed about the progress and coordinate the fix and the public disclosure with you. Accepted
reports are published as a security advisory here once a fix is available; reporters are credited
unless they prefer otherwise.

## Supported versions

Only the latest commit on the default branch (`main`) is supported. Security fixes are merged to
`main` (green CI is a required status check) and rolled out to the managed workflow deployment;
there are no release branches to backport to.

## Scope

- This repository contains no secrets, credentials, tokens, or personal data; CI, push protection,
  and secret scanning enforce this. If you believe you found a secret or personal data in the
  repository or its history, please report it privately as above.
- The workflows process PDF files from your own Nextcloud file storage. Vulnerabilities in that
  processing pipeline (the worker code under `apps/worker/`) are in scope.
- Vulnerabilities in third-party dependencies are tracked via Dependabot alerts and GitHub security
  advisories; please still report privately if you believe one is exploitable through this project.
- The project is licensed AGPL-3.0-only (see [LICENSE](LICENSE)); §13 (network use) applies: if you
  offer this software as a network service, the corresponding source must be made available to
  network users — this repository is that source.

## Out of scope

- Security issues in your own infrastructure (Nextcloud instance, file connectors, Mistral API
  account and its quota).
- Denial of service via arbitrarily large inputs beyond the documented limits of the workflows.
