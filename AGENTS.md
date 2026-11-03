# AGENTS.md — rules for every AI assistant (Claude Code, Cursor, Codex)

This repository is worked on by several AI tools in turn. When one runs out of tokens or
context, another continues. The shared memory is **[`docs/ai-handoff/PROGRESS.md`](docs/ai-handoff/PROGRESS.md)**.

## Start of every session

1. Read `docs/ai-handoff/PROGRESS.md` (current state, completed work, open items, session log).
2. Verify its "Current state" against the repo: `git status --short`, `git log --oneline -3`,
   `cd api && .venv/bin/alembic heads`. If they differ, read the session log and `git diff`
   to understand who changed what before acting.
3. Continue from "Open items / next steps" unless the user asks for something else.

## Hard rules (from the project owner — do not break)

- **Never commit, push, deploy, or run production migrations without explicit user approval.**
- **Never reset, discard, overwrite or `git clean`** changes you did not make. Uncommitted work
  from another agent is real work.
- Never delete backups, benchmark archives, databases or scratch environments without asking
  (backup: `~/Documents/Repositories/E-Sabong-backups/`; archives: `benchmark-archive/`).
- Tests that touch a database run only against **isolated** resources: backend tests use DB
  `esabong_test` + Redis DBs 14/15 (configured by `api/tests/conftest.py`). E2E runs use a
  separate DB / API port / dev-server ports (see `docs/testing.md`), never the developer's
  running stack (API :8000, dev servers :4200–4202, DB `esabong`).
- Do not weaken authentication, authorization, RLS or tenant isolation to make something work.
- **Non-monetary boundary**: play balances are never currency. No cash-out, deposit, purchase or
  transfer of play balance; keep "no cash value" disclaimers; PHP only for subscription billing.
  The UI calls the play balance "Balance"; code and API keep the Arena Points names.
- Do not modify wallet, wager, settlement, accounting or match-outcome logic unless the task
  explicitly asks for it.
- No secrets in code, docs, logs or commit messages. Use placeholders (`CHANGE_ME_…`).
- Only stream/relay video the operator is authorized to use; never bypass DRM or provider auth.
- Report honestly: if a test fails or a step was skipped, say so; label estimates as estimates.

## Conventions

- Backend: `cd api && .venv/bin/ruff check app tests unit_tests && .venv/bin/ruff format app tests unit_tests`
  (line length 130). Run `pytest tests` and `pytest unit_tests` in separate processes.
- Frontend: `cd web && npx ng test <owner|admin|player> --watch=false`, `npx ng build <app>`.
  Prettier is not enforced; match surrounding style, don't mass-reformat.
- New tenant tables need forced RLS in the migration **and** an entry in `TENANT_TABLES`
  (`api/app/models.py`); `tests/test_isolation.py` enforces it.
- Regenerate `docs/openapi.json` after API changes (command in `docs/api.md`); keep the
  trailing newline.
- Commit message style: `[ENG-XXXX] #add|#fix|#change|#perf|#docs - Summary`.

## End of every session (or when running low on tokens) — update protocol

Before you stop, edit `docs/ai-handoff/PROGRESS.md`:

1. Update **"Last updated"** (date + your tool/model name).
2. Update **§1 Current state** (HEAD, number of uncommitted files, latest migration, pending
   user decisions).
3. Move finished work into **§3 Work completed**; update **§4 test results** with what you
   actually ran (and what you did not run).
4. Update **§5 Open items** — remove done items, add new ones, keep them prioritized.
5. Add a **§6 session log** entry at the top: status (`done` / `partial` / `blocked`), what you
   did, files touched, what you verified, the exact next step, and anything half-finished
   (file + function + what remains) so another tool can resume without guessing.

If you are about to run out of context mid-task, write the session-log entry **first**, then
continue. A short accurate entry is better than none.
