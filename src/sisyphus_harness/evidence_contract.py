from __future__ import annotations

from dataclasses import dataclass

from .contracts.codec import canonical_json_bytes, sha256_digest
from .contracts.evidence_contract import (
    AllOf,
    AnyOf,
    AtLeast,
    ClauseRef,
    ContractEvaluation,
    EvaluationLifecycle,
    EvidenceClause,
    EvidenceContract,
    EvidenceExpression,
    EvidenceObservation,
    EvidenceScalar,
    EvidenceValue,
    GapClosureEvaluation,
    GapClosureResult,
    GapClosureRule,
    LogicalResult,
    Not,
    ObservationStatus,
    PredicateEvaluation,
    PredicateOperator,
)


EVALUATOR_VERSION = "sisyphus_harness.evidence_evaluator.v2"
EVALUATOR_DIGEST = sha256_digest(
    {
        "version": EVALUATOR_VERSION,
        "logical_values": ["pass", "fail", "indeterminate"],
        "aggregation": "strong-kleene-v1",
        "selector_cardinality": "exactly-one-v1",
        "predicate_semantics": "typed-fail-closed-v2",
    }
)


@dataclass(frozen=True, slots=True)
class EvidenceContractLimits:
    max_clauses: int = 128
    max_expression_nodes: int = 512
    max_expression_depth: int = 32
    max_contract_bytes: int = 256 * 1024
    max_observations: int = 1024

    def __post_init__(self) -> None:
        for label, value in (
            ("max_clauses", self.max_clauses),
            ("max_expression_nodes", self.max_expression_nodes),
            ("max_expression_depth", self.max_expression_depth),
            ("max_contract_bytes", self.max_contract_bytes),
            ("max_observations", self.max_observations),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f"evidence contract {label} must be positive")


DEFAULT_LIMITS = EvidenceContractLimits()


@dataclass(frozen=True, slots=True, order=True)
class ContractLintIssue:
    code: str
    location: str
    message: str


def lint_evidence_contract(
    contract: EvidenceContract,
    *,
    limits: EvidenceContractLimits = DEFAULT_LIMITS,
) -> tuple[ContractLintIssue, ...]:
    if type(contract) is not EvidenceContract:
        raise TypeError("contract must be an exact EvidenceContract")
    _require_limits(limits)
    return _lint_rule_set(
        clauses=contract.clauses,
        expression=contract.task_success,
        payload=contract.content_payload(),
        label="contract",
        limits=limits,
    )


def lint_gap_closure_rule(
    rule: GapClosureRule,
    *,
    limits: EvidenceContractLimits = DEFAULT_LIMITS,
) -> tuple[ContractLintIssue, ...]:
    if type(rule) is not GapClosureRule:
        raise TypeError("rule must be an exact GapClosureRule")
    _require_limits(limits)
    return _lint_rule_set(
        clauses=rule.clauses,
        expression=rule.closure_condition,
        payload=rule.content_payload(),
        label="closure_rule",
        limits=limits,
    )


def evaluate_evidence_contract(
    contract: EvidenceContract,
    observations: tuple[EvidenceObservation, ...],
    *,
    limits: EvidenceContractLimits = DEFAULT_LIMITS,
) -> ContractEvaluation:
    if type(contract) is not EvidenceContract:
        raise TypeError("contract must be an exact EvidenceContract")
    _require_limits(limits)
    values = _observation_tuple(observations)
    if len(values) > limits.max_observations:
        return ContractEvaluation(
            contract_digest=contract.contract_digest,
            observation_set_digest=_rejected_observation_set_digest(
                count=len(values),
                limit=limits.max_observations,
            ),
            evaluator_version=EVALUATOR_VERSION,
            evaluator_digest=EVALUATOR_DIGEST,
            lifecycle=EvaluationLifecycle.ERROR,
            logical_result=None,
            predicates=(),
            error_code="too_many_observations",
        )
    values = _normalize_observations(values)
    observation_digest = observation_set_digest(values, limits=limits)
    issues = lint_evidence_contract(contract, limits=limits)
    if issues:
        return ContractEvaluation(
            contract_digest=contract.contract_digest,
            observation_set_digest=observation_digest,
            evaluator_version=EVALUATOR_VERSION,
            evaluator_digest=EVALUATOR_DIGEST,
            lifecycle=EvaluationLifecycle.ERROR,
            logical_result=None,
            predicates=(),
            error_code="contract_lint_failed",
        )
    input_error = _observation_input_error(values, limits=limits)
    if input_error is not None:
        return ContractEvaluation(
            contract_digest=contract.contract_digest,
            observation_set_digest=observation_digest,
            evaluator_version=EVALUATOR_VERSION,
            evaluator_digest=EVALUATOR_DIGEST,
            lifecycle=EvaluationLifecycle.ERROR,
            logical_result=None,
            predicates=(),
            error_code=input_error,
        )
    predicates = _evaluate_clauses(contract.clauses, values)
    root = _evaluate_expression(
        contract.task_success,
        {predicate.clause_id: predicate.result for predicate in predicates},
    )
    return ContractEvaluation(
        contract_digest=contract.contract_digest,
        observation_set_digest=observation_digest,
        evaluator_version=EVALUATOR_VERSION,
        evaluator_digest=EVALUATOR_DIGEST,
        lifecycle=EvaluationLifecycle.COMPLETED,
        logical_result=root,
        predicates=predicates,
        error_code=None,
    )


