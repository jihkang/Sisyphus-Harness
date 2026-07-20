from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import re
from types import MappingProxyType

from .codec import WireModel, sha256_digest, strict_object


DERIVED_CANDIDATE_AUTHORITY = "derived_candidate_only"
MAX_GRAPH_TRAVERSAL_DEPTH = 3

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_TERM = re.compile(r"[^\W_]+", re.UNICODE)


class KnowledgeNodeType(StrEnum):
    TASK = "task"
    KNOWLEDGE = "knowledge"


class CandidateTaskStatus(StrEnum):
    COMPLETED = "completed"
    READY = "ready"
    BLOCKED = "blocked"


class DependencyEdgeWriteResult(StrEnum):
    INSERTED = "inserted"
    UNCHANGED = "unchanged"
    CYCLE = "cycle"


class KnowledgeEdgeType(StrEnum):
    DEPENDS_ON = "depends_on"
    REQUIRES = "requires"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    RELATES_TO = "relates_to"
    REFINES = "refines"
    SUPERSEDES = "supersedes"
    DERIVED_FROM = "derived_from"
    MENTIONS = "mentions"


DEPENDENCY_EDGE_TYPES = frozenset(
    {KnowledgeEdgeType.DEPENDS_ON, KnowledgeEdgeType.REQUIRES}
)


@dataclass(frozen=True, slots=True)
class KnowledgeProvenance(WireModel):
    source_id: str
    source_kind: str
    source_digest: str
    producer: str
    revision: str = "1"
    schema_version: str = "sisyphus_harness.knowledge_provenance.v1"

    def __post_init__(self) -> None:
        _validate_identifier(self.source_id, "knowledge provenance source ID")
        _validate_identifier(self.source_kind, "knowledge provenance source kind")
        _validate_digest(
            self.source_digest,
            "knowledge provenance source digest",
        )
        if (
            type(self.producer) is not str
            or not self.producer.strip()
            or "\0" in self.producer
        ):
            raise ValueError("knowledge provenance producer must be non-empty")
        if (
            type(self.revision) is not str
            or not self.revision.strip()
            or "\0" in self.revision
        ):
            raise ValueError("knowledge provenance revision must be non-empty")
        if (
            type(self.schema_version) is not str
            or self.schema_version
            != "sisyphus_harness.knowledge_provenance.v1"
        ):
            raise ValueError("unsupported knowledge provenance schema")

    @property
    def provenance_digest(self) -> str:
        return sha256_digest(WireModel.to_dict(self))

    @classmethod
    def from_dict(cls, raw: object) -> KnowledgeProvenance:
        raw = strict_object(
            raw,
            required={
                "source_id",
                "source_kind",
                "source_digest",
                "producer",
                "revision",
                "schema_version",
            },
            optional={"provenance_digest"},
            label="knowledge provenance",
        )
        values = {
            key: _string(raw[key], f"knowledge provenance {key}")
            for key in (
                "source_id",
                "source_kind",
                "source_digest",
                "producer",
                "revision",
                "schema_version",
            )
        }
        result = cls(**values)
        recorded = raw.get("provenance_digest")
        if recorded is not None:
            _validate_digest(recorded, "knowledge provenance digest")
            if recorded != result.provenance_digest:
                raise ValueError(
                    "knowledge provenance digest does not match content"
                )
        return result


