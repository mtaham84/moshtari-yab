---
name: removing-dead-code
description: Use before deleting any module, package, function, class, template, management command, setting or docker service in this repository - proves the code is unused, removes it completely, and verifies nothing broke
---

# Removing Dead Code (moshtari-yab)

## Core principle

Delete only what you have proven is unused, then delete all of it. A half-removed feature (code gone, setting still in
`.env.example`, template still on disk, test still importing it) is worse than either state.

## Step 1: Prove it is unused

Start with the reachability map. It follows imports from every docker-compose/entrypoint command, Django's
auto-loaded modules, prompt `module_path` strings and the tools in `MANUAL_ROOTS` (run outside compose, e.g. systemd):

```bash
python scripts/find_unreachable.py            # unreachable modules per package
```

A module it lists is a candidate, not proven dead. Static imports are not enough in this repo. Check every way code
can be reached:

```bash
NAME=instagram_discovery        # module path, function name, template name, setting name, ...

# 1. Python imports and string references (dynamic imports, Celery task names, settings)
grep -rn "$NAME" --include=*.py . | grep -v "/migrations/"
# 2. Process entry points
grep -n "$NAME" docker-compose.yml docker/entrypoint.sh Dockerfile config/celery.py
# 3. Django: URLs, templates, template tags, admin, signals, INSTALLED_APPS
grep -rn "$NAME" config/ apps/*/urls.py templates/ apps/*/templatetags/ apps/*/apps.py
# 4. Config, CI, scripts, docs
grep -rn "$NAME" .env.example pytest.ini pyproject.toml .github/ scripts/ README.md
```

For a management command, also search for its command name (file name without `.py`) in compose, the entrypoint,
celery beat schedules and `scripts/`.

Classify every hit:
- Hit only inside the thing itself or its own tests: unused.
- Hit in production code that is itself unused: delete both, leaves first.
- Hit in a live entry point (see the table in `AGENTS.md`): **not dead.** Stop, or plan a migration first.
- Cannot tell (it may run manually on the server, through cron, or as an operator script): **ask the user.** Don't
  guess.

## Step 2: Delete completely, in one commit per unit

Remove, in the same commit:
- the code and its tests (also drop the path from `pytest.ini` `testpaths`)
- settings it reads from `.env.example`, `config/settings*.py` and `docker-compose.yml`
- templates, static files and URL routes used only by it
- README and doc sections describing it
- requirements it alone used (confirm with grep that nothing else imports the package)

Do not delete Django models or migrations as dead code. Removing a model needs a new migration and a data decision;
ask the user first.

## Step 3: Verify

```bash
ruff check .
python -m pytest
python manage.py test apps
python manage.py check
python -c "import <each remaining top-level package>"
```

All must pass with a real database (`REQUIRE_DB=true`), or the DB tests are skipped and prove nothing. Report the
actual output. Follow `verification-before-completion`.

## Step 4: Commit

`refactor(cleanup): remove <thing> (unused: <one-line evidence>)`. List in the body what you checked in Step 1.

## Red flags

- "It's probably unused" without running the Step 1 searches.
- Deleting several unrelated units in one commit (hard to revert one).
- Keeping a stub, re-export or `# deprecated` shim "for compatibility". Nothing outside this repo imports it.
- Tests are failing and you delete or skip them to make the removal pass.
