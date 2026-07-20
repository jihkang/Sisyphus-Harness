from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .contracts.knowledge import (
    CandidateTaskStatus,
    DEPENDENCY_EDGE_TYPES,
    DependencyEdgeWriteResult,
    DependencyInspection,
    DependencyState,
    GraphPathStep,
    KnowledgeEdge,
    KnowledgeNode,
    KnowledgeNodeType,
    KnowledgeSearchHit,
    MAX_GRAPH_TRAVERSAL_DEPTH,
    NextStepCandidate,
    NextStepContext,
    knowledge_graph_score,
    knowledge_score_explanation,
    next_step_candidate_explanation,
    normalized_terms,
    validate_max_depth,
)


class _KnowledgeIndex(Protocol):
    """Structural type keeps the pure graph domain independent from port modules."""

    def put_node(self, node: KnowledgeNode) -> bool: ...

    def put_edge(self, edge: KnowledgeEdge) -> bool: ...

    def put_dependency_edge(
        self,
        edge: KnowledgeEdge,
    ) -> DependencyEdgeWriteResult: ...

    def get_node(self, node_id: str) -> KnowledgeNode | None: ...

    def edges_for(self, node_id: str) -> tuple[KnowledgeEdge, ...]: ...

    def term_scores(
        self,
        terms: tuple[str, ...],
        node_ids: tuple[str, ...],
    ) -> Mapping[str, tuple[tuple[str, int], ...]]: ...

    def revision_digest(self) -> str: ...


class KnowledgeGraphError(RuntimeError):
    pass


_MAX_GRAPH_EXPANSION_NODES = 10_000
_MAX_NEXT_STEP_CANDIDATES = 1_000
_MAX_DEPENDENCY_STATES = 1_000


@dataclass(frozen=True, slots=True)
class _Path:
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