@dataclass(frozen=True, slots=True)
class KnowledgeNode(WireModel):
    node_id: str
    node_type: KnowledgeNodeType
    title: str
    content: str
    provenance: KnowledgeProvenance
    task_status: CandidateTaskStatus | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    authority: str = DERIVED_CANDIDATE_AUTHORITY
    schema_version: str = "sisyphus_harness.knowledge_node.v1"

    def __post_init__(self) -> None:
        _validate_identifier(self.node_id, "knowledge node ID")
        object.__setattr__(
            self,
            "node_type",
            _enum(self.node_type, KnowledgeNodeType, "knowledge node type"),
        )
        if (
            type(self.title) is not str
            or not self.title.strip()
            or "\0" in self.title
            or len(self.title) > 512
        ):
            raise ValueError("knowledge node title is invalid")
        if (
            type(self.content) is not str
            or not self.content.strip()
            or "\0" in self.content
        ):
            raise ValueError("knowledge node content must be non-empty")
        if len(self.content) > 1_000_000:
            raise ValueError("knowledge node content exceeds the supported size")
        if type(self.provenance) is not KnowledgeProvenance:
            raise ValueError(
                "knowledge node provenance must be an exact KnowledgeProvenance"
            )
        status = self.task_status
        if status is not None:
            status = _enum(status, CandidateTaskStatus, "candidate task status")
            object.__setattr__(self, "task_status", status)
        if self.node_type is KnowledgeNodeType.TASK and status is None:
            raise ValueError("task knowledge node requires a candidate task status")
        if self.node_type is KnowledgeNodeType.KNOWLEDGE and status is not None:
            raise ValueError("non-task knowledge node cannot have a task status")
        object.__setattr__(
            self,
            "metadata",
            _freeze_metadata(self.metadata, "knowledge node metadata"),
        )
        if (
            type(self.authority) is not str
            or self.authority != DERIVED_CANDIDATE_AUTHORITY
        ):
            raise ValueError("knowledge nodes are derived candidates only")
        if (
            type(self.schema_version) is not str
            or self.schema_version != "sisyphus_harness.knowledge_node.v1"
        ):
            raise ValueError("unsupported knowledge node schema")

    @property
    def content_digest(self) -> str:
        return sha256_digest({"title": self.title, "content": self.content})

    @property
    def metadata_digest(self) -> str:
        return sha256_digest(dict(self.metadata))

    @property
    def provenance_digest(self) -> str:
        return self.provenance.provenance_digest

    @property
    def node_digest(self) -> str:
        return sha256_digest(self._payload())

    def _payload(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "title": self.title,
            "content": self.content,
            "provenance": WireModel.to_dict(self.provenance),
            "task_status": None if self.task_status is None else self.task_status.value,
            "metadata": dict(self.metadata),
            "authority": self.authority,
            "schema_version": self.schema_version,
        }

    def to_dict(self) -> dict[str, object]:
        payload = self._payload()
        payload.update(
            {
                "content_digest": self.content_digest,
                "metadata_digest": self.metadata_digest,
                "provenance_digest": self.provenance_digest,
                "node_digest": self.node_digest,
            }
        )
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> KnowledgeNode:
        digest_fields = {
            "content_digest",
            "metadata_digest",
            "provenance_digest",
            "node_digest",
        }
        raw = strict_object(
            raw,
            required={
                "node_id",
                "node_type",
                "title",
                "content",
                "provenance",
                "task_status",
                "metadata",
                "authority",
                "schema_version",
            },
            optional=digest_fields,
            label="knowledge node",
        )
        metadata = raw["metadata"]
        if not isinstance(metadata, dict):
            raise ValueError("knowledge node metadata must be an object")
        status_raw = raw["task_status"]
        if status_raw is not None and type(status_raw) is not str:
            raise ValueError("candidate task status must be a string or null")
        result = cls(
            node_id=_string(raw["node_id"], "knowledge node ID"),
            node_type=_string(raw["node_type"], "knowledge node type"),
            title=_string(raw["title"], "knowledge node title"),
            content=_string(raw["content"], "knowledge node content"),
            provenance=KnowledgeProvenance.from_dict(raw["provenance"]),
            task_status=status_raw,
            metadata=metadata,
            authority=_string(raw["authority"], "knowledge node authority"),
            schema_version=_string(raw["schema_version"], "knowledge node schema"),
        )
        expected = {
            "content_digest": result.content_digest,
            "metadata_digest": result.metadata_digest,
            "provenance_digest": result.provenance_digest,
            "node_digest": result.node_digest,
        }
        _validate_recorded_digests(raw, expected, "knowledge node")
        return result


