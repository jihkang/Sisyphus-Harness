from __future__ import annotations

from .contracts.knowledge import (
    DEPENDENCY_EDGE_TYPES,
    DependencyEdgeWriteResult,
    KnowledgeEdge,
    KnowledgeNode,
    KnowledgeNodeType,
)
from .knowledge_graph_errors import KnowledgeGraphError
from .ports.knowledge import KnowledgeIndexPort


class KnowledgeMutationService:
    def __init__(self, index: KnowledgeIndexPort) -> None:
        self.index = index

    def add_node(self, node: KnowledgeNode) -> bool:
        if type(node) is not KnowledgeNode:
            raise TypeError("knowledge graph requires an exact KnowledgeNode")
        return self.index.put_node(node)

    def add_edge(self, edge: KnowledgeEdge) -> bool:
        if type(edge) is not KnowledgeEdge:
            raise TypeError("knowledge graph requires an exact KnowledgeEdge")
        source = self._required_node(edge.source_node_id)
        target = self._required_node(edge.target_node_id)
        if edge.edge_type in DEPENDENCY_EDGE_TYPES:
            if (
                source.node_type is not KnowledgeNodeType.TASK
                or target.node_type is not KnowledgeNodeType.TASK
            ):
                raise KnowledgeGraphError(
                    "dependency edges must connect task candidate nodes"
                )
            result = self.index.put_dependency_edge(edge)
            if result is DependencyEdgeWriteResult.CYCLE:
                raise KnowledgeGraphError("dependency edge would create a cycle")
            return result is DependencyEdgeWriteResult.INSERTED
        return self.index.put_edge(edge)

    def _required_node(self, node_id: str) -> KnowledgeNode:
        node = self.index.get_node(node_id)
        if node is None:
            raise KnowledgeGraphError(f"knowledge node {node_id!r} does not exist")
        return node
