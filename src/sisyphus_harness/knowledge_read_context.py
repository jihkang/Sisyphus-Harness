from __future__ import annotations

from dataclasses import dataclass

from .contracts.knowledge import (
    DEPENDENCY_EDGE_TYPES,
    GraphPathStep,
    KnowledgeEdge,
    KnowledgeNode,
)
from .knowledge_graph_errors import KnowledgeGraphError
from .ports.knowledge import KnowledgeIndexPort


@dataclass(frozen=True, slots=True)
class GraphPath:
    depth: int
    node_ids: tuple[str, ...]
    steps: tuple[GraphPathStep, ...]

    @property
    def deterministic_key(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return (
            self.node_ids,
            tuple(
                f"{step.edge_type.value}:{int(step.traversed_forward)}:"
                f"{step.edge_digest}"
                for step in self.steps
            ),
        )


class KnowledgeReadContext:
    """One revision fence and cache set shared by a graph read operation."""

    def __init__(
        self,
        index: KnowledgeIndexPort,
        *,
        max_graph_expansion_nodes: int,
        max_dependency_states: int,
    ) -> None:
        self.index = index
        self.max_graph_expansion_nodes = max_graph_expansion_nodes
        self.max_dependency_states = max_dependency_states
        self.revision = index.revision_digest()
        self.node_cache: dict[str, KnowledgeNode] = {}
        self.edge_cache: dict[str, tuple[KnowledgeEdge, ...]] = {}

    def required_node(self, node_id: str) -> KnowledgeNode:
        node = self.node_cache.get(node_id)
        if node is None:
            node = self.index.get_node(node_id)
            if node is None:
                raise KnowledgeGraphError(
                    f"knowledge node {node_id!r} does not exist"
                )
            self.node_cache[node_id] = node
        return node

    def reachable_paths(
        self,
        anchor_id: str,
        *,
        max_depth: int,
    ) -> dict[str, GraphPath]:
        paths = {anchor_id: GraphPath(0, (anchor_id,), ())}
        frontier = (anchor_id,)
        for depth in range(1, max_depth + 1):
            proposals: dict[str, GraphPath] = {}
            for current_id in sorted(
                frontier,
                key=lambda node_id: paths[node_id].deterministic_key,
            ):
                current_path = paths[current_id]
                for other_id, step in self._neighbor_steps(current_id):
                    if other_id in paths:
                        continue
                    candidate = GraphPath(
                        depth=depth,
                        node_ids=current_path.node_ids + (other_id,),
                        steps=current_path.steps + (step,),
                    )
                    existing = proposals.get(other_id)
                    if (
                        existing is None
                        and len(paths) + len(proposals)
                        >= self.max_graph_expansion_nodes
                    ):
                        raise KnowledgeGraphError(
                            "graph traversal expansion exceeds the supported limit"
                        )
                    if (
                        existing is None
                        or candidate.deterministic_key < existing.deterministic_key
                    ):
                        proposals[other_id] = candidate
            if not proposals:
                break
            for node_id, path in sorted(proposals.items()):
                paths[node_id] = path
            frontier = tuple(sorted(proposals))
        return paths

    def dependency_paths(
        self,
        task_id: str,
        *,
        max_depth: int,
    ) -> tuple[dict[str, GraphPath], bool]:
        visited = {task_id}
        dependency_paths: dict[str, GraphPath] = {}
        frontier = {task_id: GraphPath(0, (task_id,), ())}
        truncated = False
        for depth in range(1, max_depth + 1):
            proposals: dict[str, GraphPath] = {}
            for current_id, current_path in sorted(frontier.items()):
                for target_id, step in self._outgoing_dependency_steps(current_id):
                    if target_id in visited:
                        continue
                    candidate = GraphPath(
                        depth=depth,
                        node_ids=current_path.node_ids + (target_id,),
                        steps=current_path.steps + (step,),
                    )
                    existing = proposals.get(target_id)
                    if (
                        existing is None
                        and len(dependency_paths) + len(proposals)
                        >= self.max_dependency_states
                    ):
                        raise KnowledgeGraphError(
                            "dependency expansion exceeds the supported limit"
                        )
                    if (
                        existing is None
                        or candidate.deterministic_key < existing.deterministic_key
                    ):
                        proposals[target_id] = candidate
            if not proposals:
                frontier = {}
                break
            for node_id, path in sorted(proposals.items()):
                dependency_paths[node_id] = path
                visited.add(node_id)
            frontier = proposals

        if frontier:
            truncated = any(
                target_id not in visited
                for current_id in frontier
                for target_id, _step in self._outgoing_dependency_steps(current_id)
            )
        return dependency_paths, truncated

    def require_stable_revision(self, *, operation: str) -> None:
        if self.index.revision_digest() != self.revision:
            raise KnowledgeGraphError(
                f"knowledge index changed during {operation}; retry from one revision"
            )

    def _neighbor_steps(
        self,
        node_id: str,
    ) -> tuple[tuple[str, GraphPathStep], ...]:
        neighbors: list[tuple[str, GraphPathStep]] = []
        for edge in self._edges_for(node_id):
            traversed_forward = edge.source_node_id == node_id
            other_id = (
                edge.target_node_id if traversed_forward else edge.source_node_id
            )
            neighbors.append(
                (
                    other_id,
                    GraphPathStep(
                        source_node_id=edge.source_node_id,
                        target_node_id=edge.target_node_id,
                        edge_type=edge.edge_type,
                        edge_digest=edge.edge_digest,
                        traversed_forward=traversed_forward,
                    ),
                )
            )
        neighbors.sort(
            key=lambda item: (
                item[0],
                item[1].edge_type.value,
                not item[1].traversed_forward,
                item[1].edge_digest,
            )
        )
        return tuple(neighbors)

    def _outgoing_dependency_steps(
        self,
        node_id: str,
    ) -> tuple[tuple[str, GraphPathStep], ...]:
        results: list[tuple[str, GraphPathStep]] = []
        for edge in self._edges_for(node_id):
            if (
                edge.source_node_id != node_id
                or edge.edge_type not in DEPENDENCY_EDGE_TYPES
            ):
                continue
            results.append(
                (
                    edge.target_node_id,
                    GraphPathStep(
                        source_node_id=edge.source_node_id,
                        target_node_id=edge.target_node_id,
                        edge_type=edge.edge_type,
                        edge_digest=edge.edge_digest,
                        traversed_forward=True,
                    ),
                )
            )
        results.sort(
            key=lambda item: (
                item[0],
                item[1].edge_type.value,
                item[1].edge_digest,
            )
        )
        return tuple(results)

    def _edges_for(self, node_id: str) -> tuple[KnowledgeEdge, ...]:
        edges = self.edge_cache.get(node_id)
        if edges is None:
            edges = self.index.edges_for(node_id)
            self.edge_cache[node_id] = edges
        return edges


def validate_result_limit(limit: int) -> None:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 1000
    ):
        raise ValueError("knowledge result limit must be between 1 and 1000")