@dataclass(frozen=True, slots=True)
class KnowledgeEdge(WireModel):
    source_node_id: str
    target_node_id: str
    edge_type: KnowledgeEdgeType
    provenance: KnowledgeProvenance
    metadata: Mapping[str, str] = field(default_factory=dict)
    authority: str = DERIVED_CANDIDATE_AUTHORITY
    schema_version: str = "sisyphus_harness.knowledge_edge.v1"

    def __post_init__(self) -> None:
        _validate_identifier(self.source_node_id, "knowledge edge source ID")
        _validate_identifier(self.target_node_id, "knowledge edge target ID")
        if self.source_node_id == self.target_node_id:
            raise ValueError("knowledge edge cannot be a self-edge")
        object.__setattr__(
            self,
            "edge_type",
            _enum(self.edge_type, KnowledgeEdgeType, "knowledge edge type"),
        )
        if type(self.provenance) is not KnowledgeProvenance:
            raise ValueError(
                "knowledge edge provenance must be an exact KnowledgeProvenance"
            )
        object.__setattr__(
            self,
            "metadata",
            _freeze_metadata(self.metadata, "knowledge edge metadata"),
        )
        if (
            type(self.authority) is not str
            or self.authority != DERIVED_CANDIDATE_AUTHORITY
        ):
            raise ValueError("knowledge edges are derived candidates only")
        if (
            type(self.schema_version) is not str
            or self.schema_version != "sisyphus_harness.knowledge_edge.v1"
        ):
            raise ValueError("unsupported knowledge edge schema")

    @property
    def metadata_digest(self) -> str:
        return sha256_digest(dict(self.metadata))

    @property
    def provenance_digest(self) -> str:
        return self.provenance.provenance_digest

    @property
    def edge_digest(self) -> str:
        return sha256_digest(self._payload())

    def _payload(self) -> dict[str, object]:
        return {
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "edge_type": self.edge_type.value,
            "provenance": WireModel.to_dict(self.provenance),
            "metadata": dict(self.metadata),
            "authority": self.authority,
            "schema_version": self.schema_version,
        }

    def to_dict(self) -> dict[str, object]:
        payload = self._payload()
        payload.update(
            {
                "metadata_digest": self.metadata_digest,
                "provenance_digest": self.provenance_digest,
                "edge_digest": self.edge_digest,
            }
        )
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> KnowledgeEdge:
        raw = strict_object(
            raw,
            required={
                "source_node_id",
                "target_node_id",
                "edge_type",
                "provenance",
                "metadata",
                "authority",
                "schema_version",
            },
            optional={"metadata_digest", "provenance_digest", "edge_digest"},
            label="knowledge edge",
        )
        metadata = raw["metadata"]
        if not isinstance(metadata, dict):
            raise ValueError("knowledge edge metadata must be an object")
        result = cls(
            source_node_id=_string(raw["source_node_id"], "knowledge edge source ID"),
            target_node_id=_string(raw["target_node_id"], "knowledge edge target ID"),
            edge_type=_string(raw["edge_type"], "knowledge edge type"),
            provenance=KnowledgeProvenance.from_dict(raw["provenance"]),
            metadata=metadata,
            authority=_string(raw["authority"], "knowledge edge authority"),
            schema_version=_string(raw["schema_version"], "knowledge edge schema"),
        )
        _validate_recorded_digests(
            raw,
            {
                "metadata_digest": result.metadata_digest,
                "provenance_digest": result.provenance_digest,
                "edge_digest": result.edge_digest,
            },
            "knowledge edge",
        )
        return result


@dataclass(frozen=True, slots=True)
class GraphPathStep(WireModel):
    source_node_id: str
    target_node_id: str
    edge_type: KnowledgeEdgeType
    edge_digest: str
    traversed_forward: bool

    def __post_init__(self) -> None:
        _validate_identifier(self.source_node_id, "graph path source node ID")
        _validate_identifier(self.target_node_id, "graph path target node ID")
        if self.source_node_id == self.target_node_id:
            raise ValueError("graph path step cannot be a self-edge")
        object.__setattr__(
            self,
            "edge_type",
            _enum(self.edge_type, KnowledgeEdgeType, "graph path edge type"),
        )
        _validate_digest(self.edge_digest, "graph path edge digest")
        _validate_bool(self.traversed_forward, "graph path direction")