class KnowledgeGraph:
    """GraphRAG decision support over a rebuildable, non-authoritative index."""

    def __init__(self, index: _KnowledgeIndex) -> None:
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

    def search(
        self,
        anchor_id: str,
        query: str,
        *,
        max_depth: int = MAX_GRAPH_TRAVERSAL_DEPTH,
        limit: int = 20,
    ) -> tuple[KnowledgeSearchHit, ...]:
        """Return lexical matches restricted to the anchor's graph neighborhood."""

        validate_max_depth(max_depth)
        _validate_limit(limit)
        node_cache: dict[str, KnowledgeNode] = {}
        edge_cache: dict[str, tuple[KnowledgeEdge, ...]] = {}
        revision = self.index.revision_digest()
        self._required_node(anchor_id, cache=node_cache)
        query_terms = normalized_terms(query)
        if not query_terms:
            raise ValueError("knowledge search query must contain at least one term")
        paths = self._reachable_paths(
            anchor_id,
            max_depth=max_depth,
            edge_cache=edge_cache,
        )
        candidate_ids = tuple(sorted(set(paths).difference({anchor_id})))
        scores = self.index.term_scores(query_terms, candidate_ids)
        hits: list[KnowledgeSearchHit] = []
        for node_id, term_weights in scores.items():
            path = paths[node_id]
            node = self._required_node(node_id, cache=node_cache)
            matched_terms = tuple(term for term, _weight in term_weights)
            lexical_score = sum(weight for _term, weight in term_weights)
            graph_score = knowledge_graph_score(path.depth)
            total_score = lexical_score * 100 + graph_score
            hits.append(
                KnowledgeSearchHit(
                    anchor_id=anchor_id,
                    index_revision_digest=revision,
                    node=node,
                    depth=path.depth,
                    path_node_ids=path.node_ids,
                    path_steps=path.steps,
                    matched_terms=matched_terms,
                    lexical_score=lexical_score,
                    graph_score=graph_score,
                    total_score=total_score,
                    explanation=knowledge_score_explanation(
                        depth=path.depth,
                        path_steps=path.steps,
                        matched_terms=matched_terms,
                        lexical_score=lexical_score,
                        graph_score=graph_score,
                        total_score=total_score,
                    ),
                )
            )
        hits.sort(
            key=lambda hit: (
                -hit.total_score,
                hit.depth,
                hit.node.node_id,
                hit.node.node_digest,
            )
        )
        self._require_stable_revision(revision, operation="search")
        return tuple(hits[:limit])

    def inspect_dependencies(
        self,
        task_id: str,
        *,
        max_depth: int = MAX_GRAPH_TRAVERSAL_DEPTH,
    ) -> DependencyInspection:
        """Inspect candidate task dependencies without making a readiness decision."""

        validate_max_depth(max_depth)
        node_cache: dict[str, KnowledgeNode] = {}
        edge_cache: dict[str, tuple[KnowledgeEdge, ...]] = {}
        revision = self.index.revision_digest()
        inspection = self._dependency_inspection(
            task_id,
            max_depth=max_depth,
            revision=revision,
            node_cache=node_cache,
            edge_cache=edge_cache,
        )
        self._require_stable_revision(revision, operation="dependency inspection")
        return inspection

    def _dependency_inspection(
        self,
        task_id: str,
        *,
        max_depth: int,
        revision: str,
        node_cache: dict[str, KnowledgeNode],
        edge_cache: dict[str, tuple[KnowledgeEdge, ...]],
    ) -> DependencyInspection:
        task = self._required_node(task_id, cache=node_cache)
        if task.node_type is not KnowledgeNodeType.TASK:
            raise KnowledgeGraphError("dependency inspection requires a task node")

        paths, truncated = self._dependency_paths(
            task_id,
            max_depth=max_depth,
            edge_cache=edge_cache,
        )
        dependencies: list[DependencyState] = []
        for dependency_id, path in sorted(
            paths.items(), key=lambda item: (item[1].depth, item[0])
        ):
            dependency = self._required_node(dependency_id, cache=node_cache)
            status = dependency.task_status
            if status is None:
                raise KnowledgeGraphError("dependency target is not a task node")
            dependencies.append(
                DependencyState(
                    node_id=dependency.node_id,
                    task_status=status,
                    depth=path.depth,
                    path_node_ids=path.node_ids,
                    path_steps=path.steps,
                    satisfied=status is CandidateTaskStatus.COMPLETED,
                )
            )

        unmet_reasons = tuple(
            f"dependency {dependency.node_id} is {dependency.task_status.value}"
            for dependency in dependencies
            if not dependency.satisfied
        ) + (
            (f"dependency traversal truncated at depth {max_depth}",)
            if truncated
            else ()
        )
        return DependencyInspection(
            task_id=task_id,
            max_depth=max_depth,
            index_revision_digest=revision,
            dependencies=tuple(dependencies),
            all_satisfied=not unmet_reasons,
            truncated=truncated,
            unmet_reasons=unmet_reasons,
        )

    def next_step_context(
        self,
        anchor_id: str,
        query: str | None = None,
        *,
        max_depth: int = MAX_GRAPH_TRAVERSAL_DEPTH,
        dependency_max_depth: int = MAX_GRAPH_TRAVERSAL_DEPTH,
        limit: int = 20,
    ) -> NextStepContext:
        """Build deterministic decision data; it never admits or executes a task."""

        validate_max_depth(max_depth)
        validate_max_depth(dependency_max_depth)
        _validate_limit(limit)
        node_cache: dict[str, KnowledgeNode] = {}
        edge_cache: dict[str, tuple[KnowledgeEdge, ...]] = {}
        revision = self.index.revision_digest()
        anchor = self._required_node(anchor_id, cache=node_cache)
        query_source = (
            query
            if query is not None and query.strip()
            else f"{anchor.title} {anchor.content}"
        )
        query_terms = normalized_terms(query_source)[:128]
        paths = self._reachable_paths(
            anchor_id,
            max_depth=max_depth,
            edge_cache=edge_cache,
        )
        task_ids_list: list[str] = []
        for node_id in sorted(set(paths).difference({anchor_id})):
            if (
                self._required_node(node_id, cache=node_cache).node_type
                is KnowledgeNodeType.TASK
            ):
                if len(task_ids_list) >= _MAX_NEXT_STEP_CANDIDATES:
                    raise KnowledgeGraphError(
                        "next-step candidate expansion exceeds the supported limit"
                    )
                task_ids_list.append(node_id)
        task_ids = tuple(task_ids_list)
        term_scores = self.index.term_scores(query_terms, task_ids)
        candidate_drafts: list[tuple[tuple[object, ...], dict[str, object]]] = []
        for task_id in task_ids:
            task = self._required_node(task_id, cache=node_cache)
            path = paths[task_id]
            weighted_terms = term_scores.get(task_id, ())
            matched_terms = tuple(term for term, _weight in weighted_terms)
            lexical_score = sum(weight for _term, weight in weighted_terms)
            graph_score = knowledge_graph_score(path.depth)
            total_score = lexical_score * 100 + graph_score
            inspection = self._dependency_inspection(
                task_id,
                max_depth=dependency_max_depth,
                revision=revision,
                node_cache=node_cache,
                edge_cache=edge_cache,
            )
            status_reasons = (
                ()
                if task.task_status is CandidateTaskStatus.READY
                else (f"task status is {task.task_status.value}",)
            )
            unmet_reasons = status_reasons + inspection.unmet_reasons
            eligible = not unmet_reasons
            candidate_fields: dict[str, object] = {
                "anchor_id": anchor_id,
                "task": task,
                "depth": path.depth,
                "path_node_ids": path.node_ids,
                "path_steps": path.steps,
                "matched_terms": matched_terms,
                "lexical_score": lexical_score,
                "graph_score": graph_score,
                "total_score": total_score,
                "dependency_inspection": inspection,
                "eligible": eligible,
                "unmet_dependency_reasons": unmet_reasons,
                "explanation": next_step_candidate_explanation(
                    task=task,
                    depth=path.depth,
                    path_steps=path.steps,
                    matched_terms=matched_terms,
                    lexical_score=lexical_score,
                    graph_score=graph_score,
                    total_score=total_score,
                    eligible=eligible,
                    unmet_reasons=unmet_reasons,
                ),
            }
            candidate_drafts.append(
                (
                    (
                        not eligible,
                        -total_score,
                        path.depth,
                        task.node_id,
                        task.node_digest,
                    ),
                    candidate_fields,
                )
            )
        candidate_drafts.sort(key=lambda item: item[0])
        ranked = tuple(
            NextStepCandidate(rank=rank, **fields)  # type: ignore[arg-type]
            for rank, (_sort_key, fields) in enumerate(
                candidate_drafts[:limit],
                start=1,
            )
        )
        context = NextStepContext(
            anchor_id=anchor_id,
            query_terms=query_terms,
            candidate_max_depth=max_depth,
            dependency_max_depth=dependency_max_depth,
            index_revision_digest=revision,
            candidates=ranked,
        )
        self._require_stable_revision(revision, operation="next-step planning")
        return context

    def _require_stable_revision(self, expected: str, *, operation: str) -> None:
        if self.index.revision_digest() != expected:
            raise KnowledgeGraphError(
                f"knowledge index changed during {operation}; retry from one revision"
            )

    def _required_node(
        self,
        node_id: str,
        *,
        cache: dict[str, KnowledgeNode] | None = None,
    ) -> KnowledgeNode:
        if cache is not None and node_id in cache:
            return cache[node_id]
        node = self.index.get_node(node_id)
        if node is None:
            raise KnowledgeGraphError(f"knowledge node {node_id!r} does not exist")
        if cache is not None:
            cache[node_id] = node
        return node

    def _reachable_paths(
        self,
        anchor_id: str,
        *,
        max_depth: int,
        edge_cache: dict[str, tuple[KnowledgeEdge, ...]],
    ) -> dict[str, _Path]:
        paths = {anchor_id: _Path(0, (anchor_id,), ())}
        frontier = (anchor_id,)
        for depth in range(1, max_depth + 1):
            proposals: dict[str, _Path] = {}
            for current_id in sorted(
                frontier,
                key=lambda node_id: paths[node_id].deterministic_key,
            ):
                current_path = paths[current_id]
                for other_id, step in self._neighbor_steps(
                    current_id,
                    edge_cache=edge_cache,
                ):
                    if other_id in paths:
                        continue
                    candidate = _Path(
                        depth=depth,
                        node_ids=current_path.node_ids + (other_id,),
                        steps=current_path.steps + (step,),
                    )
                    existing = proposals.get(other_id)
                    if (
                        existing is None
                        and len(paths) + len(proposals)
                        >= _MAX_GRAPH_EXPANSION_NODES
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

    def _neighbor_steps(
        self,
        node_id: str,
        *,
        edge_cache: dict[str, tuple[KnowledgeEdge, ...]],
    ) -> tuple[tuple[str, GraphPathStep], ...]:
        neighbors: list[tuple[str, GraphPathStep]] = []
        for edge in self._edges_for(node_id, cache=edge_cache):
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

    def _dependency_paths(
        self,
        task_id: str,
        *,
        max_depth: int,
        edge_cache: dict[str, tuple[KnowledgeEdge, ...]],
    ) -> tuple[dict[str, _Path], bool]:
        visited = {task_id}
        dependency_paths: dict[str, _Path] = {}
        frontier = {task_id: _Path(0, (task_id,), ())}
        truncated = False
        for depth in range(1, max_depth + 1):
            proposals: dict[str, _Path] = {}
            for current_id, current_path in sorted(frontier.items()):
                for target_id, step in self._outgoing_dependency_steps(
                    current_id,
                    edge_cache=edge_cache,
                ):
                    if target_id in visited:
                        continue
                    candidate = _Path(
                        depth=depth,
                        node_ids=current_path.node_ids + (target_id,),
                        steps=current_path.steps + (step,),
                    )
                    existing = proposals.get(target_id)
                    if (
                        existing is None
                        and len(dependency_paths) + len(proposals)
                        >= _MAX_DEPENDENCY_STATES
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
                for target_id, _step in self._outgoing_dependency_steps(
                    current_id,
                    edge_cache=edge_cache,
                )
            )
        return dependency_paths, truncated

    def _outgoing_dependency_steps(
        self,
        node_id: str,
        *,
        edge_cache: dict[str, tuple[KnowledgeEdge, ...]],
    ) -> tuple[tuple[str, GraphPathStep], ...]:
        results: list[tuple[str, GraphPathStep]] = []
        for edge in self._edges_for(node_id, cache=edge_cache):
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

    def _edges_for(
        self,
        node_id: str,
        *,
        cache: dict[str, tuple[KnowledgeEdge, ...]],
    ) -> tuple[KnowledgeEdge, ...]:
        edges = cache.get(node_id)
        if edges is None:
            edges = self.index.edges_for(node_id)
            cache[node_id] = edges
        return edges


def _validate_limit(limit: int) -> None:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 1000
    ):
        raise ValueError("knowledge result limit must be between 1 and 1000")