def evaluate_gap_closure_rule(
    rule: GapClosureRule,
    observations: tuple[EvidenceObservation, ...],
    *,
    grounding_revision: int,
    limits: EvidenceContractLimits = DEFAULT_LIMITS,
) -> GapClosureEvaluation:
    if type(rule) is not GapClosureRule:
        raise TypeError("rule must be an exact GapClosureRule")
    _require_limits(limits)
    if (
        type(grounding_revision) is not int
        or grounding_revision < 0
    ):
        raise ValueError("grounding revision must be a non-negative integer")
    values = _observation_tuple(observations)
    if len(values) > limits.max_observations:
        return GapClosureEvaluation(
            rule_digest=rule.rule_digest,
            requirement_id=rule.requirement_id,
            gap_id=rule.gap_id,
            grounding_revision=grounding_revision,
            observation_set_digest=_rejected_observation_set_digest(
                count=len(values),
                limit=limits.max_observations,
            ),
            evaluator_version=EVALUATOR_VERSION,
            evaluator_digest=EVALUATOR_DIGEST,
            lifecycle=EvaluationLifecycle.ERROR,
            closure_result=None,
            predicates=(),
            error_code="too_many_observations",
        )
    values = _normalize_observations(values)
    observation_digest = observation_set_digest(values, limits=limits)
    issues = lint_gap_closure_rule(rule, limits=limits)
    if issues:
        return GapClosureEvaluation(
            rule_digest=rule.rule_digest,
            requirement_id=rule.requirement_id,
            gap_id=rule.gap_id,
            grounding_revision=grounding_revision,
            observation_set_digest=observation_digest,
            evaluator_version=EVALUATOR_VERSION,
            evaluator_digest=EVALUATOR_DIGEST,
            lifecycle=EvaluationLifecycle.ERROR,
            closure_result=None,
            predicates=(),
            error_code="closure_rule_lint_failed",
        )
    input_error = _observation_input_error(values, limits=limits)
    if input_error is not None:
        return GapClosureEvaluation(
            rule_digest=rule.rule_digest,
            requirement_id=rule.requirement_id,
            gap_id=rule.gap_id,
            grounding_revision=grounding_revision,
            observation_set_digest=observation_digest,
            evaluator_version=EVALUATOR_VERSION,
            evaluator_digest=EVALUATOR_DIGEST,
            lifecycle=EvaluationLifecycle.ERROR,
            closure_result=None,
            predicates=(),
            error_code=input_error,
        )
    predicates = _evaluate_clauses(rule.clauses, values)
    logical = _evaluate_expression(
        rule.closure_condition,
        {predicate.clause_id: predicate.result for predicate in predicates},
    )
    closure_result = {
        LogicalResult.PASS: GapClosureResult.CLOSED,
        LogicalResult.FAIL: GapClosureResult.NOT_CLOSED,
        LogicalResult.INDETERMINATE: GapClosureResult.INDETERMINATE,
    }[logical]
    return GapClosureEvaluation(
        rule_digest=rule.rule_digest,
        requirement_id=rule.requirement_id,
        gap_id=rule.gap_id,
        grounding_revision=grounding_revision,
        observation_set_digest=observation_digest,
        evaluator_version=EVALUATOR_VERSION,
        evaluator_digest=EVALUATOR_DIGEST,
        lifecycle=EvaluationLifecycle.COMPLETED,
        closure_result=closure_result,
        predicates=predicates,
        error_code=None,
    )


