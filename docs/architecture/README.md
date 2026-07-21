# Structured Architecture Map

This directory is the navigable architecture view for Sisyphus Harness. It
splits the system by authority and responsibility instead of mirroring the
current Python package layout. Runtime code and executable tests remain the
behavioral authority.

The longer [architecture and data pipeline](../architecture-and-data-pipeline.md)
document remains the compatibility narrative. These documents provide smaller
ownership views and must not turn target-state design into a current claim.

## Service Boundaries

| Boundary | Owns | Detailed view |
| --- | --- | --- |
| Agent | model interaction, bounded workspace actions, compaction, attempt diagnostics | [Agent](components/agent.md) |
| Verifier | command execution, immutable observations, verification receipts | [Verifier](components/verifier.md) |
| Evolve | candidate proposal, rollout coordination, train/holdout comparison | [Evolve](components/evolve.md) |
| Control | queue authority, attempt admission, task outcomes, approval, active policy | [Control](components/control.md) |

The boundaries are logical today. Agent, Evolve, and Control still share a
Python distribution and several composition roots. Verifier has a
host-orchestrated per-command Docker path, while standalone Compose and trusted
compatibility paths retain the service executor. A logical
boundary is not evidence of process, host, or identity isolation.

## Cross-Cutting Views

- [Trust and artifact boundaries](trust-and-artifacts.md) records who may create,
  read, and publish each authoritative artifact and which digest bindings are
  implemented or missing.
- [Data pipelines](data-pipelines.md) follows direct runs, queued attempts,
  Control adjudication, benchmark evaluation, and evolution without assigning
  authority to transport completion.
- [Accepted decisions](../adr/) records durable design decisions.
- [Implementation debt](../status/implementation-debt.md) records current gaps
  with stable IDs and executable exit conditions.

## Reading Contract

Each component document uses the same sections:

1. responsibility;
2. owned and forbidden authority;
3. current implementation;
4. inbound and outbound contracts;
5. target boundary;
6. open debt and executable evidence.

`Current implementation` describes code that exists. `Target boundary`
describes unimplemented work. A target becomes current only when its debt item
has current-revision test and delivery evidence.

## Conformance Crosswalk

| Boundary | Current status | Primary open debt |
| --- | --- | --- |
| Agent | `AMBER` Partial | `SH-ARCH-002`, `SH-IO-001` |
| Verifier | `AMBER` Host-owned evidence implemented locally; CI/merge pending, confidentiality unclaimed | `SH-VERIFY-001`, `SH-VERIFY-002`, `SH-ORACLE-001` |
| Evolve | `AMBER` Partial | `SH-EVOLVE-001`, `SH-BENCH-001`, `SH-EVIDENCE-001` |
| Control | `AMBER` Partial | `SH-CTRL-001`, `SH-GRAPH-001`, `SH-TRUST-001` |

The token meanings and promotion rules are defined only in the
[canonical conformance model](../status/conformance-model.md).
