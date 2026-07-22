from __future__ import annotations

from pathlib import Path

from ..contracts.knowledge import (
    DependencyEdgeWriteResult,
    KnowledgeEdge,
    KnowledgeNode,
)
from .knowledge_database import (
    KNOWLEDGE_INDEX_SCHEMA_VERSION,
    SQLiteKnowledgeDatabase,
)
from .knowledge_index_errors import KnowledgeIndexConflict, KnowledgeIndexError
from .knowledge_projection import SQLiteKnowledgeProjection
from .knowledge_queries import SQLiteKnowledgeQueries


class SQLiteKnowledgeIndex(SQLiteKnowledgeDatabase):
    """Rebuildable lexical/graph projection with no task authority."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self._projection = SQLiteKnowledgeProjection(self)
        self._queries = SQLiteKnowledgeQueries(self)

    def put_node(self, node: KnowledgeNode) -> bool:
        return self._projection.put_node(node)

    def put_edge(self, edge: KnowledgeEdge) -> bool:
        return self._projection.put_edge(edge)

    def put_dependency_edge(
        self,
        edge: KnowledgeEdge,
    ) -> DependencyEdgeWriteResult:
        return self._projection.put_dependency_edge(edge)

    def get_node(self, node_id: str) -> KnowledgeNode | None:
        return self._queries.get_node(node_id)

    def list_nodes(self) -> tuple[KnowledgeNode, ...]:
        return self._queries.list_nodes()

    def edges_for(self, node_id: str) -> tuple[KnowledgeEdge, ...]:
        return self._queries.edges_for(node_id)

    def term_scores(
        self,
        terms: tuple[str, ...],
        node_ids: tuple[str, ...],
    ) -> dict[str, tuple[tuple[str, int], ...]]:
        return self._queries.term_scores(terms, node_ids)

    def revision_digest(self) -> str:
        return self._queries.revision_digest()


__all__ = [
    "KNOWLEDGE_INDEX_SCHEMA_VERSION",
    "KnowledgeIndexConflict",
    "KnowledgeIndexError",
    "SQLiteKnowledgeIndex",
]
