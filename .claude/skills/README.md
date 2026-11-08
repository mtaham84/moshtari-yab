# Agent skills

| Skill | Source |
|---|---|
| `systematic-debugging` | [obra/superpowers](https://github.com/obra/superpowers) @ `22f0b46` (MIT, see `LICENSE-superpowers`) |
| `test-driven-development` | obra/superpowers @ `22f0b46` |
| `verification-before-completion` | obra/superpowers @ `22f0b46` |
| `removing-dead-code` | written for this repository |

Local changes to the vendored skills: removed the `superpowers:` prefix from cross-references, replaced `npm test`
commands with `python -m pytest`, and removed the JS/TS-only helper files (`find-polluter.sh`,
`condition-based-waiting-example.ts`) and the references to them.

To update a vendored skill, copy it again from upstream and reapply these changes. Add a new skill only when agents
keep making the same mistake and a line in `AGENTS.md` is not enough.
