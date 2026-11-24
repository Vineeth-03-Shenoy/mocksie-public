# Mocksie: an AI voice mock-interview agent

Mocksie runs a realistic practice interview for engineering students: it talks with the candidate by voice (or text), asks questions from a question bank, hands over a coding or SQL task in an editor with a sandboxed judge, and then gives the human interviewer a scored report that maps onto a college evaluation form. The design rule is **the LLM talks, code decides**: a deterministic state machine chooses every question, enforces every timer and runs every test, and the model is only used to phrase questions, follow up, grade answers against key points and write comments. Everything the candidate sends (resume, speech, code) is treated as untrusted data, and no personal details reach an AI service.

> **About the question bank.** This repository ships a small **sample bank** (6 concept questions, 2 coding problems and 1 SQL question, all written for this repo, each with its own tests) so you can run everything. The real question bank and its hidden tests are private and are not part of this repository.

## Architecture and interview flow

<!-- Add the exported diagrams here, e.g. ![Architecture](docs/images/architecture.png) -->
> *Diagram placeholder: system architecture (`docs/images/architecture.png`)*
>
> *Diagram placeholder: interview flow (`docs/images/interview-flow.png`)*

In words: the interviewer signs in to a console, types the candidate's name (and any extra terms to hide), uploads the resume and sends a link. The candidate opens the link and talks to the interviewer. The backend runs one interview as a state machine:

`intro` → `resume warm-up` → `behavioural` → `technical questions` → `coding 1` → (`more technical` or) `coding 2` → `SQL (if triggered)` → `candidate questions` → `close` → `scoring`

While a coding or SQL task is open the conversation (and the voice connection) pauses; the task is judged by the sandbox, and the interviewer resumes with the result. After the interview a stronger model grades each answer twice, maps the evidence onto the evaluation form, and the interviewer reviews, overrides and finalises the results.

## Screenshots

> *Placeholders. Add images under `docs/images/` and link them here:*
> - *Interviewer console: new interview, results list*
> - *Candidate room: voice orb and chat*
> - *Coding task panel with sample tests*
> - *Results page in evaluation-form order, with evidence per score*

## Key engineering decisions

