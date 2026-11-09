# AGENTS.md

Instructions for AI coding agents (Claude Code, Codex, Antigravity, Cursor, ...) working in this repository.
Humans: this is also the shortest accurate description of how the repo works. Keep it under ~150 lines.

## What this project is

Moshtari-Yab: a Django panel plus background workers. They collect public posts and messages from Telegram, X and
Instagram, use an LLM to extract purchase needs, match them to sellers' products and show the resulting
"opportunities" in the panel. Everything runs on one PostgreSQL 16 database with pgvector.

**The codebase is being refactored in phases. Before any refactor or cleanup work, read `docs/CLEANUP_ROADMAP.md`
(status, next step, baseline, known issues) and update it in the same PR.**

The user-facing UI and its texts are in Persian. Code, identifiers, comments, commit messages and docs for developers are
in English.

## What is actually running (source of truth: `docker-compose.yml` + `docker/entrypoint.sh`)

| Service | Entry point | Code it owns |
|---|---|---|
| `web` | gunicorn `config.wsgi` | `config/`, `apps/`, `templates/`, `static/` |
| `sync` | `manage.py sync_opportunities`, `sync_x_status` | `apps/discovery/engine_bridge.py` |
| `engine` | `python -m need_engine run` | `need_engine/` |
| `crawler` | `python -m telegram_crawler.main` | `telegram_crawler/` |
| `x` (profile `x`) | `python -m workers.x_collector` | `workers/x_collector/`, `x_ingest/` |
| `ig` | `manage.py run_instagram_scraper` | `apps/discovery/` (Instagram parts), `instagram_engine/` |
| `ig-outreach` | `manage.py run_instagram_outreach` | `apps/discovery/outreach_service.py` |
| `channel-config-sync` | `manage.py run_channel_config_automation` | `apps/discovery/channel_config_automation.py`, `xray/multi_proxy_manager.py` |
| `celery-worker`, `celery-beat` | `celery -A config` | `config/celery.py`, `apps/discovery/tasks.py` |
| `retention`, `retention-report` (profile `maintenance`) | `purge_retained_data`, `scripts/retention_report.py` | |

Run outside docker-compose by systemd on the server (keep them): `workers/telegram_config_bot.py` (ops bot) and
`workers/channel_config_collector.py`. `python scripts/find_unreachable.py` lists code no entry point reaches; do not
extend anything it lists (see `docs/CLEANUP_ROADMAP.md`).

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env                      # every config variable is documented there

# Tests need PostgreSQL 16 with pgvector. Without it, DB tests are SKIPPED silently, not failed.
docker compose up -d db
export TEST_DATABASE_URL=postgresql://postgres:<pass>@127.0.0.1:5432/postgres REQUIRE_DB=true
python -m pytest                          # every test, including the Django apps (pytest-django)
python -m pytest path/to/test_x.py::test_name -q   # single test

