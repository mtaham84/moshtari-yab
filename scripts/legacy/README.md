# Legacy one-off scripts

Moved here from the repository root (DEAD-01). Nothing in the app, compose files or entrypoint runs them;
they are kept only for manual use and may be deleted once nobody needs them.

Run from the repository root so `config.settings` and `apps` are importable:

```bash
PYTHONPATH=. python scripts/legacy/<script>.py
```