def observation_set_digest(
    observations: tuple[EvidenceObservation, ...],
    *,
    limits: EvidenceContractLimits = DEFAULT_LIMITS,
) -> str:
    values = _normalize_observations(observations)
    _require_limits(limits)
    if len(values) > limits.max_observations:
        raise ValueError(
            "observation set exceeds the configured observation limit"
        )
    ordered = sorted(
        values,
        key=lambda item: (item.observation_id, item.observation_digest),
    )
    return sha256_digest([item.to_dict() for item in ordered])


def _lint_rule_set(
    *,
    clauses: tuple[EvidenceClause, ...],
    expression: EvidenceExpression,
    payload: dict[str, object],
    label: str,
    limits: EvidenceContractLimits,
) -> tuple[ContractLintIssue, ...]:
    issues: list[ContractLintIssue] = []
    if len(clauses) > limits.max_clauses:
        issues.append(
            ContractLintIssue(
                "too_many_clauses",
                f"{label}.clauses",
                f"clause count exceeds {limits.max_clauses}",
            )
        )

    references: list[str] = []
    nodes = 0
    max_depth = 0
    stack: list[tuple[EvidenceExpression, int, str]] = [
        (expression, 1, f"{label}.expression")
    ]
    while stack:
        current, depth, location = stack.pop()
        nodes += 1
        max_depth = max(max_depth, depth)
        current_type = type(current)
        if current_type is ClauseRef:
            references.append(current.clause_id)
        elif current_type in (AllOf, AnyOf, AtLeast):
            for index in range(len(current.children) - 1, -1, -1):
                stack.append(
                    (
                        current.children[index],
                        depth + 1,
                        f"{location}.children[{index}]",
                    )
                )
        elif current_type is Not:
            stack.append((current.child, depth + 1, f"{location}.child"))
        else:
            raise TypeError("evidence expression nodes must use exact model types")

    if nodes > limits.max_expression_nodes:
        issues.append(
            ContractLintIssue(
                "expression_too_large",
                f"{label}.expression",
                f"expression node count exceeds {limits.max_expression_nodes}",
            )
        )
    if max_depth > limits.max_expression_depth:
        issues.append(
            ContractLintIssue(
                "expression_too_deep",
                f"{label}.expression",
                f"expression depth exceeds {limits.max_expression_depth}",
            )
        )

    known = {clause.clause_id for clause in clauses}
    seen: set[str] = set()
    for index, clause_id in enumerate(references):
        if clause_id not in known:
            issues.append(
                ContractLintIssue(
                    "unknown_clause_reference",
                    f"{label}.expression.references[{index}]",
                    f"unknown clause reference: {clause_id}",
                )
            )
        if clause_id in seen:
            issues.append(
                ContractLintIssue(
                    "duplicate_clause_reference",
                    f"{label}.expression.references[{index}]",
                    f"clause is referenced more than once: {clause_id}",
                )
            )
        seen.add(clause_id)
    for clause in clauses:
        if clause.clause_id not in seen:
            issues.append(
                ContractLintIssue(
                    "unused_clause",
                    f"{label}.clauses.{clause.clause_id}",
                    "clause is not referenced by the root expression",
                )
            )
        issue = _lint_clause(clause, label=label)
        if issue is not None:
            issues.append(issue)

    if len(canonical_json_bytes(payload)) > limits.max_contract_bytes:
        issues.append(
            ContractLintIssue(
                "contract_too_large",
                label,
                f"canonical contract exceeds {limits.max_contract_bytes} bytes",
            )
        )
    return tuple(sorted(issues))


def _lint_clause(
    clause: EvidenceClause,
    *,
    label: str,
) -> ContractLintIssue | None:
    expected = clause.expected
    if clause.operator in {
        PredicateOperator.LESS_THAN,
        PredicateOperator.LESS_THAN_OR_EQUAL,
        PredicateOperator.GREATER_THAN,
        PredicateOperator.GREATER_THAN_OR_EQUAL,
    } and type(expected) is not int:
        return ContractLintIssue(
            "invalid_expected_type",
            f"{label}.clauses.{clause.clause_id}.expected",
            "numeric comparison requires an integer expected value",
        )
    if clause.operator is PredicateOperator.CONTAINS and type(expected) is tuple:
        return ContractLintIssue(
            "invalid_expected_type",
            f"{label}.clauses.{clause.clause_id}.expected",
            "contains requires a scalar expected value",
        )
    if clause.operator in {
        PredicateOperator.SUBSET,
        PredicateOperator.DISJOINT,
    } and type(expected) is not tuple:
        return ContractLintIssue(
            "invalid_expected_type",
            f"{label}.clauses.{clause.clause_id}.expected",
            "set comparison requires a list expected value",
        )
    return None


