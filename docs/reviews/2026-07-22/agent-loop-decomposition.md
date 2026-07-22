# Local Coding Agent Decomposition Review

- Review date: 2026-07-22
- Base revision: `main@fbe3adf`
- Reviewed state: working branch before delivery commit
- Review scope: `LocalCodingAgent` responsibility decomposition
- Delivery status: local validation complete; PR/CI/merge pending

## Findings

No unresolved High finding or observed behavioral regression remains in the
reviewed change.

| Severity | Status | Finding | Disposition |
| --- | --- | --- | --- |
| Medium | Open debt | Agent still runs in the shared distribution and has no authenticated process transport or Compose parity proof. | `SH-ARCH-002` remains `AMBER`; this PR changes internal ownership only. |
| Low | Resolved | The first AST guard checked only direct `import` statements and collaborator file contents, so a `from datetime import ...` reintroduction or unused extraction could evade the intended rule. | The final guard evaluates absolute import roots, requires facade collaborator imports, forbids a loop in `run()`, and caps all component classes. |
| Low | Accepted compatibility | The facade retains `_messages()` because the existing prompt characterization test calls that private method directly. | Keep the thin delegate until an explicit compatibility migration moves the test and inventories external consumers. |
| Low | Open test debt | `tests/test_agent.py` remains a large facade suite. | Keep under `SH-TEST-001`; split tests by prompt, transition, and artifact responsibility in a later PR without weakening facade characterization. |

## Responsibility Result

| Component | Class lines | Owned responsibility |
| --- | ---: | --- |
| `LocalCodingAgent` | 119 | public validation, deadline and collaborator composition, prompt compatibility delegate |
| `AgentRunLoop` | 180 | provider/protocol loop, budgets, compaction timing, ordered dispatch |
| `AgentPromptRenderer` | 71 | deterministic safety and task context messages |
| `AgentRunState` | 130 | counters, compaction, fingerprints, criterion-aware cycle state |
| `AgentToolTransitionHandler` | 80 | tool dispatch, mutation consistency, file state, cycle termination |
| `AgentVerificationTransitionHandler` | 118 | final/intermediate verification, evidence references, criterion updates |
| `AgentRunRecorder` | 100 | metadata, step, compaction, and final artifact projection |

The previous 738-line facade and 569-line `run()` no longer own event JSON,
compaction mechanics, provider parsing branches, tool execution, verifier
transitions, workspace-cycle state, or result serialization. The architecture
guard caps the facade and every extracted class and forbids moved methods and
serialization imports from returning.

## Behavioral Equivalence

The existing facade tests were kept unchanged. They exercise prompt JSON and
verbatim file content, successful repair, repeated failed finish, bounded
protocol and provider errors, semantic decision stagnation, workspace cycles,
global deadlines, intermediate verification, verifier mutation, write scopes,
and persisted step/result artifacts. Public `AgentTask`, `AgentResult`,
`LocalCodingAgent`, constructor, and `run()` imports and signatures remain
unchanged.

Local validation evidence:

- all 444 tests passed with three explicit Docker integration skips;
- total branch coverage was 90.5%; the facade, recorder, loop, state, and
  transition modules measured 100%, 100%, 97.2%, 93.4%, and 89.4% respectively;
- AST comparison with `main@fbe3adf` confirmed equal public signatures and all
  non-empty operational strings from the original implementation;
- a deterministic successful repair produced equal parsed result, metadata,
  step, compaction, request, receipt, and stream artifacts across 11 files after
  normalizing timestamps, durations, temporary roots, and their derived digests;
- Ruff and Bandit passed with zero medium/high findings;
- lock, compileall, documentation links, Compose parsing, historical manifest,
  GEPA, and `git diff --check` passed;
- offline source/wheel build, isolated Python 3.14 install, installed Agent
  imports, and CLI startup passed;
- a source-rebuilt verifier image passed all three real-Docker boundary probes.

Current-head CI, PR, and merge identifiers remain pending delivery evidence.

## Residual Risk

This is an internal structural refactor. It does not create an Agent service
transport, alter verifier containment, improve hidden-oracle confidentiality,
or make Agent success authoritative. Those boundaries remain governed by
`SH-ARCH-002`, `SH-ORACLE-001`, and Control-owned outcome evaluation.