@dataclass(frozen=True, slots=True)
class KnowledgeSearchHit(WireModel):
    anchor_id: str
    index_revision_digest: str
    node: KnowledgeNode
    depth: int
    path_node_ids: tuple[str, ...]
    path_steps: tuple[GraphPathStep, ...]
    matched_terms: tuple[str, ...]
    lexical_score: int
    graph_score: int
    total_score: int
    explanation: str
    authority: str = DERIVED_CANDIDATE_AUTHORITY

    def __post_init__(self) -> None:
        _validate_identifier(self.anchor_id, "knowledge search anchor ID")
        _validate_digest(
            self.index_revision_digest,
            "knowledge search index revision digest",
        )
        if type(self.node) is not KnowledgeNode:
            raise ValueError("knowledge search hit node is invalid")
        _validate_graph_path(
            start_node_id=self.anchor_id,
            terminal_node_id=self.node.node_id,
            depth=self.depth,
            path_node_ids=self.path_node_ids,
            path_steps=self.path_steps,
            label="knowledge search path",
        )
        _validate_scored_projection(
            node=self.node,
            depth=self.depth,
            path_steps=self.path_steps,
            matched_terms=self.matched_terms,
            lexical_score=self.lexical_score,
            graph_score=self.graph_score,
            total_score=self.total_score,
            allow_empty_terms=False,
            label="knowledge search hit",
        )
        expected_explanation = knowledge_score_explanation(
            depth=self.depth,
            path_steps=self.path_steps,
            matched_terms=self.matched_terms,
            lexical_score=self.lexical_score,
            graph_score=self.graph_score,
            total_score=self.total_score,
        )
        if (
            type(self.explanation) is not str
            or self.explanation != expected_explanation
        ):
            raise ValueError("knowledge search explanation does not match its score")
        _validate_candidate_authority(self.authority, "knowledge search hit")


@dataclass(frozen=True, slots=True)
class DependencyState(WireModel):
    node_id: str
    task_status: CandidateTaskStatus
    depth: int
    path_node_ids: tuple[str, ...]
    path_steps: tuple[GraphPathStep, ...]
    satisfied: bool

    def __post_init__(self) -> None:
        _validate_identifier(self.node_id, "dependency node ID")
        object.__setattr__(
            self,
            "task_status",
            _enum(self.task_status, CandidateTaskStatus, "dependency task status"),
        )
        path_node_ids, path_steps = _validate_graph_path(
            start_node_id=None,
            terminal_node_id=self.node_id,
            depth=self.depth,
            path_node_ids=self.path_node_ids,
            path_steps=self.path_steps,
            dependency_only=True,
            label="dependency path",
        )
        object.__setattr__(self, "path_node_ids", path_node_ids)
        object.__setattr__(self, "path_steps", path_steps)
        _validate_bool(self.satisfied, "dependency satisfaction")
        if self.satisfied != (self.task_status is CandidateTaskStatus.COMPLETED):
            raise ValueError(
                "dependency satisfaction does not match its candidate task status"
            )


