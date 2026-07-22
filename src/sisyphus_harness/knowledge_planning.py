from __future__ import annotations

from .contracts.knowledge import (
    CandidateTaskStatus,
    KnowledgeNodeType,
    MAX_GRAPH_TRAVERSAL_DEPTH,
    NextStepCandidate,
    NextStepContext,
    knowledge_graph_score,
    next_step_candidate_explanation,
    normalized_terms,
    validate_max_depth,
)
from .knowledge_dependencies import KnowledgeDependencyService
from .knowledge_graph_errors import KnowledgeGraphError
from .knowledge_read_context import KnowledgeReadContext, validate_result_limit
from .ports.knowledge import KnowledgeIndexPort


class KnowledgePlanningService:
    def __init__(
        self,
        index: KnowledgeIndexPort,
        *,
        max_graph_expansion_nodes: int,
        max_next_step_candidates: int,
        max_dependency_states: int,
    ) -> None:
        self.index = index
        self.max_graph_expansion_nodes = max_graph_expansion_nodes
        self.max_next_step_candidates = max_next_step_candidates
        self.max_dependency_states = max_dependency_states
        self.dependencies = KnowledgeDependencyService(
            index,
            max_graph_expansion_nodes=max_graph_expansion_nodes,
            max_dependency_states=max_dependency_states,
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
        validate_result_limit(limit)
        context = KnowledgeReadContext(
            self.index,
            max_graph_expansion_nodes=self.max_graph_expansion_nodes,
            max_dependency_states=self.max_dependency_states,
        )
        anchor = context.required_node(anchor_id)
        query_source = (
            query
            if query is not None and query.strip()
            else f"{anchor.title} {anchor.content}"
        )
        query_terms = normalized_terms(query_source)[:128]
        paths = context.reachable_paths(anchor_id, max_depth=max_depth)
        task_ids_list: list[str] = []
        for node_id in sorted(set(paths).difference({anchor_id})):
            if context.required_node(node_id).node_type is KnowledgeNodeType.TASK:
                if len(task_ids_list) >= self.max_next_step_candidates:
                    raise KnowledgeGraphError(
                        "next-step candidate expansion exceeds the supported limit"
                    )
                task_ids_list.append(node_id)
        task_ids = tuple(task_ids_list)
        term_scores = self.index.term_scores(query_terms, task_ids)
        candidate_drafts: list[tuple[tuple[object, ...], dict[str, object]]] = []
        for task_id in task_ids:
            task = context.required_node(task_id)
            path = paths[task_id]
            weighted_terms = term_scores.get(task_id, ())
            matched_terms = tuple(term for term, _weight in weighted_terms)
            lexical_score = sum(weight for _term, weight in weighted_terms)
            graph_score = knowledge_graph_score(path.depth)
            total_score = lexical_score * 100 + graph_score
            inspection = self.dependencies.inspect_in_context(
                context,
                task_id,
                max_depth=dependency_max_depth,
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
        result = NextStepContext(
            anchor_id=anchor_id,
            query_terms=query_terms,
            candidate_max_depth=max_depth,
            dependency_max_depth=dependency_max_depth,
            index_revision_digest=context.revision,
            candidates=ranked,
        )
        context.require_stable_revision(operation="next-step planning")
        return result
