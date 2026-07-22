from __future__ import annotations

from .contracts.knowledge import (
    CandidateTaskStatus,
    DependencyInspection,
    DependencyState,
    KnowledgeNodeType,
    MAX_GRAPH_TRAVERSAL_DEPTH,
    validate_max_depth,
)
from .knowledge_graph_errors import KnowledgeGraphError
from .knowledge_read_context import KnowledgeReadContext
from .ports.knowledge import KnowledgeIndexPort


class KnowledgeDependencyService:
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

    def inspect_dependencies(
        self,
        task_id: str,
        *,
        max_depth: int = MAX_GRAPH_TRAVERSAL_DEPTH,
    ) -> DependencyInspection:
        """Inspect candidate task dependencies without making a readiness decision."""

        validate_max_depth(max_depth)
        context = KnowledgeReadContext(
            self.index,
            max_graph_expansion_nodes=self.max_graph_expansion_nodes,
            max_dependency_states=self.max_dependency_states,
        )
        inspection = self.inspect_in_context(
            context,
            task_id,
            max_depth=max_depth,
        )
        context.require_stable_revision(operation="dependency inspection")
        return inspection

    def inspect_in_context(
        self,
        context: KnowledgeReadContext,
        task_id: str,
        *,
        max_depth: int,
    ) -> DependencyInspection:
        task = context.required_node(task_id)
        if task.node_type is not KnowledgeNodeType.TASK:
            raise KnowledgeGraphError("dependency inspection requires a task node")

        paths, truncated = context.dependency_paths(
            task_id,
            max_depth=max_depth,
        )
        dependencies: list[DependencyState] = []
        for dependency_id, path in sorted(
            paths.items(), key=lambda item: (item[1].depth, item[0])
        ):
            dependency = context.required_node(dependency_id)
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
            index_revision_digest=context.revision,
            dependencies=tuple(dependencies),
            all_satisfied=not unmet_reasons,
            truncated=truncated,
            unmet_reasons=unmet_reasons,
        )
