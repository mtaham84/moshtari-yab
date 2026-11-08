# Cleanup roadmap

Goal: make the codebase small, understandable and safe to change, without a rewrite and without stopping production.
Each phase ships as small PRs to `main` from short-lived `<type>/<description>` branches, with CI green, before the
next phase starts. Status as of 2026-11-08.

## Phase 0: Guardrails for AI-assisted development (in progress)

The mess came from the process, not from one bad decision. Fix the process first, or the cleanup will be undone.

- [x] Rewrite `AGENTS.md`: what runs, real commands, rules against duplication, dead code, giant files and silent
      errors; definition of done; git workflow. `CLAUDE.md` imports it for Claude Code.
- [x] Add skills in `.claude/skills/`: `systematic-debugging`, `test-driven-development`,
      `verification-before-completion` (from obra/superpowers) and `removing-dead-code` (project-specific).
- [x] Git conventions in `AGENTS.md`: standard branch names, Conventional Commits, no AI attribution in commits/PRs.
- [ ] Review and commit these files on `docs/agent-guidelines`, open a PR to `main`.
- [ ] Remove the hardcoded `taha` branch from `scripts/compare_and_merge_main.sh` (take the branch as an argument)
      or delete the script in favor of GitHub PRs.
- [ ] Mechanical guardrails in CI, because agents ignore text rules more often than failing checks:
  - [ ] fail CI when a changed `.py`/`.html` file goes over 800 lines (allow-list for existing big files that may
        only shrink)
  - [ ] `tests/test_architecture.py`: AST import-boundary test (rule 7 in `AGENTS.md`)
  - [ ] enable the stage-2 ruff rules listed in `pyproject.toml` (`BLE001`, `S110`, ...) with a baseline, so new
        code can't add broad excepts
  - [ ] `pre-commit` running ruff locally
  - [ ] CI check that rejects PR commits containing AI attribution (`Co-Authored-By: Claude|Antigravity|...`,
        "Generated with ...") or a non-Conventional-Commits subject. `.claude/settings.json` already turns off
        Claude Code's own attribution; other tools are only covered by this check.
- [ ] Working habits (team agreement, not code):
  - one task per agent session and per branch; ask the agent for a plan first for anything over ~50 lines
  - read the diff before committing; reject diffs that touch unrelated files
  - when an agent repeats a mistake, add one line to `AGENTS.md` (or a check to CI), not a new doc

## Phase 1: Repository hygiene

- [ ] Ask each owner about the other unmerged branches before deleting them: `new-insta` (12 commits ahead of
      `main`), `taha`/`taha2` (5), `fix/robust-proxy-integrity` (4), `refactor-cire` (3), `server-live-snapshot` (1:
      check what was changed on the server), `telegram-send-button` (1). `refactor` (38) is not needed and can be
      deleted.
- [ ] Delete the remote branches already merged into `main`: `engine-merge`, `feature/*` (4), `fix-conflicts`,
      `javad-new-refactor`, `main-harmonized`, `new-javad`, `refactore-j`.
- [ ] Mark `docs/architecture/*` as outdated or delete it. Remove other docs that no longer match the code.
- [ ] Single test layout: tests are split between `pytest.ini` paths and `manage.py test apps`, and some
      `tests_*.py` files are in neither. Make one command run everything.

## Phase 2: Live map and dead code removal

- [ ] For every compose service, list the modules it really imports (`scripts/import_graph.py` can help).
- [ ] Confirm with the team what runs manually on the server (`workers/telegram_config_bot.py`,
      `xray/proxy_switcher.py`, ...).
- [ ] Remove, one PR per unit, following the `removing-dead-code` skill: `scripts/legacy/`, `xray/proxy_switcher.py`,
      `instagram_discovery/` and `workers/ig_collector/` (after moving the few pieces `apps/` imports), unused
      settings in `.env.example`, unused templates and static files.

## Phase 3: One Instagram stack

- [ ] Choose the owner (`instagram_engine/`, plus the Instagram code in `apps/discovery/`).
- [ ] Break up `run_instagram_scraper.py` (1,927 lines) and `apps/discovery/ig_accounts.py` (1,435 lines) into it.
- [ ] Consolidate proxy handling (currently in about 15 files) behind one module.

## Phase 4: Split the largest files

`templates/ops/manage_dashboard.html` (4,084 lines), `static/css/site.css` / `panel.css`,
`apps/billing/manage_views.py` (2,204 lines), `templates/accounts/outreach.html`,
`apps/discovery/outreach_service.py`, `workers/telegram_config_bot.py` (if kept). Split by responsibility: views per
page, services, template partials with `{% include %}`. Behavior must not change; tests first.

## Phase 5: Error handling and bugs

- [ ] Work down the ~413 broad `except Exception` blocks: narrow them, or log with `log.exception` at process
      boundaries.
- [ ] Every bug fixed from here on starts with a failing test (`systematic-debugging` skill).