- **PII is redacted before every AI call, and a second line of defence enforces it.** `backend/guardrails/pii.py` removes the candidate's name (every part and initials, case-insensitive, whole-word), extra terms the interviewer types, and patterns (email, Indian phone numbers, links and handles, Aadhaar, PAN, dates of birth, PIN codes). `LLMClient` is a tripwire: it refuses to send any payload that still contains a typed name or term or a pattern hit. Calls use `store=false` and per-model daily usage tracking.
- **A rule-based scrubber on purpose.** No spaCy/Presidio model: the interviewer already knows the candidate's name, so typing it is more reliable than guessing names with an NLP model, and it keeps the dependency set small. The remaining risk (names the interviewer did not type, such as a mentor mentioned in a story) is documented in `SECURITY.md`.
- **Untrusted input is data, never instructions.** Resumes are sanitised (hidden white-on-white text, tiny fonts, off-page text and invisible characters are removed and flagged), injection phrasing is neutralised and fenced, profile extraction uses a strict closed JSON schema, and the model's output is checked again.
- **A deterministic state machine; the LLM only phrases.** The model may *request* transitions through tools (`next_question`, `mark_answer_complete`, `start_coding`, `end_interview`) and the engine validates each one. The engine picks every card, counts questions per stage, adapts difficulty, ends early after three misses, and an output guard stops the model from reading out key points or model answers.
- **A code sandbox on a supported base.** Candidate code runs in [Piston](https://github.com/engineer-man/piston) built from our own `infra/piston/Dockerfile` on Debian 12 with digest-pinned bases, commit-pinned sources, a refreshed npm lock, no npm/yarn/corepack in the runtime image, networking off for candidate code and CPU, memory, process and output limits. SQL runs in a read-only SQLite sandbox. Judging is deterministic: weighted hidden tests (edge cases weigh more), with timeouts reported as evidence.
- **Voice with direct Deepgram Flux and Aura-2 over raw websockets.** Flux supplies speech-to-text with its own end-of-turn detection, so there is no separate voice-activity or turn model. Our engine owns the turn logic: end of turn → redaction → interviewer model → output guard → Aura-2 speech. Barge-in is a setting (off by default on one laptop), timers map onto Flux events (`ForceEndTurn`, timeouts), and both sockets close while a task is open so nothing is billed. Pipecat was evaluated and dropped; the reasoning is in `docs/architecture.md` (4.5).
- **Scoring that can be audited.** Each question is graded twice and averaged (large disagreement is flagged), evidence quotes are verified against the candidate's own words, and technical and problem-solving scores may only move a little from evidence-based anchors. The overall score is the equal-weight mean of the form's criteria, recomputed whenever the interviewer enters or overrides a score, and labels come only from the form template. Comments are written per criterion and a guard keeps the prep material out of student-facing text.
- **Supply-chain discipline and security CI.** Every dependency is installed from a hashed lockfile (`uv.lock`, `package-lock.json`); new packages are vetted by name, downloads, publisher and first-publish date before they are added. `scripts/security.py` (also run by GitHub Actions) checks lockfiles, osv-scanner, pip-audit, `npm audit`, Trivy on the tree and on both images, and gitleaks over the whole history. A pre-commit hook scans staged files for secrets.

## Tech stack

Python 3.12 (FastAPI, Pydantic, pdfplumber, `websockets`), SQLite, Next.js 16 / React (TypeScript, plain CSS, Web Audio AudioWorklet), Piston in Docker, OpenAI models for the interviewer, quick grading and judge, Deepgram Flux and Aura-2 for voice.

## Run it with the sample bank

You need: [uv](https://docs.astral.sh/uv/), Node 20+, Docker Desktop, an OpenAI API key and (for voice) a Deepgram API key. On Windows use PowerShell, from the repo root.

```powershell
copy .env.example .env                                    # then fill in OPENAI_API_KEY (and DEEPGRAM_API_KEY for voice)
uv sync --frozen --project backend                        # backend packages from the lockfile
cd frontend; npm ci --ignore-scripts; cd ..               # frontend packages from the lockfile
uv run --frozen --project backend python -m backend.auth hash   # prints ADMIN_PASSWORD_HASH=...  -> paste into .env
docker compose -f infra/docker-compose.yml up -d piston   # the code judge (Docker must be running)
python infra/piston_install.py                            # first time only: installs the Python, Java and C runtimes
```

Set `SECRET_KEY` in `.env` to at least 32 random characters (`python -c "import secrets;print(secrets.token_urlsafe(48))"`). Then, in two terminals:

```powershell
uv run --frozen --project backend python -m backend       # API on http://localhost:8000
cd frontend; npm run dev                                  # UI on http://localhost:3000
```

Open <http://localhost:3000/admin>, sign in, create an interview (type a name, upload a resume PDF or skip it) and open the candidate link. Use `localhost` for both (not `127.0.0.1`) or the login cookie is not sent. With only 9 sample questions the interview is short: one technical stage with two questions, two coding problems and one SQL question. Check the microphone and speaker echo first at <http://localhost:3000/mic-test>; use headphones for voice.

To use your own questions, follow the pattern in `backend/content/tools/build_question_bank.py` (concept entries plus coding and SQL specs whose tests are generated from reference solutions) and write `backend/content/question_bank.json`; the schema is `backend/content/question_bank.schema.json`. Put the job description in `backend/content/jd.md`.

## Tests and security checks

```powershell
uv run --frozen --project backend pytest                  # unit and API tests (fake LLM, fake Deepgram, fake judge; Java/C tests need a JDK or C compiler)
python backend/judge/verify_bank.py                       # with Piston up: runs every reference solution through the real judge
python scripts/security.py                                # lockfiles, osv, pip-audit, npm audit, trivy, secrets, image scans (needs Docker)
```

## Layout

- `frontend/`  Next.js: interviewer console (`/admin`), candidate room (`/i/[token]`), mic check (`/mic-test`)
- `backend/`   FastAPI: `api`, `orchestrator` (state machine), `voice`, `guardrails`, `judge`, `scoring`, `llm`, `resume`, `content`
- `infra/`     docker-compose and the Piston image
- `scripts/`   security gate and secret scan
- `tests/`     unit tests and red-team cases
- `docs/`      architecture and design notes (`architecture.md`)

## Status

Working end to end in text mode (interview, coding and SQL tasks, scoring, results) and with a first voice loop (verified against the real Deepgram and OpenAI services with simulated speech; the browser microphone path still needs more hands-on testing). Still to do: the full-screen candidate mode, the consent screen and end screens, and lower voice latency (the current round trip from end of speech to first reply audio is several seconds, mostly Flux's end-of-turn wait and two model calls).

## License

MIT. See [LICENSE](LICENSE).
