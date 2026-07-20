from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import TypeAlias

from .codec import WireModel, sha256_digest, strict_object


_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_MAX_TOKEN_LENGTH = 256
_MAX_STRING_VALUE_LENGTH = 4096
_MAX_SEQUENCE_VALUE_LENGTH = 256
_MAX_INTEGER = (1 << 63) - 1
_HARD_MAX_CLAUSES = 256
_HARD_MAX_EXPRESSION_NODES = 2048
_HARD_MAX_EXPRESSION_DEPTH = 64


EvidenceScalar: TypeAlias = bool | int | str
EvidenceValue: TypeAlias = EvidenceScalar | tuple[EvidenceScalar, ...]


class PredicateOperator(str, Enum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    CONTAINS = "contains"
    SUBSET = "subset"
    DISJOINT = "disjoint"


class ObservationStatus(str, Enum):
    OBSERVED = "observed"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class LogicalResult(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INDETERMINATE = "indeterminate"


class EvaluationLifecycle(str, Enum):
    COMPLETED = "completed"
    ERROR = "error"


class GapClosureResult(str, Enum):
    CLOSED = "closed"
    NOT_CLOSED = "not_closed"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True, slots=True)
class EvidenceSelector(WireModel):
    observation_type: str
    stage: str
    check_id: str
    producer_authority: str

    def __post_init__(self) -> None:
        _validate_token(self.observation_type, "observation type")
        _validate_token(self.stage, "observation stage")
        _validate_token(self.check_id, "observation check ID")
        _validate_token(self.producer_authority, "observation producer authority")

    @property
    def selector_digest(self) -> str:
        return sha256_digest(WireModel.to_dict(self))

    @classmethod
    def from_dict(cls, raw: object) -> EvidenceSelector:
        raw = strict_object(
            raw,
            required={
                "observation_type",
                "stage",
                "check_id",
                "producer_authority",
            },
            label="evidence selector",
        )
        return cls(
            observation_type=_string(raw["observation_type"], "observation type"),
            stage=_string(raw["stage"], "observation stage"),
            check_id=_string(raw["check_id"], "observation check ID"),
            producer_authority=_string(
                raw["producer_authority"],
                "observation producer authority",
            ),
        )


@dataclass(frozen=True, slots=True)
class EvidenceClause(WireModel):
    clause_id: str
    selector: EvidenceSelector
    operator: PredicateOperator
    expected: EvidenceValue

    def __post_init__(self) -> None:
        _validate_token(self.clause_id, "evidence clause ID")
        if type(self.selector) is not EvidenceSelector:
            raise ValueError("evidence clause selector is invalid")
        if not isinstance(self.operator, PredicateOperator):
            raise ValueError("evidence clause operator is invalid")
        object.__setattr__(
            self,
            "expected",
            _normalize_evidence_value(self.expected, "evidence clause expected value"),
        )

    @property
    def clause_digest(self) -> str:
        return sha256_digest(WireModel.to_dict(self))

    @classmethod
    def from_dict(cls, raw: object) -> EvidenceClause:
        raw = strict_object(
            raw,
            required={"clause_id", "selector", "operator", "expected"},
            label="evidence clause",
        )
        return cls(
            clause_id=_string(raw["clause_id"], "evidence clause ID"),
            selector=EvidenceSelector.from_dict(raw["selector"]),
            operator=_enum(
                PredicateOperator,
                raw["operator"],
                "evidence clause operator",
            ),
            expected=_parse_evidence_value(
                raw["expected"],
                "evidence clause expected value",
            ),
        )


@dataclass(frozen=True, slots=True)
class ClauseRef(WireModel):
    clause_id: str

    def __post_init__(self) -> None:
        _validate_token(self.clause_id, "expression clause ID")

    def to_dict(self) -> dict[str, object]:
        return {"kind": "clause_ref", "clause_id": self.clause_id}


@dataclass(frozen=True, slots=True)
class AllOf(WireModel):
    children: tuple[EvidenceExpression, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "children",
            _validate_expression_children(self.children, "all_of"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "all_of",
            "children": [child.to_dict() for child in self.children],
        }


@dataclass(frozen=True, slots=True)
class AnyOf(WireModel):
    children: tuple[EvidenceExpression, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "children",
            _validate_expression_children(self.children, "any_of"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "any_of",
            "children": [child.to_dict() for child in self.children],
        }


@dataclass(frozen=True, slots=True)
class AtLeast(WireModel):
    minimum: int
    children: tuple[EvidenceExpression, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "children",
            _validate_expression_children(self.children, "at_least"),
        )
        if (
            type(self.minimum) is not int
            or self.minimum <= 0
            or self.minimum > len(self.children)
        ):
            raise ValueError("at_least minimum must be within its child count")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "at_least",
            "minimum": self.minimum,
            "children": [child.to_dict() for child in self.children],
        }


@dataclass(frozen=True, slots=True)
class Not(WireModel):
    child: EvidenceExpression

    def __post_init__(self) -> None:
        if type(self.child) not in _EXPRESSION_TYPES:
            raise ValueError("not expression child is invalid")

    def to_dict(self) -> dict[str, object]:
        return {"kind": "not", "child": self.child.to_dict()}


EvidenceExpression: TypeAlias = ClauseRef | AllOf | AnyOf | AtLeast | Not
_EXPRESSION_TYPES = (ClauseRef, AllOf, AnyOf, AtLeast, Not)


def expression_from_dict(raw: object) -> EvidenceExpression:
    counter = [0]
    return _expression_from_dict(raw, depth=1, counter=counter)


@dataclass(frozen=True, slots=True)
class EvidenceContract(WireModel):
    contract_id: str
    version: int
    requirement_ids: tuple[str, ...]
    gap_ids: tuple[str, ...]
    task_basis_ids: tuple[str, ...]
    verification_profile_digest: str
    observation_adapter_digest: str
    clauses: tuple[EvidenceClause, ...]
    task_success: EvidenceExpression
    schema_version: str = "sisyphus_harness.evidence_contract.v1"

    def __post_init__(self) -> None:
        _validate_token(self.contract_id, "evidence contract ID")
        _validate_positive_integer(self.version, "evidence contract version")
        object.__setattr__(
            self,
            "requirement_ids",
            _normalize_token_tuple(self.requirement_ids, "requirement IDs"),
        )
        object.__setattr__(
            self,
            "gap_ids",
            _normalize_token_tuple(self.gap_ids, "Gap IDs"),
        )
        object.__setattr__(
            self,
            "task_basis_ids",
            _normalize_token_tuple(self.task_basis_ids, "TaskBasis IDs"),
        )
        _validate_digest(
            self.verification_profile_digest,
            "evidence contract verification profile digest",
        )
        _validate_digest(
            self.observation_adapter_digest,
            "evidence contract observation adapter digest",
        )
        clauses = _strict_model_tuple(self.clauses, "evidence contract clauses")
        if not clauses:
            raise ValueError("evidence contract requires evidence clauses")
        if any(type(clause) is not EvidenceClause for clause in clauses):
            raise ValueError(
                "evidence contract clauses must be exact EvidenceClause values"
            )
        if len(clauses) > _HARD_MAX_CLAUSES:
            raise ValueError("evidence contract exceeds the hard clause limit")
        clause_ids = [clause.clause_id for clause in clauses]
        if len(set(clause_ids)) != len(clause_ids):
            raise ValueError("evidence contract clause IDs must be unique")
        object.__setattr__(self, "clauses", clauses)
        _validate_expression_hard_limits(self.task_success)
        if self.schema_version != "sisyphus_harness.evidence_contract.v1":
            raise ValueError("unsupported evidence contract schema")

    def content_payload(self) -> dict[str, object]:
        return WireModel.to_dict(self)

    @property
    def contract_digest(self) -> str:
        return sha256_digest(self.content_payload())

    def to_dict(self) -> dict[str, object]:
        payload = self.content_payload()
        payload["contract_digest"] = self.contract_digest
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> EvidenceContract:
        raw = strict_object(
            raw,
            required={
                "contract_id",
                "version",
                "requirement_ids",
                "gap_ids",
                "task_basis_ids",
                "verification_profile_digest",
                "observation_adapter_digest",
                "clauses",
                "task_success",
                "schema_version",
                "contract_digest",
            },
            label="evidence contract",
        )
        clauses = _object_list(raw["clauses"], "evidence contract clauses")
        contract = cls(
            contract_id=_string(raw["contract_id"], "evidence contract ID"),
            version=_integer(raw["version"], "evidence contract version"),
            requirement_ids=_string_tuple(
                raw["requirement_ids"],
                "evidence contract requirement IDs",
            ),
            gap_ids=_string_tuple(raw["gap_ids"], "evidence contract Gap IDs"),
            task_basis_ids=_string_tuple(
                raw["task_basis_ids"],
                "evidence contract TaskBasis IDs",
            ),
            verification_profile_digest=_digest(
                raw["verification_profile_digest"],
                "evidence contract verification profile digest",
            ),
            observation_adapter_digest=_digest(
                raw["observation_adapter_digest"],
                "evidence contract observation adapter digest",
            ),
            clauses=tuple(EvidenceClause.from_dict(item) for item in clauses),
            task_success=expression_from_dict(raw["task_success"]),
            schema_version=_string(
                raw["schema_version"],
                "evidence contract schema",
            ),
        )
        recorded = _digest(raw["contract_digest"], "evidence contract digest")
        if recorded != contract.contract_digest:
            raise ValueError("evidence contract digest does not match content")
        return contract


@dataclass(frozen=True, slots=True)
class GapClosureRule(WireModel):
    rule_id: str
    version: int
    requirement_id: str
    gap_id: str
    clauses: tuple[EvidenceClause, ...]
    closure_condition: EvidenceExpression
    schema_version: str = "sisyphus_harness.gap_closure_rule.v1"

    def __post_init__(self) -> None:
        _validate_token(self.rule_id, "Gap closure rule ID")
        _validate_positive_integer(self.version, "Gap closure rule version")
        _validate_token(self.requirement_id, "Gap closure requirement ID")
        _validate_token(self.gap_id, "Gap closure Gap ID")
        clauses = _strict_model_tuple(self.clauses, "Gap closure clauses")
        if not clauses:
            raise ValueError("Gap closure rule requires evidence clauses")
        if any(type(clause) is not EvidenceClause for clause in clauses):
            raise ValueError(
                "Gap closure rule clauses must be exact EvidenceClause values"
            )
        if len(clauses) > _HARD_MAX_CLAUSES:
            raise ValueError("Gap closure rule exceeds the hard clause limit")
        clause_ids = [clause.clause_id for clause in clauses]
        if len(set(clause_ids)) != len(clause_ids):
            raise ValueError("Gap closure clause IDs must be unique")
        object.__setattr__(self, "clauses", clauses)
        _validate_expression_hard_limits(self.closure_condition)
        if self.schema_version != "sisyphus_harness.gap_closure_rule.v1":
            raise ValueError("unsupported Gap closure rule schema")

    def content_payload(self) -> dict[str, object]:
        return WireModel.to_dict(self)

    @property
    def rule_digest(self) -> str:
        return sha256_digest(self.content_payload())

    def to_dict(self) -> dict[str, object]:
        payload = self.content_payload()
        payload["rule_digest"] = self.rule_digest
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> GapClosureRule:
        raw = strict_object(
            raw,
            required={
                "rule_id",
                "version",
                "requirement_id",
                "gap_id",
                "clauses",
                "closure_condition",
                "schema_version",
                "rule_digest",
            },
            label="Gap closure rule",
        )
        clauses = _object_list(raw["clauses"], "Gap closure rule clauses")
        rule = cls(
            rule_id=_string(raw["rule_id"], "Gap closure rule ID"),
            version=_integer(raw["version"], "Gap closure rule version"),
            requirement_id=_string(
                raw["requirement_id"],
                "Gap closure requirement ID",
            ),
            gap_id=_string(raw["gap_id"], "Gap closure Gap ID"),
            clauses=tuple(EvidenceClause.from_dict(item) for item in clauses),
            closure_condition=expression_from_dict(raw["closure_condition"]),
            schema_version=_string(raw["schema_version"], "Gap closure rule schema"),
        )
        recorded = _digest(raw["rule_digest"], "Gap closure rule digest")
        if recorded != rule.rule_digest:
            raise ValueError("Gap closure rule digest does not match content")
        return rule


@dataclass(frozen=True, slots=True)
class EvidenceObservation(WireModel):
    observation_id: str
    selector: EvidenceSelector
    subject_digest: str
    source_run_id: str
    artifact_digest: str
    status: ObservationStatus
    value: EvidenceValue | None
    reason_code: str | None
    schema_version: str = "sisyphus_harness.evidence_observation.v1"

    def __post_init__(self) -> None:
        _validate_token(self.observation_id, "evidence observation ID")
        if type(self.selector) is not EvidenceSelector:
            raise ValueError("evidence observation selector is invalid")
        _validate_digest(self.subject_digest, "evidence observation subject digest")
        _validate_token(self.source_run_id, "evidence observation source run ID")
        _validate_digest(self.artifact_digest, "evidence observation artifact digest")
        if not isinstance(self.status, ObservationStatus):
            raise ValueError("evidence observation status is invalid")
        if self.status is ObservationStatus.OBSERVED:
            if self.value is None:
                raise ValueError("observed evidence requires a value")
            if self.reason_code is not None:
                raise ValueError("observed evidence cannot have a reason code")
            object.__setattr__(
                self,
                "value",
                _normalize_evidence_value(self.value, "evidence observation value"),
            )
        else:
            if self.value is not None:
                raise ValueError("unavailable or error evidence cannot have a value")
            if self.reason_code is None:
                raise ValueError("unavailable or error evidence requires a reason code")
            _validate_token(self.reason_code, "evidence observation reason code")
        if self.schema_version != "sisyphus_harness.evidence_observation.v1":
            raise ValueError("unsupported evidence observation schema")

    def content_payload(self) -> dict[str, object]:
        return WireModel.to_dict(self)

    @property
    def observation_digest(self) -> str:
        return sha256_digest(self.content_payload())

    def to_dict(self) -> dict[str, object]:
        payload = self.content_payload()
        payload["observation_digest"] = self.observation_digest
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> EvidenceObservation:
        raw = strict_object(
            raw,
            required={
                "observation_id",
                "selector",
                "subject_digest",
                "source_run_id",
                "artifact_digest",
                "status",
                "value",
                "reason_code",
                "schema_version",
                "observation_digest",
            },
            label="evidence observation",
        )
        status = _enum(
            ObservationStatus,
            raw["status"],
            "evidence observation status",
        )
        value_raw = raw["value"]
        observation = cls(
            observation_id=_string(
                raw["observation_id"],
                "evidence observation ID",
            ),
            selector=EvidenceSelector.from_dict(raw["selector"]),
            subject_digest=_digest(
                raw["subject_digest"],
                "evidence observation subject digest",
            ),
            source_run_id=_string(
                raw["source_run_id"],
                "evidence observation source run ID",
            ),
            artifact_digest=_digest(
                raw["artifact_digest"],
                "evidence observation artifact digest",
            ),
            status=status,
            value=(
                _parse_evidence_value(value_raw, "evidence observation value")
                if value_raw is not None
                else None
            ),
            reason_code=_optional_string(
                raw["reason_code"],
                "evidence observation reason code",
            ),
            schema_version=_string(
                raw["schema_version"],
                "evidence observation schema",
            ),
        )
        recorded = _digest(
            raw["observation_digest"],
            "evidence observation digest",
        )
        if recorded != observation.observation_digest:
            raise ValueError("evidence observation digest does not match content")
        return observation


@dataclass(frozen=True, slots=True)
class PredicateEvaluation(WireModel):
    clause_id: str
    result: LogicalResult
    observation_ids: tuple[str, ...]
    reason_code: str

    def __post_init__(self) -> None:
        _validate_token(self.clause_id, "predicate evaluation clause ID")
        if not isinstance(self.result, LogicalResult):
            raise ValueError("predicate evaluation result is invalid")
        object.__setattr__(
            self,
            "observation_ids",
            _normalize_token_tuple(
                self.observation_ids,
                "predicate evaluation observation IDs",
                allow_empty=True,
            ),
        )
        _validate_token(self.reason_code, "predicate evaluation reason code")

    @property
    def evaluation_digest(self) -> str:
        return sha256_digest(WireModel.to_dict(self))

    @classmethod
    def from_dict(cls, raw: object) -> PredicateEvaluation:
        raw = strict_object(
            raw,
            required={"clause_id", "result", "observation_ids", "reason_code"},
            label="predicate evaluation",
        )
        return cls(
            clause_id=_string(raw["clause_id"], "predicate evaluation clause ID"),
            result=_enum(
                LogicalResult,
                raw["result"],
                "predicate evaluation result",
            ),
            observation_ids=_string_tuple(
                raw["observation_ids"],
                "predicate evaluation observation IDs",
            ),
            reason_code=_string(
                raw["reason_code"],
                "predicate evaluation reason code",
            ),
        )


@dataclass(frozen=True, slots=True)
class ContractEvaluation(WireModel):
    contract_digest: str
    observation_set_digest: str
    evaluator_version: str
    evaluator_digest: str
    lifecycle: EvaluationLifecycle
    logical_result: LogicalResult | None
    predicates: tuple[PredicateEvaluation, ...]
    error_code: str | None
    scope: str = "task"
    schema_version: str = "sisyphus_harness.contract_evaluation.v1"

    def __post_init__(self) -> None:
        _validate_digest(self.contract_digest, "contract evaluation contract digest")
        _validate_digest(
            self.observation_set_digest,
            "contract evaluation observation-set digest",
        )
        _validate_token(self.evaluator_version, "contract evaluator version")
        _validate_digest(self.evaluator_digest, "contract evaluator digest")
        if not isinstance(self.lifecycle, EvaluationLifecycle):
            raise ValueError("contract evaluation lifecycle is invalid")
        predicates = _strict_model_tuple(
            self.predicates,
            "contract evaluation predicates",
        )
        if any(not isinstance(item, PredicateEvaluation) for item in predicates):
            raise ValueError("contract evaluation predicates are invalid")
        if len({item.clause_id for item in predicates}) != len(predicates):
            raise ValueError("contract evaluation predicate clause IDs must be unique")
        object.__setattr__(self, "predicates", predicates)
        if self.lifecycle is EvaluationLifecycle.COMPLETED:
            if not isinstance(self.logical_result, LogicalResult):
                raise ValueError("completed contract evaluation requires a logical result")
            if self.error_code is not None:
                raise ValueError("completed contract evaluation cannot have an error code")
        else:
            if self.logical_result is not None:
                raise ValueError("errored contract evaluation cannot have a logical result")
            if self.error_code is None:
                raise ValueError("errored contract evaluation requires an error code")
            _validate_token(self.error_code, "contract evaluation error code")
        if self.scope != "task":
            raise ValueError("contract evaluation scope must be task")
        if self.schema_version != "sisyphus_harness.contract_evaluation.v1":
            raise ValueError("unsupported contract evaluation schema")

    def content_payload(self) -> dict[str, object]:
        return WireModel.to_dict(self)

    @property
    def evaluation_digest(self) -> str:
        return sha256_digest(self.content_payload())

    def to_dict(self) -> dict[str, object]:
        payload = self.content_payload()
        payload["evaluation_digest"] = self.evaluation_digest
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> ContractEvaluation:
        raw = strict_object(
            raw,
            required={
                "contract_digest",
                "observation_set_digest",
                "evaluator_version",
                "evaluator_digest",
                "lifecycle",
                "logical_result",
                "predicates",
                "error_code",
                "scope",
                "schema_version",
                "evaluation_digest",
            },
            label="contract evaluation",
        )
        predicates = _object_list(raw["predicates"], "contract evaluation predicates")
        result_raw = raw["logical_result"]
        evaluation = cls(
            contract_digest=_digest(
                raw["contract_digest"],
                "contract evaluation contract digest",
            ),
            observation_set_digest=_digest(
                raw["observation_set_digest"],
                "contract evaluation observation-set digest",
            ),
            evaluator_version=_string(
                raw["evaluator_version"],
                "contract evaluator version",
            ),
            evaluator_digest=_digest(
                raw["evaluator_digest"],
                "contract evaluator digest",
            ),
            lifecycle=_enum(
                EvaluationLifecycle,
                raw["lifecycle"],
                "contract evaluation lifecycle",
            ),
            logical_result=(
                _enum(LogicalResult, result_raw, "contract evaluation logical result")
                if result_raw is not None
                else None
            ),
            predicates=tuple(PredicateEvaluation.from_dict(item) for item in predicates),
            error_code=_optional_string(
                raw["error_code"],
                "contract evaluation error code",
            ),
            scope=_string(raw["scope"], "contract evaluation scope"),
            schema_version=_string(
                raw["schema_version"],
                "contract evaluation schema",
            ),
        )
        recorded = _digest(
            raw["evaluation_digest"],
            "contract evaluation digest",
        )
        if recorded != evaluation.evaluation_digest:
            raise ValueError("contract evaluation digest does not match content")
        return evaluation


@dataclass(frozen=True, slots=True)
class GapClosureEvaluation(WireModel):
    rule_digest: str
    requirement_id: str
    gap_id: str
    grounding_revision: int
    observation_set_digest: str
    evaluator_version: str
    evaluator_digest: str
    lifecycle: EvaluationLifecycle
    closure_result: GapClosureResult | None
    predicates: tuple[PredicateEvaluation, ...]
    error_code: str | None
    schema_version: str = "sisyphus_harness.gap_closure_evaluation.v1"

    def __post_init__(self) -> None:
        _validate_digest(self.rule_digest, "Gap closure evaluation rule digest")
        _validate_token(self.requirement_id, "Gap closure evaluation requirement ID")
        _validate_token(self.gap_id, "Gap closure evaluation Gap ID")
        _validate_nonnegative_integer(
            self.grounding_revision,
            "Gap closure grounding revision",
        )
        _validate_digest(
            self.observation_set_digest,
            "Gap closure observation-set digest",
        )
        _validate_token(self.evaluator_version, "Gap closure evaluator version")
        _validate_digest(self.evaluator_digest, "Gap closure evaluator digest")
        if not isinstance(self.lifecycle, EvaluationLifecycle):
            raise ValueError("Gap closure evaluation lifecycle is invalid")
        predicates = _strict_model_tuple(
            self.predicates,
            "Gap closure evaluation predicates",
        )
        if any(not isinstance(item, PredicateEvaluation) for item in predicates):
            raise ValueError("Gap closure predicates are invalid")
        if len({item.clause_id for item in predicates}) != len(predicates):
            raise ValueError("Gap closure predicate clause IDs must be unique")
        object.__setattr__(self, "predicates", predicates)
        if self.lifecycle is EvaluationLifecycle.COMPLETED:
            if not isinstance(self.closure_result, GapClosureResult):
                raise ValueError("completed Gap closure evaluation requires a result")
            if self.error_code is not None:
                raise ValueError("completed Gap closure evaluation cannot have an error")
        else:
            if self.closure_result is not None:
                raise ValueError("errored Gap closure evaluation cannot have a result")
            if self.error_code is None:
                raise ValueError("errored Gap closure evaluation requires an error code")
            _validate_token(self.error_code, "Gap closure evaluation error code")
        if self.schema_version != "sisyphus_harness.gap_closure_evaluation.v1":
            raise ValueError("unsupported Gap closure evaluation schema")

    def content_payload(self) -> dict[str, object]:
        return WireModel.to_dict(self)

    @property
    def evaluation_digest(self) -> str:
        return sha256_digest(self.content_payload())

    def to_dict(self) -> dict[str, object]:
        payload = self.content_payload()
        payload["evaluation_digest"] = self.evaluation_digest
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> GapClosureEvaluation:
        raw = strict_object(
            raw,
            required={
                "rule_digest",
                "requirement_id",
                "gap_id",
                "grounding_revision",
                "observation_set_digest",
                "evaluator_version",
                "evaluator_digest",
                "lifecycle",
                "closure_result",
                "predicates",
                "error_code",
                "schema_version",
                "evaluation_digest",
            },
            label="Gap closure evaluation",
        )
        predicates = _object_list(raw["predicates"], "Gap closure predicates")
        result_raw = raw["closure_result"]
        evaluation = cls(
            rule_digest=_digest(raw["rule_digest"], "Gap closure rule digest"),
            requirement_id=_string(
                raw["requirement_id"],
                "Gap closure requirement ID",
            ),
            gap_id=_string(raw["gap_id"], "Gap closure Gap ID"),
            grounding_revision=_integer(
                raw["grounding_revision"],
                "Gap closure grounding revision",
            ),
            observation_set_digest=_digest(
                raw["observation_set_digest"],
                "Gap closure observation-set digest",
            ),
            evaluator_version=_string(
                raw["evaluator_version"],
                "Gap closure evaluator version",
            ),
            evaluator_digest=_digest(
                raw["evaluator_digest"],
                "Gap closure evaluator digest",
            ),
            lifecycle=_enum(
                EvaluationLifecycle,
                raw["lifecycle"],
                "Gap closure evaluation lifecycle",
            ),
            closure_result=(
                _enum(GapClosureResult, result_raw, "Gap closure result")
                if result_raw is not None
                else None
            ),
            predicates=tuple(PredicateEvaluation.from_dict(item) for item in predicates),
            error_code=_optional_string(
                raw["error_code"],
                "Gap closure evaluation error code",
            ),
            schema_version=_string(
                raw["schema_version"],
                "Gap closure evaluation schema",
            ),
        )
        recorded = _digest(
            raw["evaluation_digest"],
            "Gap closure evaluation digest",
        )
        if recorded != evaluation.evaluation_digest:
            raise ValueError("Gap closure evaluation digest does not match content")
        return evaluation


def _expression_from_dict(
    raw: object,
    *,
    depth: int,
    counter: list[int],
) -> EvidenceExpression:
    if depth > _HARD_MAX_EXPRESSION_DEPTH:
        raise ValueError("evidence expression exceeds the hard depth limit")
    counter[0] += 1
    if counter[0] > _HARD_MAX_EXPRESSION_NODES:
        raise ValueError("evidence expression exceeds the hard node limit")
    if not isinstance(raw, dict):
        raise ValueError("evidence expression must be an object")
    kind = raw.get("kind")
    if kind == "clause_ref":
        parsed = strict_object(
            raw,
            required={"kind", "clause_id"},
            label="clause_ref expression",
        )
        return ClauseRef(_string(parsed["clause_id"], "expression clause ID"))
    if kind in {"all_of", "any_of", "at_least"}:
        required = {"kind", "children"}
        if kind == "at_least":
            required.add("minimum")
        parsed = strict_object(raw, required=required, label=f"{kind} expression")
        children_raw = parsed["children"]
        if not isinstance(children_raw, list) or not children_raw:
            raise ValueError(f"{kind} expression requires children")
        children = tuple(
            _expression_from_dict(item, depth=depth + 1, counter=counter)
            for item in children_raw
        )
        if kind == "all_of":
            return AllOf(children)
        if kind == "any_of":
            return AnyOf(children)
        return AtLeast(
            _integer(parsed["minimum"], "at_least minimum"),
            children,
        )
    if kind == "not":
        parsed = strict_object(
            raw,
            required={"kind", "child"},
            label="not expression",
        )
        return Not(
            _expression_from_dict(
                parsed["child"],
                depth=depth + 1,
                counter=counter,
            )
        )
    raise ValueError("evidence expression kind is unsupported")


def _validate_expression_children(
    children: tuple[EvidenceExpression, ...],
    label: str,
) -> tuple[EvidenceExpression, ...]:
    values = _strict_model_tuple(children, f"{label} expression children")
    if not values or any(type(child) not in _EXPRESSION_TYPES for child in values):
        raise ValueError(f"{label} expression requires valid children")
    return values


def _validate_expression_hard_limits(expression: EvidenceExpression) -> None:
    if type(expression) not in _EXPRESSION_TYPES:
        raise ValueError("evidence expression is invalid")
    nodes = 0
    stack: list[tuple[EvidenceExpression, int]] = [(expression, 1)]
    while stack:
        current, depth = stack.pop()
        current_type = type(current)
        if current_type not in _EXPRESSION_TYPES:
            raise ValueError("evidence expression contains an invalid node")
        nodes += 1
        if nodes > _HARD_MAX_EXPRESSION_NODES:
            raise ValueError("evidence expression exceeds the hard node limit")
        if depth > _HARD_MAX_EXPRESSION_DEPTH:
            raise ValueError("evidence expression exceeds the hard depth limit")
        if current_type in (AllOf, AnyOf, AtLeast):
            stack.extend((child, depth + 1) for child in reversed(current.children))
        elif current_type is Not:
            stack.append((current.child, depth + 1))


def _normalize_token_tuple(
    raw: tuple[str, ...],
    label: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if type(raw) is not tuple:
        raise ValueError(f"{label} must be a tuple")
    values = raw
    if not values and not allow_empty:
        raise ValueError(f"{label} must be non-empty")
    for value in values:
        _validate_token(value, label)
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")
    return values


def _strict_model_tuple(raw: object, label: str) -> tuple:
    if type(raw) is not tuple:
        raise ValueError(f"{label} must be a built-in tuple")
    return raw


def _normalize_evidence_value(raw: object, label: str) -> EvidenceValue:
    raw_type = type(raw)
    if raw_type is bool:
        return raw
    if raw_type is int:
        if abs(raw) > _MAX_INTEGER:
            raise ValueError(f"{label} integer is out of range")
        return raw
    if raw_type is str:
        if len(raw) > _MAX_STRING_VALUE_LENGTH or "\0" in raw:
            raise ValueError(f"{label} string is invalid")
        return raw
    if raw_type in (list, tuple):
        if len(raw) > _MAX_SEQUENCE_VALUE_LENGTH:
            raise ValueError(f"{label} sequence is too large")
        values: list[EvidenceScalar] = []
        for item in raw:
            normalized = _normalize_evidence_value(item, label)
            if type(normalized) is tuple:
                raise ValueError(f"{label} cannot contain nested sequences")
            values.append(normalized)
        return tuple(values)
    raise ValueError(f"{label} must be a boolean, integer, string, or scalar list")


def _parse_evidence_value(raw: object, label: str) -> EvidenceValue:
    return _normalize_evidence_value(raw, label)


def _validate_token(value: str, label: str) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > _MAX_TOKEN_LENGTH
        or "\0" in value
        or value.strip() != value
        or any(character.isspace() for character in value)
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{label} must be a bounded non-whitespace token")


def _validate_digest(value: str, label: str) -> None:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be SHA-256")


def _validate_positive_integer(value: int, label: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")


def _validate_nonnegative_integer(value: int, label: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")


def _string(raw: object, label: str) -> str:
    if type(raw) is not str:
        raise ValueError(f"{label} must be a string")
    return raw


def _optional_string(raw: object, label: str) -> str | None:
    if raw is None:
        return None
    return _string(raw, label)


def _integer(raw: object, label: str) -> int:
    if type(raw) is not int:
        raise ValueError(f"{label} must be an integer")
    return raw


def _string_tuple(raw: object, label: str) -> tuple[str, ...]:
    if type(raw) is not list or any(type(item) is not str for item in raw):
        raise ValueError(f"{label} must be a string list")
    return tuple(raw)


def _object_list(raw: object, label: str) -> list[object]:
    if type(raw) is not list:
        raise ValueError(f"{label} must be a list")
    return raw


def _digest(raw: object, label: str) -> str:
    value = _string(raw, label)
    _validate_digest(value, label)
    return value


def _enum(enum_type, raw: object, label: str):
    value = _string(raw, label)
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"{label} is unsupported") from exc