@dataclass(frozen=True, slots=True)
class DependencyInspection(WireModel):
    task_id: str
    max_depth: int
    index_revision_digest: str
    dependencies: tuple[DependencyState, ...]
    all_satisfied: bool
    truncated: bool
    unmet_reasons: tuple[str, ...]
    authority: str = DERIVED_CANDIDATE_AUTHORITY

    def __post_init__(self) -> None:
        _validate_identifier(self.task_id, "dependency inspection task ID")
        validate_max_depth(self.max_depth)
        _validate_digest(
            self.index_revision_digest,
            "dependency inspection index revision digest",
        )
        dependencies = _strict_tuple(
            self.dependencies,
            "dependency inspection dependencies",
        )
        if len(dependencies) > 1000 or any(
            type(item) is not DependencyState for item in dependencies
        ):
            raise ValueError("dependency inspection dependencies are invalid")
        if any(
            item.path_node_ids[0] != self.task_id or item.depth > self.max_depth
            for item in dependencies
        ):
            raise ValueError(
                "dependency inspection paths exceed or do not start at the task"
            )
        expected_order = tuple(
            sorted(dependencies, key=lambda item: (item.depth, item.node_id))
        )
        if dependencies != expected_order or len(
            {item.node_id for item in dependencies}
        ) != len(dependencies):
            raise ValueError(
                "dependency inspection dependencies must be unique and ordered"
            )
        object.__setattr__(self, "dependencies", dependencies)
        _validate_bool(self.all_satisfied, "dependency inspection satisfaction")
        _validate_bool(self.truncated, "dependency inspection truncation")
        reasons = _strict_bounded_strings(
            self.unmet_reasons,
            "dependency inspection unmet reasons",
        )
        expected_reasons = tuple(
            f"dependency {dependency.node_id} is {dependency.task_status.value}"
            for dependency in dependencies
            if not dependency.satisfied
        ) + (
            (f"dependency traversal truncated at depth {self.max_depth}",)
            if self.truncated
            else ()
        )
        if reasons != expected_reasons:
            raise ValueError(
                "dependency inspection unmet reasons do not match its state"
            )
        object.__setattr__(self, "unmet_reasons", reasons)
        if self.all_satisfied != (not expected_reasons):
            raise ValueError(
                "dependency inspection satisfaction does not match its reasons"
            )
        _validate_candidate_authority(self.authority, "dependency inspection")


@dataclass(frozen=True, slots=True)
class NextStepCandidate(WireModel):
    rank: int
    anchor_id: str
    task: KnowledgeNode
    depth: int
    path_node_ids: tuple[str, ...]
    path_steps: tuple[GraphPathStep, ...]
    matched_terms: tuple[str, ...]
    lexical_score: int
    graph_score: int
    total_score: int
    dependency_inspection: DependencyInspection
    eligible: bool
    unmet_dependency_reasons: tuple[str, ...]
    explanation: str
    authority: str = DERIVED_CANDIDATE_AUTHORITY

    def __post_init__(self) -> None:
        _validate_positive_rank(self.rank)
        _validate_identifier(self.anchor_id, "next-step candidate anchor ID")
        if (
            type(self.task) is not KnowledgeNode
            or self.task.node_type is not KnowledgeNodeType.TASK
        ):
            raise ValueError("next-step candidate requires a task node")
        _validate_graph_path(
            start_node_id=self.anchor_id,
            terminal_node_id=self.task.node_id,
            depth=self.depth,
            path_node_ids=self.path_node_ids,
            path_steps=self.path_steps,
            label="next-step candidate path",
        )
        _validate_scored_projection(
            node=self.task,
            depth=self.depth,
            path_steps=self.path_steps,
            matched_terms=self.matched_terms,
            lexical_score=self.lexical_score,
            graph_score=self.graph_score,
            total_score=self.total_score,
            allow_empty_terms=True,
            label="next-step candidate",
        )
        if (
            type(self.dependency_inspection) is not DependencyInspection
            or self.dependency_inspection.task_id != self.task.node_id
        ):
            raise ValueError(
                "next-step candidate dependency inspection does not match its task"
            )
        _validate_bool(self.eligible, "next-step candidate eligibility")
        reasons = _strict_bounded_strings(
            self.unmet_dependency_reasons,
            "next-step candidate unmet reasons",
            max_items=1002,
        )
        expected_reasons = (
            ()
            if self.task.task_status is CandidateTaskStatus.READY
            else (f"task status is {self.task.task_status.value}",)
        ) + self.dependency_inspection.unmet_reasons
        if reasons != expected_reasons:
            raise ValueError(
                "next-step candidate unmet reasons do not match its state"
            )
        object.__setattr__(self, "unmet_dependency_reasons", reasons)
        if self.eligible != (not expected_reasons):
            raise ValueError(
                "next-step candidate eligibility does not match its reasons"
            )
        expected_explanation = next_step_candidate_explanation(
            task=self.task,
            depth=self.depth,
            path_steps=self.path_steps,
            matched_terms=self.matched_terms,
            lexical_score=self.lexical_score,
            graph_score=self.graph_score,
            total_score=self.total_score,
            eligible=self.eligible,
            unmet_reasons=self.unmet_dependency_reasons,
        )
        if (
            type(self.explanation) is not str
            or self.explanation != expected_explanation
        ):
            raise ValueError(
                "next-step candidate explanation does not match its decision data"
            )
        _validate_candidate_authority(self.authority, "next-step candidate")


