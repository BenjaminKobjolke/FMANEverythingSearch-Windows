# Version
3

# Coding Rules (Pointer)

This project's rules live in two files in the project root:

- `CODING_RULES.md` — code quality and conventions. BINDING for all code work in
  this repository, always.
- `IMPLEMENTATION_FLOW.md` — the end-to-end flow to follow when planning and
  implementing a change (checks, gates, Definition of Done).

MANDATORY: Before writing or editing ANY code, you MUST Read BOTH files in full
**in the current session**. Do not rely on memory of a previous session, a
summary, or partial reads.

If you are about to make a code change and have not read both files in this
session: STOP, read them, then continue.

An external tool may orchestrate the implementation itself. When a skill or run
states that the implementation is orchestrated, ignore `IMPLEMENTATION_FLOW.md`
entirely — the orchestrator runs those steps as its own phases. `CODING_RULES.md`
stays binding either way.

Next to `CODING_RULES.md`, apply also writes `CODING_RULES_PLAN.md`,
`CODING_RULES_REVIEW.md` and `CODING_RULES_CODE.md`. When an orchestrator's run
instructions name one of these step files, that file replaces `CODING_RULES.md`
for that run; without such an instruction, `CODING_RULES.md` stays binding in full.

Do not inline rules back into this file and do not use `@import` for either file —
they are intentionally referenced, not imported.


# graphify

This project's code directory is `.` (flat layout: `everything/` and `check_everything.py` in the root). The root `.graphifyignore` keeps non-code files out of the graph.
