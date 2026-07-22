from __future__ import annotations

from .contracts.knowledge import (
    DependencyInspection,
    KnowledgeEdge,
    KnowledgeNode,
    KnowledgeSearchHit,
    MAX_GRAPH_TRAVERSAL_DEPTH,
    NextStepContext,
)
from .knowledge_dependencies import KnowledgeDependencyService
from .knowledge_graph_errors import KnowledgeGraphError
from .knowledge_mutations import KnowledgeMutationService
from .knowledge_planning import KnowledgePlanningService
from .knowledge_search import KnowledgeSearchService
from .ports.knowledge import KnowledgeIndexPort


_KnowledgeIndex = KnowledgeIndexPort
_MAX_GRAPH_EXPANSION_NODES = 10_000
_MAX_NEXT_STEP_CANDIDATES = 1_000
_MAX_DEPENDENCY_STATES = 1_000


class KnowledgeGraph:
    """GraphRAG decision support over a rebuildable, non-authoritative index."""

    def __init__(self, index: _KnowledgeIndex) -> None:
        self.index = index

    def add_node(self, node: KnowledgeNode) -> bool:
        return KnowledgeMutationService(self.index).add_node(node)

    def add_edge(self, edge: KnowledgeEdge) -> bool:
        return KnowledgeMutationService(self.index).add_edge(edge)

    def search(
        self,
        anchor_id: str,
        query: str,
        *,
        max_depth: int = MAX_GRAPH_TRAVERSAL_DEPTH,
        limit: int = 20,
    ) -> tuple[KnowledgeSearchHit, ...]:
        return KnowledgeSearchService(
            self.index,
            max_graph_expansion_nodes=_MAX_GRAPH_EXPANSION_NODES,
            max_dependency_states=_MAX_DEPENDENCY_STATES,
        ).search(anchor_id, query, max_depth=max_depth, limit=limit)

    def inspect_dependencies(
        self,
        task_id: str,
        *,
        max_depth: int = MAX_GRAPH_TRAVERSAL_DEPTH,
    ) -> DependencyInspection:
        return KnowledgeDependencyService(
            self.index,
            max_graph_expansion_nodes=_MAX_GRAPH_EXPANSION_NODES,
            max_dependency_states=_MAX_DEPENDENCY_STATES,
        ).inspect_dependencies(task_id, max_depth=max_depth)

    def next_step_context(
        self,
        anchor_id: str,
        query: str | None = None,
        *,
        max_depth: int = MAX_GRAPH_TRAVERSAL_DEPTH,
        dependency_max_depth: int = MAX_GRAPH_TRAVERSAL_DEPTH,
        limit: int = 20,
    ) -> NextStepContext:
        return KnowledgePlanningService(
            self.index,
            max_graph_expansion_nodes=_MAX_GRAPH_EXPANSION_NODES,
            max_next_step_candidates=_MAX_NEXT_STEP_CANDIDATES,
            max_dependency_states=_MAX_DEPENDENCY_STATES,
        ).next_step_context(
            anchor_id,
            query,
            max_depth=max_depth,
            dependency_max_depth=dependency_max_depth,
            limit=limit,
        )


__all__ = ["KnowledgeGraph", "KnowledgeGraphError"]