@dataclass(frozen=True, slots=True)
class NextStepContext(WireModel):
    anchor_id: str
    query_terms: tuple[str, ...]
    candidate_max_depth: int
    dependency_max_depth: int
    index_revision_digest: str
    candidates: tuple[NextStepCandidate, ...]
    authority: str = DERIVED_CANDIDATE_AUTHORITY
    schema_version: str = "sisyphus_harness.next_step_context.v2"

    def __post_init__(self) -> None:
        _validate_identifier(self.anchor_id, "next-step context anchor ID")
        terms = _validate_terms(
            self.query_terms,
            allow_empty=True,
            label="next-step context query terms",
        )
        object.__setattr__(self, "query_terms", terms)
        validate_max_depth(self.candidate_max_depth)
        validate_max_depth(self.dependency_max_depth)
        _validate_digest(
            self.index_revision_digest,
            "next-step context index revision digest",
        )
        candidates = _strict_tuple(
            self.candidates,
            "next-step context candidates",
        )
        if len(candidates) > 1000 or any(
            type(item) is not NextStepCandidate for item in candidates
        ):
            raise ValueError("next-step context candidates are invalid")
        if tuple(item.rank for item in candidates) != tuple(
            range(1, len(candidates) + 1)
        ):
            raise ValueError("next-step context candidate ranks are not contiguous")
        if len({item.task.node_id for item in candidates}) != len(candidates):
            raise ValueError("next-step context candidate tasks must be unique")
        query_term_set = frozenset(terms)
        for candidate in candidates:
            if (
                candidate.anchor_id != self.anchor_id
                or candidate.depth > self.candidate_max_depth
                or not set(candidate.matched_terms).issubset(query_term_set)
                or candidate.dependency_inspection.max_depth
                != self.dependency_max_depth
                or candidate.dependency_inspection.index_revision_digest
                != self.index_revision_digest
            ):
                raise ValueError(
                    "next-step candidate does not match its context budgets or revision"
                )
        expected_order = tuple(
            sorted(
                candidates,
                key=lambda candidate: (
                    not candidate.eligible,
                    -candidate.total_score,
                    candidate.depth,
                    candidate.task.node_id,
                    candidate.task.node_digest,
                ),
            )
        )
        if candidates != expected_order:
            raise ValueError("next-step context candidates are not deterministically ordered")
        object.__setattr__(self, "candidates", candidates)
        _validate_candidate_authority(self.authority, "next-step context")
        if (
            type(self.schema_version) is not str
            or self.schema_version != "sisyphus_harness.next_step_context.v2"
        ):
            raise ValueError("unsupported next-step context schema")


def normalized_terms(text: str) -> tuple[str, ...]:
    if type(text) is not str:
        raise TypeError("term source must be a string")
    return tuple(sorted({match.group(0).casefold() for match in _TERM.finditer(text)}))


def weighted_node_terms(node: KnowledgeNode) -> Mapping[str, int]:
    weights: Counter[str] = Counter()
    for term in _TERM.findall(node.title.casefold()):
        weights[term] += 5
    for term in _TERM.findall(node.content.casefold()):
        weights[term] += 2
    for key, value in node.metadata.items():
        for term in _TERM.findall(f"{key} {value}".casefold()):
            weights[term] += 1
    return MappingProxyType(dict(sorted(weights.items())))


def knowledge_graph_score(depth: int) -> int:
    if type(depth) is not int or not 1 <= depth <= MAX_GRAPH_TRAVERSAL_DEPTH:
        raise ValueError("knowledge score depth must be between 1 and 3")
    return (MAX_GRAPH_TRAVERSAL_DEPTH + 1 - depth) * 10


