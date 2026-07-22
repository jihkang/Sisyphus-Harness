from __future__ import annotations

from .contracts.knowledge import (
    KnowledgeSearchHit,
    MAX_GRAPH_TRAVERSAL_DEPTH,
    knowledge_graph_score,
    knowledge_score_explanation,
    normalized_terms,
    validate_max_depth,
)
from .knowledge_read_context import KnowledgeReadContext, validate_result_limit
from .ports.knowledge import KnowledgeIndexPort


class KnowledgeSearchService:
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
        validate_result_limit(limit)
        context = KnowledgeReadContext(
            self.index,
            max_graph_expansion_nodes=self.max_graph_expansion_nodes,
            max_dependency_states=self.max_dependency_states,
        )
        context.required_node(anchor_id)
        query_terms = normalized_terms(query)
        if not query_terms:
            raise ValueError("knowledge search query must contain at least one term")
        paths = context.reachable_paths(anchor_id, max_depth=max_depth)
        candidate_ids = tuple(sorted(set(paths).difference({anchor_id})))
        scores = self.index.term_scores(query_terms, candidate_ids)
        hits: list[KnowledgeSearchHit] = []
        for node_id, term_weights in scores.items():
            path = paths[node_id]
            node = context.required_node(node_id)
            matched_terms = tuple(term for term, _weight in term_weights)
            lexical_score = sum(weight for _term, weight in term_weights)
            graph_score = knowledge_graph_score(path.depth)
            total_score = lexical_score * 100 + graph_score
            hits.append(
                KnowledgeSearchHit(
                    anchor_id=anchor_id,
                    index_revision_digest=context.revision,
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
        context.require_stable_revision(operation="search")
        return tuple(hits[:limit])