def _normalize_observations(
    observations: tuple[EvidenceObservation, ...],
) -> tuple[EvidenceObservation, ...]:
    values = _observation_tuple(observations)
    if any(type(item) is not EvidenceObservation for item in values):
        raise TypeError("observations must contain exact EvidenceObservation values")
    return values


def _observation_tuple(
    observations: tuple[EvidenceObservation, ...],
) -> tuple[EvidenceObservation, ...]:
    if type(observations) is not tuple:
        raise TypeError("observations must be an immutable tuple")
    return observations


def _require_limits(limits: EvidenceContractLimits) -> None:
    if type(limits) is not EvidenceContractLimits:
        raise TypeError("limits must be EvidenceContractLimits")


def _rejected_observation_set_digest(*, count: int, limit: int) -> str:
    return sha256_digest(
        {
            "schema_version": "sisyphus_harness.rejected_observation_set.v1",
            "reason": "too_many_observations",
            "count": count,
            "limit": limit,
        }
    )


def _observation_input_error(
    observations: tuple[EvidenceObservation, ...],
    *,
    limits: EvidenceContractLimits,
) -> str | None:
    if len(observations) > limits.max_observations:
        return "too_many_observations"
    identifiers = [item.observation_id for item in observations]
    if len(set(identifiers)) != len(identifiers):
        return "duplicate_observation_id"
    return None


def _evaluate_clauses(
    clauses: tuple[EvidenceClause, ...],
    observations: tuple[EvidenceObservation, ...],
) -> tuple[PredicateEvaluation, ...]:
    results: list[PredicateEvaluation] = []
    for clause in clauses:
        matches = sorted(
            (
                observation
                for observation in observations
                if observation.selector == clause.selector
            ),
            key=lambda item: item.observation_id,
        )
        if not matches:
            results.append(
                PredicateEvaluation(
                    clause_id=clause.clause_id,
                    result=LogicalResult.INDETERMINATE,
                    observation_ids=(),
                    reason_code="missing_observation",
                )
            )
            continue
        if len(matches) != 1:
            results.append(
                PredicateEvaluation(
                    clause_id=clause.clause_id,
                    result=LogicalResult.INDETERMINATE,
                    observation_ids=tuple(item.observation_id for item in matches),
                    reason_code="ambiguous_observation",
                )
            )
            continue
        observation = matches[0]
        if observation.status is ObservationStatus.UNAVAILABLE:
            results.append(
                PredicateEvaluation(
                    clause_id=clause.clause_id,
                    result=LogicalResult.INDETERMINATE,
                    observation_ids=(observation.observation_id,),
                    reason_code="observation_unavailable",
                )
            )
            continue
        if observation.status is ObservationStatus.ERROR:
            results.append(
                PredicateEvaluation(
                    clause_id=clause.clause_id,
                    result=LogicalResult.INDETERMINATE,
                    observation_ids=(observation.observation_id,),
                    reason_code="observation_error",
                )
            )
            continue
        assert observation.value is not None
        result, reason = _evaluate_predicate(
            clause.operator,
            observation.value,
            clause.expected,
        )
        results.append(
            PredicateEvaluation(
                clause_id=clause.clause_id,
                result=result,
                observation_ids=(observation.observation_id,),
                reason_code=reason,
            )
        )
    return tuple(results)