def knowledge_score_explanation(
    *,
    depth: int,
    path_steps: tuple[GraphPathStep, ...],
    matched_terms: tuple[str, ...],
    lexical_score: int,
    graph_score: int,
    total_score: int,
) -> str:
    relations = ", ".join(
        f"{step.edge_type.value}:{'forward' if step.traversed_forward else 'reverse'}"
        for step in path_steps
    )
    return (
        f"matched terms [{', '.join(matched_terms)}]; depth={depth}; "
        f"edges=[{relations}]; lexical={lexical_score}; graph={graph_score}; "
        f"total={total_score}; authority={DERIVED_CANDIDATE_AUTHORITY}"
    )


def next_step_candidate_explanation(
    *,
    task: KnowledgeNode,
    depth: int,
    path_steps: tuple[GraphPathStep, ...],
    matched_terms: tuple[str, ...],
    lexical_score: int,
    graph_score: int,
    total_score: int,
    eligible: bool,
    unmet_reasons: tuple[str, ...],
) -> str:
    score = knowledge_score_explanation(
        depth=depth,
        path_steps=path_steps,
        matched_terms=matched_terms,
        lexical_score=lexical_score,
        graph_score=graph_score,
        total_score=total_score,
    )
    disposition = "eligible" if eligible else f"ineligible: {'; '.join(unmet_reasons)}"
    return f"task_status={task.task_status.value}; {disposition}; {score}"


def _validate_graph_path(
    *,
    start_node_id: str | None,
    terminal_node_id: str,
    depth: int,
    path_node_ids: tuple[str, ...],
    path_steps: tuple[GraphPathStep, ...],
    label: str,
    dependency_only: bool = False,
) -> tuple[tuple[str, ...], tuple[GraphPathStep, ...]]:
    if type(depth) is not int or not 1 <= depth <= MAX_GRAPH_TRAVERSAL_DEPTH:
        raise ValueError(f"{label} depth must be between 1 and 3")
    node_ids = _strict_tuple(path_node_ids, f"{label} node IDs")
    steps = _strict_tuple(path_steps, f"{label} steps")
    if len(node_ids) != depth + 1 or len(steps) != depth:
        raise ValueError(f"{label} length does not match its depth")
    if any(type(node_id) is not str for node_id in node_ids):
        raise ValueError(f"{label} contains an invalid node ID")
    for node_id in node_ids:
        _validate_identifier(node_id, f"{label} node ID")
    if len(set(node_ids)) != len(node_ids):
        raise ValueError(f"{label} cannot repeat a node")
    if (start_node_id is not None and node_ids[0] != start_node_id) or (
        node_ids[-1] != terminal_node_id
    ):
        raise ValueError(f"{label} endpoints do not match its result")
    if any(type(step) is not GraphPathStep for step in steps):
        raise ValueError(f"{label} contains an invalid step")
    for current, following, step in zip(
        node_ids[:-1],
        node_ids[1:],
        steps,
        strict=True,
    ):
        expected = (
            (current, following)
            if step.traversed_forward
            else (following, current)
        )
        if (step.source_node_id, step.target_node_id) != expected:
            raise ValueError(f"{label} step endpoints are disconnected")
        if dependency_only and (
            not step.traversed_forward
            or step.edge_type not in DEPENDENCY_EDGE_TYPES
        ):
            raise ValueError(f"{label} contains a non-dependency step")
    return node_ids, steps


