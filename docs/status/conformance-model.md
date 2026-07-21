# Canonical Conformance Model

Conformance is reported with a stable token and a human-readable label. Color is
never the only signal, and these labels do not replace the runtime
`passed | failed | indeterminate` values of a `TaskOutcome`.

| Token | Label | Meaning | Minimum evidence |
| --- | --- | --- | --- |
| `GREEN` | Conformant | The required invariant exists on the evaluated revision and every declared gate passes. | implementation reference, regression test, and any required current-head CI or external proof |
| `AMBER` | Partial | The boundary is implemented only in part, or implementation exists but a declared proof or delivery gate is pending. | exact implemented subset, exact missing proof, and owner/exit condition |
| `RED` | Non-conformant | A required invariant is absent, contradicted, or known to be bypassable. | reproducible fact or source reference plus a fail-closed remediation gate |
| `GRAY` | Not evaluated | The item is outside the evaluated scope, optional future work, or lacks enough evidence for a claim. | scope statement; never interpreted as pass |

## Decision Rules

1. Evaluate one named invariant at one source revision. Do not average unrelated
   boundaries into a single project color.
2. `GREEN` requires all conjunctive gates. Local tests cannot substitute for a
   required Docker, model, CI, merge, or repository-setting check.
3. A target described only by an ADR or plan is `RED` when it is a currently
   required invariant, and `GRAY` when it is explicitly outside current scope.
4. `AMBER` must name the missing part. It is not a softer synonym for unknown.
5. Any bypass that lets Agent or Worker grant semantic success makes the task
   outcome authority invariant `RED`, regardless of unrelated passing tests.
6. Historical evidence is evaluated against its pinned source revision. It
   cannot make a later release `GREEN`.
7. Status changes require a linked code/test/evidence change. Prose alone cannot
   promote a status.

The active applications of this model live in the
[implementation debt register](implementation-debt.md) and dated review finding
tables.
