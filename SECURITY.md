# Security

## Run the checks
```
python scripts/security.py          # everything (same as `make security` and the GitHub workflow)
python scripts/security.py osv pip-audit     # or pick checks
```
Needs Docker running (the scanners are container images pinned by digest). A missing tool is a **failure**, never a skip.

| check | what it does | fails on |
|---|---|---|
| `locks` | `uv.lock` and `package-lock.json` exist, match their manifests, every npm package has an integrity hash | any mismatch |
| `osv` | osv-scanner over both lockfiles | HIGH or CRITICAL |
| `pip-audit` | backend lock exported with hashes, audited without pip | any known vulnerability |
| `npm-audit` | `npm audit --audit-level=high` | HIGH or CRITICAL |
| `trivy-fs` | `trivy fs` for vulnerabilities and secrets | HIGH or CRITICAL |
| `secrets` | gitleaks over the working tree and the full git history | any secret |
| `trivy-image` | builds and scans the backend image; scans the Piston image | backend: HIGH/CRITICAL; Piston: see below |

The pre-commit hook scans what is **staged** for secrets. Enable it once per clone: `make hooks` (or `git config core.hooksPath .githooks`). It needs Docker; bypass a single commit only deliberately with `git commit --no-verify`.

## Dependencies
- Backend: `backend/uv.lock` pins every package with hashes. Install with `uv sync --frozen` (fails if the lock is stale; never re-resolves). The lock is restricted to Windows, Linux and macOS.
- Frontend: `frontend/package-lock.json`. Install with `npm ci --ignore-scripts`, never `npm install`.
- **Before adding a package**: confirm it exists under that exact name, and check weekly downloads, publisher and first-publish date. Nothing with very low downloads or a first publish under a year ago without explicit approval. Packages that arrive transitively are checked too (see the lockfile review in the project notes).
- Scanner images and GitHub Actions are pinned by digest / commit SHA. Update them deliberately.

## The question bank
This repository contains only a small **sample** question bank (`backend/content/question_bank.json`, written for this repo). The production bank, its hidden tests and reference solutions are private and must never be added to this repository. Hidden tests never reach the browser or the interviewer model: see `backend/content/bank.py` (`llm_card`, `browser_problem`) and `tests/test_question_bank.py`.

## Known risks
- **Piston sandbox.** We build our own image (`infra/piston/Dockerfile`) on Debian 12 + Node 20, with base images pinned by digest and the Piston and isolate sources pinned by commit. Its npm lock (`infra/piston/package-lock.json`) is refreshed within upstream's semver ranges via `infra/piston/lock.Dockerfile`; npm, corepack and yarn are removed from the runtime image. `scripts/security.py` builds and scans it like the backend image and **blocks** on any fixable HIGH/CRITICAL finding or secret. It still runs privileged (Piston needs that for its per-run sandbox) and executes untrusted code; mitigations: bound to `127.0.0.1`, networking off for candidate code, 256 MB / 5 s / 64-process limits.
- **Accepted: `uuid` < 11.1.1 (moderate, GHSA-w5hq-g745-h8pq) in the Piston API.** Missing buffer bounds check in v3/v5/v6 when a caller passes its own buffer. Fixing it needs a breaking major upgrade (uuid 14). Piston calls uuid without a buffer, it listens on localhost only and candidate code has no network. Below the HIGH gate. **Revisit after the pilot.**
- OpenAI traffic may be used for training (free daily allowance). Only redacted text may reach it: enforced by `backend/llm/client.py` and its tests.
- **Voice goes to Deepgram.** In voice mode the candidate's raw audio is streamed to Deepgram Flux for transcription, and the interviewer's replies as text to Deepgram Aura-2 for speech. The audio cannot be redacted before transcription: only the transcript is redacted, before it reaches OpenAI. We send `mip_opt_out=true` (opt out of Deepgram's model-improvement data use) and log no transcripts or spoken text. The Deepgram key stays on the server and is never sent to the browser. The candidate consent screen must say that voice is processed by a speech service.
- **PII scrubber is rule-based (no NLP model).** spaCy and Presidio are not used. `backend/guardrails/pii.py` redacts, on the resume AND on live transcripts, before anything reaches OpenAI: (1) the candidate's name, typed by the interviewer from the resume (required): the full name and each part, case-insensitive, whole-word, including initials like "Lakshmi N."; (2) the optional "Also hide" terms (college, city, other names), same matching; (3) patterns for email, Indian phone numbers, URLs and profile handles, Aadhaar, PAN, date of birth, PIN codes and roll numbers / USNs. Tokens are typed (`[NAME] [EMAIL] [PHONE] [LINK] [ID] [REDACTED]`). `backend/guardrails/resume_sections.py` drops References, Address, Declaration, Personal Details, Father's/Mother's name and Date of Birth sections entirely. `LLMClient` is a second line of defence: it refuses to send any payload that still contains a typed name or term, or a pattern hit.
- **Accepted risk (pilot): names the interviewer did not type** (friends, mentors, managers named in a project story) are not recognised by a rule-based scrubber and may still reach OpenAI. Also: a bare 6-digit PIN code is only caught after a "PIN/Pincode/Zip" label or an address-style "City - 560001" on the resume, never in speech; a name part that is also a common English word is redacted everywhere; a resume section with an unusual heading that directly follows a dropped section can be dropped with it (privacy over completeness).