pip install ruff==0.17.4 && ruff check .  # same version and rules as CI (pyproject.toml)
python scripts/check_baseline.py          # CI guardrail: files over 800 lines and broad excepts may only shrink
pip install pre-commit && pre-commit install   # runs both before every commit
```

Existing violations are recorded in `scripts/guardrail_baseline.json` (file size, broad excepts) and in `ALLOWED` in
`tests/test_architecture.py` (import boundaries). Both may only shrink: after you shrink a file or remove an except,
run `python scripts/check_baseline.py --update`; after you remove a forbidden import, delete its `ALLOWED` entry. Never
add entries. On pull requests CI also runs `scripts/check_commits.py` (Conventional Commits, no AI attribution).

Test files are named `test_*.py`, `tests.py` or `tests_*.py` and live under a `testpaths` directory of `pytest.ini`;
anything else never runs in CI.

## Rules that exist because we broke them before

This code was built fast with AI help, and the same mistakes kept piling up. Each rule below prevents one of them.

1. **Search before you write.** Before adding a function, class, helper, model or command, grep for an existing one
   (`grep -rn "def <likely_name>\|<keyword>" --include=*.py .`). If something similar exists, extend or reuse it. Never
   write a second implementation next to the first.
2. **No parallel implementations.** Do not create a new package, worker, "v2", "new_", "_old" or "engine" folder for
   something that already has an owner in the table above. There is one Instagram stack (`run_instagram_scraper` +
   `instagram_engine/`); do not start a second one. A new top-level package needs explicit approval from the user.
3. **Replace means delete.** When new code replaces old code, delete the old code, its tests, its settings and its docs
   in the same change. Don't keep anything "for reference", "for compatibility" or "just in case". Git history is the
   backup.
4. **File size.** Do not grow a file that is already over 800 lines (for example `apps/billing/manage_views.py`,
   `templates/ops/manage_dashboard.html`, `run_instagram_scraper.py`). Put new code in a new, focused module, or split
   the file first in a separate change. New files should stay under 400 lines.
5. **No silent failures.** Don't write `except Exception: pass` or a broad `except` that only logs and continues. Catch
   the specific exception you expect. A broad catch is allowed only at a process boundary (worker loop, management
   command) and must call `log.exception(...)`.
6. **Config.** A new environment variable must be read in one place (the module's `config.py` / settings) and added to
   `.env.example` with a comment in the same change. Don't scatter `os.environ.get` calls.
7. **Dependency direction.** `shared/` imports none of the project packages. `need_engine/`, `telegram_crawler/` and
   the workers do not import `apps/` (Django). The panel talks to workers through the database, not imports.
8. **Scope.** Do one thing per change. Don't refactor, rename or "improve" code unrelated to the task. If you notice a
   problem, report it instead of fixing it on the side.
9. **No new docs files as a side effect.** Don't create analysis, summary or report `.md` files unless asked. Update the
   existing README or doc instead. Stale docs are worse than none.
10. **Dependencies.** Don't add a package to `requirements.txt` without asking. Check first whether the stdlib or an
    existing dependency already covers it.
11. **Migrations.** Never edit an applied migration. Create a new one with `python manage.py makemigrations <app>` and
    review the generated file.

## Definition of done

- A bug fix comes with a test that fails without the fix (use the `systematic-debugging` and
  `test-driven-development` skills).
- `ruff check .` and `python -m pytest` were actually run and passed. Paste the result
  in the summary. If you could not run them (no database, for example), say so explicitly; never say "should pass".
  See the `verification-before-completion` skill.
- The diff contains only the task. Dead code you replaced is gone.

## Git workflow

- Never edit or create files directly on the production server (194.60.231.71). All changes are made locally and
  deployed from git.
- Start every task from an up-to-date `main` on a new short-lived branch. Branch names follow
  `<type>/<short-kebab-description>`, using the same types as commits: `feat/x-reply-drafts`,
  `fix/ig-session-expiry`, `refactor/split-manage-views`, `chore/remove-legacy-scripts`. No personal or vague names
  (`new-javad`, `taha2`, `fix-conflicts`, `refactor`).
- Never push directly to `main`. Changes reach `main` only through a GitHub pull request with green CI and the
  user's explicit approval. Conflicts are resolved together with the user. Deployment happens only after the merge.
  Delete the branch after it is merged.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  `<type>(<optional scope>): <imperative summary>`, at most 72 characters, where type is one of `feat`, `fix`,
  `refactor`, `perf`, `test`, `docs`, `build`, `ci`, `chore`. Add a body explaining *why* when it isn't obvious. One
  logical change per commit.
- **No AI attribution anywhere.** Commits are authored by the human's own git identity (`git config user.name` /
  `user.email`, their real GitHub account). Don't add `Co-Authored-By:` trailers for an AI, "Generated with Claude
  Code / Antigravity / ..." lines, or AI names in commit messages, PR descriptions, code comments or `git blame`.
  Never change `git config` or pass `--author`.

## Skills

`.claude/skills/` holds the skills agents should load (Claude Code loads them automatically; other agents should read
the `SKILL.md` when the situation matches):

- `systematic-debugging`: any bug, test failure or unexpected behavior, before proposing a fix.
- `test-driven-development`: any feature or bug fix, before writing implementation code.
- `verification-before-completion`: before claiming anything is done, fixed or passing.
- `removing-dead-code`: before deleting a module, function, template, setting or service.

## Domain notes

- The LLM used by the engine and bots is configured through `NE_LLM_*` in `.env`. The Telegram ops bot reports the
  `gpt-6-luna` model ("Luna") on Houshyar24. Don't hardcode model names or keys anywhere else.
- Instagram and X accounts get banned easily. Don't change rate limits, delays, daily caps or anti-ban behavior
  (`INSTAGRAM_ANTIBAN_*`, outreach `--max-daily-dms`, ...) unless the task explicitly asks for it.
- Secrets in the DB are encrypted with `FIELD_ENCRYPTION_KEY` (see `README.md`). Never log decrypted values.
