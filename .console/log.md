# Log

## 2026-05-21 — Gitignore .console/.context

`.console/.context` is regenerated on every session launch (timestamp changes unconditionally). Added it to `.gitignore` and untracked it with `git rm --cached`. All other repos (OC, RxP, etc.) already gitignore it; CLP was the only outlier. Source truth remains `.console/{task,guidelines,backlog,log}.md`.

## 2026-05-21 — Add closing fence to console-context block

Added <!-- /console-context --> end marker so OperatorConsole only replaces its
managed block and leaves repo-owned content below it untouched.

_Chronological continuity log. Decisions, stop points, what changed and why._
_Not a task tracker — that's backlog.md. Keep entries concise and dated._

## Recent Decisions

_Log significant choices here so they survive context resets._

| Decision | Rationale | Date |
|----------|-----------|------|
| [what was decided] | [why] | [date] |

## Stop Points

_Where did you leave off? What should be verified next session?_

- [what to pick up next]

## Notes

_Free-form scratch. Clear periodically — old entries can be deleted once no longer relevant._

---
