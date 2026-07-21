# Current Project Status

This directory contains living status documents. It is intentionally separate
from dated reviews, accepted architecture decisions, and delivery plans:

- [Conformance model](conformance-model.md) defines the canonical status labels
  and the proof required to use them.
- [Implementation debt](implementation-debt.md) is the active, ID-based register
  of known implementation gaps and their exit conditions.

## Authority Order

When documents disagree, use this order:

1. runtime code and executable regression tests;
2. accepted ADRs for intended authority boundaries;
3. this living status register for open implementation work;
4. dated reviews for facts observed at their pinned revision;
5. delivery plans for unimplemented target design.

A plan checkbox or an accepted ADR does not make an implementation conformant.
Likewise, passing local tests do not close a delivery gate that explicitly
requires current-head CI, an external service, or repository settings.

## Update Contract

Every pull request that closes, splits, or discovers implementation debt must
update the debt register in the same change. A debt item is removed only after
its exit condition has current-revision evidence; closed items move to the
register's closure log so their IDs are never reused.