def _validate_scored_projection(
    *,
    node: KnowledgeNode,
    depth: int,
    path_steps: tuple[GraphPathStep, ...],
    matched_terms: tuple[str, ...],
    lexical_score: int,
    graph_score: int,
    total_score: int,
    allow_empty_terms: bool,
    label: str,
) -> None:
    del path_steps
    terms = _validate_terms(
        matched_terms,
        allow_empty=allow_empty_terms,
        label=f"{label} matched terms",
    )
    weights = weighted_node_terms(node)
    if any(term not in weights for term in terms):
        raise ValueError(f"{label} contains a term absent from its node")
    for value, score_label in (
        (lexical_score, "lexical score"),
        (graph_score, "graph score"),
        (total_score, "total score"),
    ):
        if type(value) is not int or value < 0:
            raise ValueError(f"{label} {score_label} is invalid")
    expected_lexical = sum(weights[term] for term in terms)
    expected_graph = knowledge_graph_score(depth)
    if lexical_score != expected_lexical:
        raise ValueError(f"{label} lexical score does not match its terms")
    if graph_score != expected_graph:
        raise ValueError(f"{label} graph score does not match its depth")
    if total_score != lexical_score * 100 + graph_score:
        raise ValueError(f"{label} total score does not match its components")


def _validate_terms(
    raw: tuple[str, ...],
    *,
    allow_empty: bool,
    label: str,
) -> tuple[str, ...]:
    terms = _strict_tuple(raw, label)
    if len(terms) > 128 or (not allow_empty and not terms):
        raise ValueError(f"{label} has an invalid count")
    if any(
        type(term) is not str or normalized_terms(term) != (term,)
        for term in terms
    ):
        raise ValueError(f"{label} must contain normalized terms")
    if terms != tuple(sorted(set(terms))):
        raise ValueError(f"{label} must be sorted and unique")
    return terms


def _strict_tuple(raw: object, label: str) -> tuple:
    if type(raw) is not tuple:
        raise ValueError(f"{label} must be a built-in tuple")
    return raw


def _strict_bounded_strings(
    raw: object,
    label: str,
    *,
    max_items: int = 1001,
) -> tuple[str, ...]:
    values = _strict_tuple(raw, label)
    if len(values) > max_items or any(
        type(value) is not str
        or not value
        or "\0" in value
        or len(value) > 1024
        for value in values
    ):
        raise ValueError(f"{label} contains an invalid value")
    return values


def _validate_digest(value: str, label: str) -> None:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be SHA-256")


def _validate_bool(value: bool, label: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean")


def _validate_positive_rank(rank: int) -> None:
    if type(rank) is not int or not 1 <= rank <= 1000:
        raise ValueError("next-step candidate rank must be between 1 and 1000")


def _validate_candidate_authority(authority: str, label: str) -> None:
    if type(authority) is not str or authority != DERIVED_CANDIDATE_AUTHORITY:
        raise ValueError(f"{label} authority must remain candidate-only")


def validate_max_depth(max_depth: int) -> None:
    if type(max_depth) is not int or not 0 <= max_depth <= MAX_GRAPH_TRAVERSAL_DEPTH:
        raise ValueError(
            f"graph traversal depth must be between 0 and {MAX_GRAPH_TRAVERSAL_DEPTH}"
        )


def _freeze_metadata(raw: Mapping[str, str], label: str) -> Mapping[str, str]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{label} must be a mapping")
    result: dict[str, str] = {}
    for key, value in raw.items():
        if (
            type(key) is not str
            or not key.strip()
            or key.strip() != key
            or "\0" in key
            or len(key) > 128
        ):
            raise ValueError(f"{label} contains an invalid key")
        if type(value) is not str or "\0" in value or len(value) > 4096:
            raise ValueError(f"{label} contains an invalid value for {key!r}")
        result[key] = value
    return MappingProxyType(dict(sorted(result.items())))


def _validate_identifier(value: str, label: str) -> None:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")


def _string(value: object, label: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{label} must be a string")
    return value


def _enum(value: object, enum_type: type[StrEnum], label: str) -> StrEnum:
    if type(value) is not str and type(value) is not enum_type:
        raise ValueError(f"{label} is unsupported")
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is unsupported") from exc


def _validate_recorded_digests(
    raw: Mapping[str, object],
    expected: Mapping[str, str],
    label: str,
) -> None:
    for key, value in expected.items():
        recorded = raw.get(key)
        if recorded is not None:
            _validate_digest(recorded, f"{label} {key}")
            if recorded != value:
                raise ValueError(f"{label} {key} does not match content")
