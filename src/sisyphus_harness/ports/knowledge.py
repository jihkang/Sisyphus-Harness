from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from ..contracts.knowledge import (
    DependencyEdgeWriteResult,
    KnowledgeEdge,
    KnowledgeNode,
)


@runtime_checkable
class KnowledgeIndexPort(Protocol):
    """Derived index only; this port has no grounding or task authority."""

    def initialize(self) -> None:
        ...

    def put_node(self, node: KnowledgeNode) -> bool:
        ...

    def put_edge(self, edge: KnowledgeEdge) -> bool:
        ...

    def put_dependency_edge(
        self,
        edge: KnowledgeEdge,
    ) -> DependencyEdgeWriteResult:
        """Atomically reject a dependency edge that would create a cycle."""
        ...

    def get_node(self, node_id: str) -> KnowledgeNode | None:
        ...

    def list_nodes(self) -> tuple[KnowledgeNode, ...]:
        ...

    def edges_for(self, node_id: str) -> tuple[KnowledgeEdge, ...]:
        ...

    def term_scores(
        self,
        terms: tuple[str, ...],
        node_ids: tuple[str, ...],
    ) -> Mapping[str, tuple[tuple[str, int], ...]]:
        ...

    def revision_digest(self) -> str:
        ...