def _evaluate_predicate(
    operator: PredicateOperator,
    actual: EvidenceValue,
    expected: EvidenceValue,
) -> tuple[LogicalResult, str]:
    if operator is PredicateOperator.EQUALS:
        passed = _typed_equal(actual, expected)
    elif operator is PredicateOperator.NOT_EQUALS:
        if not _same_value_schema(actual, expected):
            return LogicalResult.INDETERMINATE, "observation_type_mismatch"
        passed = not _typed_equal(actual, expected)
    elif operator in {
        PredicateOperator.LESS_THAN,
        PredicateOperator.LESS_THAN_OR_EQUAL,
        PredicateOperator.GREATER_THAN,
        PredicateOperator.GREATER_THAN_OR_EQUAL,
    }:
        if not _is_integer(actual) or not _is_integer(expected):
            return LogicalResult.INDETERMINATE, "observation_type_mismatch"
        if operator is PredicateOperator.LESS_THAN:
            passed = actual < expected
        elif operator is PredicateOperator.LESS_THAN_OR_EQUAL:
            passed = actual <= expected
        elif operator is PredicateOperator.GREATER_THAN:
            passed = actual > expected
        else:
            passed = actual >= expected
    elif operator is PredicateOperator.CONTAINS:
        if type(actual) is not tuple or type(expected) is tuple:
            return LogicalResult.INDETERMINATE, "observation_type_mismatch"
        passed = _typed_key(expected) in {_typed_key(item) for item in actual}
    elif operator in {PredicateOperator.SUBSET, PredicateOperator.DISJOINT}:
        if type(actual) is not tuple or type(expected) is not tuple:
            return LogicalResult.INDETERMINATE, "observation_type_mismatch"
        if (
            operator is PredicateOperator.DISJOINT
            and actual
            and expected
            and {type(item) for item in actual} != {type(item) for item in expected}
        ):
            return LogicalResult.INDETERMINATE, "observation_type_mismatch"
        actual_set = {_typed_key(item) for item in actual}
        expected_set = {_typed_key(item) for item in expected}
        if operator is PredicateOperator.SUBSET:
            passed = actual_set.issubset(expected_set)
        else:
            passed = actual_set.isdisjoint(expected_set)
    else:  # pragma: no cover - enum exhaustiveness guard
        raise AssertionError(f"unsupported predicate operator: {operator}")
    if passed:
        return LogicalResult.PASS, "predicate_satisfied"
    return LogicalResult.FAIL, "predicate_not_satisfied"


def _evaluate_expression(
    expression: EvidenceExpression,
    predicates: dict[str, LogicalResult],
) -> LogicalResult:
    expression_type = type(expression)
    if expression_type is ClauseRef:
        return predicates[expression.clause_id]
    if expression_type is AllOf:
        results = tuple(
            _evaluate_expression(child, predicates) for child in expression.children
        )
        if LogicalResult.FAIL in results:
            return LogicalResult.FAIL
        if all(result is LogicalResult.PASS for result in results):
            return LogicalResult.PASS
        return LogicalResult.INDETERMINATE
    if expression_type is AnyOf:
        results = tuple(
            _evaluate_expression(child, predicates) for child in expression.children
        )
        if LogicalResult.PASS in results:
            return LogicalResult.PASS
        if all(result is LogicalResult.FAIL for result in results):
            return LogicalResult.FAIL
        return LogicalResult.INDETERMINATE
    if expression_type is AtLeast:
        results = tuple(
            _evaluate_expression(child, predicates) for child in expression.children
        )
        passes = sum(result is LogicalResult.PASS for result in results)
        indeterminate = sum(
            result is LogicalResult.INDETERMINATE for result in results
        )
        if passes >= expression.minimum:
            return LogicalResult.PASS
        if passes + indeterminate < expression.minimum:
            return LogicalResult.FAIL
        return LogicalResult.INDETERMINATE
    if expression_type is Not:
        result = _evaluate_expression(expression.child, predicates)
        if result is LogicalResult.PASS:
            return LogicalResult.FAIL
        if result is LogicalResult.FAIL:
            return LogicalResult.PASS
        return LogicalResult.INDETERMINATE
    raise AssertionError("unsupported evidence expression")  # pragma: no cover


def _typed_equal(left: EvidenceValue, right: EvidenceValue) -> bool:
    if type(left) is tuple or type(right) is tuple:
        if type(left) is not tuple or type(right) is not tuple:
            return False
        return len(left) == len(right) and all(
            _typed_key(left_item) == _typed_key(right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return _typed_key(left) == _typed_key(right)


def _typed_key(value: EvidenceScalar) -> tuple[str, EvidenceScalar]:
    if type(value) is bool:
        return "bool", value
    if type(value) is int:
        return "int", value
    if type(value) is str:
        return "str", value
    raise TypeError("evidence scalar must use an exact built-in type")


def _same_value_schema(left: EvidenceValue, right: EvidenceValue) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is not tuple:
        return True
    return all(
        type(left_item) is type(right_item)
        for left_item, right_item in zip(left, right)
    )


def _is_integer(value: EvidenceValue) -> bool:
    return type(value) is int
