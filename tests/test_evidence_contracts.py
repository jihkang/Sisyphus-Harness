from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from sisyphus_harness.contracts.evidence_contract import (
    AllOf,
    AnyOf,
    AtLeast,
    ClauseRef,
    ContractEvaluation,
    EvaluationLifecycle,
    EvidenceClause,
    EvidenceContract,
    EvidenceObservation,
    EvidenceSelector,
    GapClosureEvaluation,
    GapClosureResult,
    GapClosureRule,
    LogicalResult,
    Not,
    ObservationStatus,
    PredicateEvaluation,
    PredicateOperator,
    expression_from_dict,
)
from sisyphus_harness.evidence_contract import (
    EvidenceContractLimits,
    evaluate_evidence_contract,
    evaluate_gap_closure_rule,
    lint_evidence_contract,
    lint_gap_closure_rule,
    observation_set_digest,
)


_SUBJECT = "sha256:" + "1" * 64
_ARTIFACT = "sha256:" + "2" * 64
_PROFILE = "sha256:" + "3" * 64
_ADAPTER = "sha256:" + "4" * 64


class _StatefulObservationTuple(tuple):
    def __iter__(self):
        raise AssertionError("tuple subclass must be rejected before iteration")


class _AdversarialInt(int):
    def __gt__(self, other):
        return True

    def __le__(self, other):
        return True


class _AdversarialString(str):
    def __eq__(self, other):
        return True

    __hash__ = str.__hash__


class _AdversarialList(list):
    pass


class _EvidenceObservationSubclass(EvidenceObservation):
    pass


class _EvidenceSelectorSubclass(EvidenceSelector):
    pass


class _EvidenceClauseSubclass(EvidenceClause):
    pass


class _EvidenceContractSubclass(EvidenceContract):
    pass


class _ClauseRefSubclass(ClauseRef):
    pass


class _AllOfSubclass(AllOf):
    pass


class _AnyOfSubclass(AnyOf):
    pass


class _AtLeastSubclass(AtLeast):
    pass


class _NotSubclass(Not):
    pass


def _selector(
    check_id: str,
    *,
    stage: str = "candidate",
    observation_type: str = "test_status",
) -> EvidenceSelector:
    return EvidenceSelector(
        observation_type=observation_type,
        stage=stage,
        check_id=check_id,
        producer_authority="verifier",
    )


def _clause(
    clause_id: str,
    *,
    expected=True,
    operator: PredicateOperator = PredicateOperator.EQUALS,
) -> EvidenceClause:
    return EvidenceClause(
        clause_id=clause_id,
        selector=_selector(f"check:{clause_id}"),
        operator=operator,
        expected=expected,
    )


def _contract(
    clauses: tuple[EvidenceClause, ...],
    expression,
    *,
    contract_id: str = "contract-1",
) -> EvidenceContract:
    return EvidenceContract(
        contract_id=contract_id,
        version=1,
        requirement_ids=("requirement-1",),
        gap_ids=("gap-1",),
        task_basis_ids=("basis-1",),
        verification_profile_digest=_PROFILE,
        observation_adapter_digest=_ADAPTER,
        clauses=clauses,
        task_success=expression,
    )


def _observation(
    clause: EvidenceClause,
    value=True,
    *,
    observation_id: str | None = None,
    status: ObservationStatus = ObservationStatus.OBSERVED,
) -> EvidenceObservation:
    return EvidenceObservation(
        observation_id=observation_id or f"observation:{clause.clause_id}",
        selector=clause.selector,
        subject_digest=_SUBJECT,
        source_run_id="verification-run-1",
        artifact_digest=_ARTIFACT,
        status=status,
        value=value if status is ObservationStatus.OBSERVED else None,
        reason_code=None if status is ObservationStatus.OBSERVED else "runner_unavailable",
    )


class EvidenceContractWireTests(unittest.TestCase):
    def test_nested_component_round_trips_and_digests(self) -> None:
        selector = _selector("component-check")
        clause = EvidenceClause(
            clause_id="component",
            selector=selector,
            operator=PredicateOperator.EQUALS,
            expected=True,
        )
        predicate = PredicateEvaluation(
            clause_id="component",
            result=LogicalResult.PASS,
            observation_ids=("observation-1",),
            reason_code="predicate_satisfied",
        )

        self.assertEqual(EvidenceSelector.from_dict(selector.to_dict()), selector)
        self.assertEqual(EvidenceClause.from_dict(clause.to_dict()), clause)
        self.assertEqual(PredicateEvaluation.from_dict(predicate.to_dict()), predicate)
        self.assertTrue(selector.selector_digest.startswith("sha256:"))
        self.assertTrue(clause.clause_digest.startswith("sha256:"))
        self.assertTrue(predicate.evaluation_digest.startswith("sha256:"))

    def test_contract_expression_and_digest_round_trip_strictly(self) -> None:
        clauses = tuple(_clause(name) for name in "abcdef")
        expression = AllOf(
            (
                ClauseRef("a"),
                AnyOf((ClauseRef("b"), ClauseRef("c"))),
                AtLeast(1, (ClauseRef("d"), ClauseRef("e"))),
                Not(ClauseRef("f")),
            )
        )
        contract = _contract(clauses, expression)

        payload = contract.to_dict()
        self.assertEqual(EvidenceContract.from_dict(payload), contract)
        self.assertEqual(expression_from_dict(expression.to_dict()), expression)
        self.assertEqual(payload["contract_digest"], contract.contract_digest)

        tampered = contract.to_dict()
        tampered["version"] = 2
        with self.assertRaisesRegex(ValueError, "digest does not match"):
            EvidenceContract.from_dict(tampered)

        unknown = contract.to_dict()
        unknown["gap_closure"] = ClauseRef("a").to_dict()
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            EvidenceContract.from_dict(unknown)

    def test_expression_parser_rejects_code_unknown_fields_and_invalid_threshold(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported"):
            expression_from_dict({"kind": "python", "code": "return True"})
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            expression_from_dict(
                {"kind": "clause_ref", "clause_id": "a", "command": "true"}
            )
        with self.assertRaisesRegex(ValueError, "within its child count"):
            expression_from_dict(
                {
                    "kind": "at_least",
                    "minimum": 2,
                    "children": [{"kind": "clause_ref", "clause_id": "a"}],
                }
            )

    def test_wire_models_are_frozen_and_evidence_values_are_bounded(self) -> None:
        clause = EvidenceClause(
            clause_id="paths",
            selector=_selector("changed-paths", observation_type="path_set"),
            operator=PredicateOperator.DISJOINT,
            expected=["protected.py"],
        )
        self.assertEqual(clause.expected, ("protected.py",))
        with self.assertRaises(FrozenInstanceError):
            clause.clause_id = "changed"  # type: ignore[misc]
        with self.assertRaisesRegex(ValueError, "boolean, integer, string"):
            EvidenceClause(
                clause_id="float",
                selector=_selector("float"),
                operator=PredicateOperator.EQUALS,
                expected=1.5,  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ValueError, "nested"):
            EvidenceClause(
                clause_id="nested",
                selector=_selector("nested"),
                operator=PredicateOperator.EQUALS,
                expected=(("nested",),),  # type: ignore[arg-type]
            )

    def test_direct_constructors_do_not_split_strings_into_identifier_tuples(self) -> None:
        clause = _clause("strict-identifiers")
        values = {
            "contract_id": "contract-strict-identifiers",
            "version": 1,
            "requirement_ids": ("requirement-1",),
            "gap_ids": ("gap-1",),
            "task_basis_ids": ("basis-1",),
            "verification_profile_digest": _PROFILE,
            "observation_adapter_digest": _ADAPTER,
            "clauses": (clause,),
            "task_success": ClauseRef(clause.clause_id),
        }
        for field in ("requirement_ids", "gap_ids", "task_basis_ids"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "must be a tuple"):
                    EvidenceContract(**(values | {field: "identifier"}))  # type: ignore[arg-type]

        stateful_identifiers = _StatefulObservationTuple(("identifier",))
        with self.assertRaisesRegex(ValueError, "must be a tuple"):
            EvidenceContract(
                **(values | {"requirement_ids": stateful_identifiers})
            )

        with self.assertRaisesRegex(ValueError, "must be a tuple"):
            PredicateEvaluation(
                clause_id="strict-identifiers",
                result=LogicalResult.PASS,
                observation_ids="observation",  # type: ignore[arg-type]
                reason_code="predicate_satisfied",
            )

    def test_observation_round_trip_and_status_invariants(self) -> None:
        clause = _clause("green")
        observation = _observation(clause)
        self.assertEqual(
            EvidenceObservation.from_dict(observation.to_dict()),
            observation,
        )

        tampered = observation.to_dict()
        tampered["value"] = False
        with self.assertRaisesRegex(ValueError, "digest does not match"):
            EvidenceObservation.from_dict(tampered)
        with self.assertRaisesRegex(ValueError, "cannot have a reason"):
            EvidenceObservation(
                observation_id="observed-with-error",
                selector=clause.selector,
                subject_digest=_SUBJECT,
                source_run_id="run-1",
                artifact_digest=_ARTIFACT,
                status=ObservationStatus.OBSERVED,
                value=True,
                reason_code="unexpected",
            )
        with self.assertRaisesRegex(ValueError, "cannot have a value"):
            EvidenceObservation(
                observation_id="unavailable-with-value",
                selector=clause.selector,
                subject_digest=_SUBJECT,
                source_run_id="run-1",
                artifact_digest=_ARTIFACT,
                status=ObservationStatus.UNAVAILABLE,
                value=True,
                reason_code="offline",
            )

    def test_operator_and_lifecycle_values_are_strict(self) -> None:
        raw_clause = _clause("green").to_dict()
        raw_clause["operator"] = "shell"
        with self.assertRaisesRegex(ValueError, "unsupported"):
            EvidenceClause.from_dict(raw_clause)

        contract = _contract((_clause("a"),), ClauseRef("a"))
        observation_digest = observation_set_digest(())
        with self.assertRaisesRegex(ValueError, "requires a logical result"):
            ContractEvaluation(
                contract_digest=contract.contract_digest,
                observation_set_digest=observation_digest,
                evaluator_version="evaluator-v1",
                evaluator_digest=_ARTIFACT,
                lifecycle=EvaluationLifecycle.COMPLETED,
                logical_result=None,
                predicates=(),
                error_code=None,
            )
        with self.assertRaisesRegex(ValueError, "cannot have a logical result"):
            ContractEvaluation(
                contract_digest=contract.contract_digest,
                observation_set_digest=observation_digest,
                evaluator_version="evaluator-v1",
                evaluator_digest=_ARTIFACT,
                lifecycle=EvaluationLifecycle.ERROR,
                logical_result=LogicalResult.PASS,
                predicates=(),
                error_code="broken",
            )

    def test_contract_and_rule_constructor_invariants_fail_closed(self) -> None:
        clause = _clause("one")
        duplicate = _clause("one")
        base = {
            "contract_id": "contract-invalid",
            "version": 1,
            "requirement_ids": ("requirement-1",),
            "gap_ids": ("gap-1",),
            "task_basis_ids": ("basis-1",),
            "verification_profile_digest": _PROFILE,
            "observation_adapter_digest": _ADAPTER,
            "clauses": (clause,),
            "task_success": ClauseRef("one"),
        }
        invalid_contracts = (
            ({**base, "version": 0}, "positive integer"),
            ({**base, "requirement_ids": ()}, "non-empty"),
            ({**base, "clauses": ()}, "requires evidence clauses"),
            ({**base, "clauses": (clause, duplicate)}, "must be unique"),
            ({**base, "task_success": "one"}, "expression is invalid"),
            ({**base, "schema_version": "v2"}, "unsupported"),
        )
        for arguments, message in invalid_contracts:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                EvidenceContract(**arguments)  # type: ignore[arg-type]

        with self.assertRaisesRegex(ValueError, "requires evidence clauses"):
            GapClosureRule(
                rule_id="rule-empty",
                version=1,
                requirement_id="requirement-1",
                gap_id="gap-1",
                clauses=(),
                closure_condition=ClauseRef("one"),
            )
        with self.assertRaisesRegex(ValueError, "must be unique"):
            GapClosureRule(
                rule_id="rule-duplicate",
                version=1,
                requirement_id="requirement-1",
                gap_id="gap-1",
                clauses=(clause, duplicate),
                closure_condition=ClauseRef("one"),
            )

    def test_observation_rejects_invalid_identity_status_and_missing_details(self) -> None:
        clause = _clause("green")
        base = {
            "observation_id": "observation-1",
            "selector": clause.selector,
            "subject_digest": _SUBJECT,
            "source_run_id": "run-1",
            "artifact_digest": _ARTIFACT,
            "status": ObservationStatus.OBSERVED,
            "value": True,
            "reason_code": None,
        }
        invalid = (
            ({**base, "selector": "invalid"}, "selector is invalid"),
            ({**base, "subject_digest": "invalid"}, "must be SHA-256"),
            ({**base, "status": "observed"}, "status is invalid"),
            ({**base, "value": None}, "requires a value"),
            (
                {
                    **base,
                    "status": ObservationStatus.UNAVAILABLE,
                    "value": None,
                    "reason_code": None,
                },
                "requires a reason code",
            ),
            ({**base, "schema_version": "v2"}, "unsupported"),
        )
        for arguments, message in invalid:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                EvidenceObservation(**arguments)  # type: ignore[arg-type]


class EvidenceContractEvaluationTests(unittest.TestCase):
    def test_public_evaluator_type_guards(self) -> None:
        with self.assertRaisesRegex(TypeError, "EvidenceContract"):
            lint_evidence_contract("contract")  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "EvidenceContract"):
            evaluate_evidence_contract("contract", ())  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "GapClosureRule"):
            lint_gap_closure_rule("rule")  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "GapClosureRule"):
            evaluate_gap_closure_rule(  # type: ignore[arg-type]
                "rule",
                (),
                grounding_revision=0,
            )
        with self.assertRaisesRegex(ValueError, "grounding revision"):
            evaluate_gap_closure_rule(
                GapClosureRule(
                    rule_id="rule-1",
                    version=1,
                    requirement_id="requirement-1",
                    gap_id="gap-1",
                    clauses=(_clause("one"),),
                    closure_condition=ClauseRef("one"),
                ),
                (),
                grounding_revision=-1,
            )

    def test_task_contract_pass_and_fail_are_clause_level_and_round_trip(self) -> None:
        clause = _clause("green", expected="passed")
        contract = _contract((clause,), ClauseRef("green"))

        passed = evaluate_evidence_contract(
            contract,
            (_observation(clause, "passed"),),
        )
        failed = evaluate_evidence_contract(
            contract,
            (_observation(clause, "failed"),),
        )

        self.assertEqual(passed.lifecycle, EvaluationLifecycle.COMPLETED)
        self.assertEqual(passed.logical_result, LogicalResult.PASS)
        self.assertEqual(passed.scope, "task")
        self.assertEqual(passed.predicates[0].reason_code, "predicate_satisfied")
        self.assertEqual(failed.logical_result, LogicalResult.FAIL)
        self.assertEqual(
            ContractEvaluation.from_dict(passed.to_dict()),
            passed,
        )

    def test_missing_ambiguous_and_unavailable_observations_are_indeterminate(self) -> None:
        clause = _clause("green")
        contract = _contract((clause,), ClauseRef("green"))

        missing = evaluate_evidence_contract(contract, ())
        ambiguous = evaluate_evidence_contract(
            contract,
            (
                _observation(clause, observation_id="observation-1"),
                _observation(clause, observation_id="observation-2"),
            ),
        )
        unavailable = evaluate_evidence_contract(
            contract,
            (_observation(clause, status=ObservationStatus.UNAVAILABLE),),
        )
        errored_observation = evaluate_evidence_contract(
            contract,
            (_observation(clause, status=ObservationStatus.ERROR),),
        )

        for evaluation in (missing, ambiguous, unavailable, errored_observation):
            self.assertEqual(evaluation.lifecycle, EvaluationLifecycle.COMPLETED)
            self.assertEqual(evaluation.logical_result, LogicalResult.INDETERMINATE)
        self.assertEqual(missing.predicates[0].reason_code, "missing_observation")
        self.assertEqual(ambiguous.predicates[0].reason_code, "ambiguous_observation")
        self.assertEqual(
            unavailable.predicates[0].reason_code,
            "observation_unavailable",
        )
        self.assertEqual(
            errored_observation.predicates[0].reason_code,
            "observation_error",
        )

    def test_strong_kleene_all_of_and_any_of(self) -> None:
        passing = _clause("passing")
        failing = _clause("failing")
        unknown = _clause("unknown")
        observations = (
            _observation(passing, True),
            _observation(failing, False),
        )

        cases = (
            (
                AllOf((ClauseRef("passing"), ClauseRef("unknown"))),
                (passing, unknown),
                LogicalResult.INDETERMINATE,
            ),
            (
                AllOf((ClauseRef("failing"), ClauseRef("unknown"))),
                (failing, unknown),
                LogicalResult.FAIL,
            ),
            (
                AnyOf((ClauseRef("failing"), ClauseRef("unknown"))),
                (failing, unknown),
                LogicalResult.INDETERMINATE,
            ),
            (
                AnyOf((ClauseRef("passing"), ClauseRef("unknown"))),
                (passing, unknown),
                LogicalResult.PASS,
            ),
        )
        for expression, clauses, expected in cases:
            with self.subTest(expression=expression.to_dict()):
                evaluation = evaluate_evidence_contract(
                    _contract(clauses, expression),
                    tuple(
                        item
                        for item in observations
                        if item.selector in {clause.selector for clause in clauses}
                    ),
                )
                self.assertEqual(evaluation.logical_result, expected)

    def test_at_least_distinguishes_failure_from_indeterminate(self) -> None:
        first = _clause("first")
        second = _clause("second")
        third = _clause("third")
        expression = AtLeast(
            2,
            (ClauseRef("first"), ClauseRef("second"), ClauseRef("third")),
        )
        contract = _contract((first, second, third), expression)

        indeterminate = evaluate_evidence_contract(
            contract,
            (_observation(first), _observation(second, False)),
        )
        passed = evaluate_evidence_contract(
            contract,
            (_observation(first), _observation(second), _observation(third, False)),
        )
        failed = evaluate_evidence_contract(
            contract,
            (_observation(first), _observation(second, False), _observation(third, False)),
        )

        self.assertEqual(indeterminate.logical_result, LogicalResult.INDETERMINATE)
        self.assertEqual(passed.logical_result, LogicalResult.PASS)
        self.assertEqual(failed.logical_result, LogicalResult.FAIL)

    def test_not_never_turns_missing_evidence_into_pass(self) -> None:
        clause = _clause("forbidden")
        contract = _contract((clause,), Not(ClauseRef("forbidden")))

        self.assertEqual(
            evaluate_evidence_contract(contract, ()).logical_result,
            LogicalResult.INDETERMINATE,
        )
        self.assertEqual(
            evaluate_evidence_contract(
                contract,
                (_observation(clause, True),),
            ).logical_result,
            LogicalResult.FAIL,
        )
        self.assertEqual(
            evaluate_evidence_contract(
                contract,
                (_observation(clause, False),),
            ).logical_result,
            LogicalResult.PASS,
        )

    def test_typed_predicate_operators(self) -> None:
        cases = (
            (PredicateOperator.EQUALS, True, True, LogicalResult.PASS),
            (PredicateOperator.EQUALS, 1, True, LogicalResult.FAIL),
            (PredicateOperator.NOT_EQUALS, "old", "new", LogicalResult.PASS),
            (PredicateOperator.LESS_THAN, 4, 5, LogicalResult.PASS),
            (PredicateOperator.LESS_THAN_OR_EQUAL, 5, 5, LogicalResult.PASS),
            (PredicateOperator.GREATER_THAN, 6, 5, LogicalResult.PASS),
            (PredicateOperator.GREATER_THAN_OR_EQUAL, 5, 5, LogicalResult.PASS),
            (PredicateOperator.CONTAINS, ("unit", "integration"), "unit", LogicalResult.PASS),
            (PredicateOperator.SUBSET, ("a",), ("a", "b"), LogicalResult.PASS),
            (PredicateOperator.DISJOINT, ("src/a.py",), ("tests/",), LogicalResult.PASS),
        )
        for index, (operator, actual, expected, result) in enumerate(cases):
            with self.subTest(operator=operator):
                clause = _clause(
                    f"operator-{index}",
                    operator=operator,
                    expected=expected,
                )
                evaluation = evaluate_evidence_contract(
                    _contract((clause,), ClauseRef(clause.clause_id)),
                    (_observation(clause, actual),),
                )
                self.assertEqual(evaluation.logical_result, result)

    def test_predicate_type_mismatch_is_not_a_semantic_failure(self) -> None:
        clause = _clause(
            "numeric",
            operator=PredicateOperator.GREATER_THAN,
            expected=3,
        )
        evaluation = evaluate_evidence_contract(
            _contract((clause,), ClauseRef("numeric")),
            (_observation(clause, "four"),),
        )
        self.assertEqual(evaluation.logical_result, LogicalResult.INDETERMINATE)
        self.assertEqual(
            evaluation.predicates[0].reason_code,
            "observation_type_mismatch",
        )

    def test_negative_predicates_do_not_pass_on_type_mismatch(self) -> None:
        cases = (
            (PredicateOperator.NOT_EQUALS, "not-an-integer", 0),
            (PredicateOperator.DISJOINT, ("not-an-integer",), (0,)),
        )
        for index, (operator, actual, expected) in enumerate(cases):
            with self.subTest(operator=operator):
                clause = _clause(
                    f"negative-type-{index}",
                    operator=operator,
                    expected=expected,
                )
                evaluation = evaluate_evidence_contract(
                    _contract((clause,), ClauseRef(clause.clause_id)),
                    (_observation(clause, actual),),
                )
                self.assertEqual(
                    evaluation.logical_result,
                    LogicalResult.INDETERMINATE,
                )
                self.assertEqual(
                    evaluation.predicates[0].reason_code,
                    "observation_type_mismatch",
                )

    def test_collection_operator_type_mismatch_is_indeterminate(self) -> None:
        contains = _clause(
            "contains",
            operator=PredicateOperator.CONTAINS,
            expected="unit",
        )
        subset = _clause(
            "subset",
            operator=PredicateOperator.SUBSET,
            expected=("unit",),
        )
        for clause, actual in ((contains, "unit"), (subset, "unit")):
            with self.subTest(operator=clause.operator):
                result = evaluate_evidence_contract(
                    _contract((clause,), ClauseRef(clause.clause_id)),
                    (_observation(clause, actual),),
                )
                self.assertEqual(result.logical_result, LogicalResult.INDETERMINATE)


    def test_observation_order_is_digest_and_evaluation_independent(self) -> None:
        first = _clause("first")
        second = _clause("second")
        contract = _contract(
            (first, second),
            AllOf((ClauseRef("first"), ClauseRef("second"))),
        )
        observations = (_observation(first), _observation(second))

        left = evaluate_evidence_contract(contract, observations)
        right = evaluate_evidence_contract(contract, tuple(reversed(observations)))

        self.assertEqual(observation_set_digest(observations), observation_set_digest(tuple(reversed(observations))))
        self.assertEqual(left, right)
        self.assertEqual(left.evaluation_digest, right.evaluation_digest)


class EvidenceContractLintAndLimitTests(unittest.TestCase):
    def test_limits_and_observation_inputs_are_strict(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be positive"):
            EvidenceContractLimits(max_clauses=0)
        with self.assertRaisesRegex(TypeError, "immutable tuple"):
            observation_set_digest(None)  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "EvidenceObservation"):
            observation_set_digest(("not-an-observation",))  # type: ignore[arg-type]

    def test_public_evaluators_reject_stateful_tuple_subclasses(self) -> None:
        clause = _clause("stateful-input")
        contract = _contract((clause,), ClauseRef(clause.clause_id))
        rule = GapClosureRule(
            rule_id="stateful-rule",
            version=1,
            requirement_id="requirement-1",
            gap_id="gap-1",
            clauses=(clause,),
            closure_condition=ClauseRef(clause.clause_id),
        )
        observations = _StatefulObservationTuple((_observation(clause),))

        for operation in (
            lambda: observation_set_digest(observations),
            lambda: evaluate_evidence_contract(contract, observations),
            lambda: evaluate_gap_closure_rule(
                rule,
                observations,
                grounding_revision=1,
            ),
        ):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(TypeError, "immutable tuple"):
                    operation()

    def test_contract_models_reject_adversarial_scalar_subclasses(self) -> None:
        clause = _clause("exact-scalars")
        reference = ClauseRef(clause.clause_id)
        cases = (
            lambda: EvidenceClause(
                clause_id="bad-expected",
                selector=clause.selector,
                operator=PredicateOperator.GREATER_THAN,
                expected=_AdversarialInt(999),
            ),
            lambda: EvidenceClause(
                clause_id="bad-sequence",
                selector=clause.selector,
                operator=PredicateOperator.SUBSET,
                expected=_AdversarialList(("unit",)),
            ),
            lambda: EvidenceObservation(
                observation_id="bad-value",
                selector=clause.selector,
                subject_digest=_SUBJECT,
                source_run_id="verification-run-1",
                artifact_digest=_ARTIFACT,
                status=ObservationStatus.OBSERVED,
                value=_AdversarialInt(1),
                reason_code=None,
            ),
            lambda: AtLeast(_AdversarialInt(2), (reference, reference)),
            lambda: EvidenceSelector(
                observation_type="test_status",
                stage="candidate",
                check_id=_AdversarialString("different-check"),
                producer_authority="verifier",
            ),
        )
        for operation in cases:
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    operation()

    def test_public_evaluators_reject_observation_subclasses(self) -> None:
        clause = _clause("exact-observation")
        observation = _observation(clause)
        subclass = _EvidenceObservationSubclass(
            observation_id=observation.observation_id,
            selector=observation.selector,
            subject_digest=observation.subject_digest,
            source_run_id=observation.source_run_id,
            artifact_digest=observation.artifact_digest,
            status=observation.status,
            value=observation.value,
            reason_code=observation.reason_code,
        )

        with self.assertRaisesRegex(TypeError, "EvidenceObservation"):
            evaluate_evidence_contract(
                _contract((clause,), ClauseRef(clause.clause_id)),
                (subclass,),
            )

    def test_nested_selectors_require_the_exact_wire_type(self) -> None:
        selector = _selector("exact-selector")
        subclass = _EvidenceSelectorSubclass(
            observation_type=selector.observation_type,
            stage=selector.stage,
            check_id=selector.check_id,
            producer_authority=selector.producer_authority,
        )
        with self.assertRaisesRegex(ValueError, "selector"):
            EvidenceClause(
                clause_id="selector-clause",
                selector=subclass,
                operator=PredicateOperator.EQUALS,
                expected=True,
            )
        with self.assertRaisesRegex(ValueError, "selector"):
            EvidenceObservation(
                observation_id="selector-observation",
                selector=subclass,
                subject_digest=_SUBJECT,
                source_run_id="verification-run-1",
                artifact_digest=_ARTIFACT,
                status=ObservationStatus.OBSERVED,
                value=True,
                reason_code=None,
            )

    def test_contract_authority_rejects_clause_and_expression_subclasses(self) -> None:
        clause = _clause("exact-model")
        reference = ClauseRef(clause.clause_id)
        expression_subclasses = (
            _ClauseRefSubclass(clause.clause_id),
            _AllOfSubclass((reference,)),
            _AnyOfSubclass((reference,)),
            _AtLeastSubclass(1, (reference,)),
            _NotSubclass(reference),
        )
        for expression in expression_subclasses:
            with self.subTest(expression=type(expression).__name__):
                with self.assertRaisesRegex(ValueError, "expression is invalid"):
                    _contract((clause,), expression)

        subclass_clause = _EvidenceClauseSubclass(
            clause_id=clause.clause_id,
            selector=clause.selector,
            operator=clause.operator,
            expected=clause.expected,
        )
        with self.assertRaisesRegex(ValueError, "exact EvidenceClause"):
            _contract((subclass_clause,), reference)

        with self.assertRaisesRegex(ValueError, "valid children"):
            AllOf((_ClauseRefSubclass(clause.clause_id),))

    def test_public_contract_authority_rejects_contract_subclasses(self) -> None:
        clause = _clause("exact-contract")
        contract = _contract((clause,), ClauseRef(clause.clause_id))
        subclass = _EvidenceContractSubclass.from_dict(contract.to_dict())

        for operation in (
            lambda: lint_evidence_contract(subclass),
            lambda: evaluate_evidence_contract(
                subclass,
                (_observation(clause),),
            ),
        ):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(TypeError, "exact EvidenceContract"):
                    operation()

    def test_lint_reports_unknown_unused_and_duplicate_references_deterministically(self) -> None:
        first = _clause("first")
        unused = _clause("unused")
        contract = _contract(
            (first, unused),
            AllOf(
                (
                    ClauseRef("first"),
                    ClauseRef("first"),
                    ClauseRef("missing"),
                )
            ),
        )

        issues = lint_evidence_contract(contract)
        self.assertEqual(issues, tuple(sorted(issues)))
        self.assertEqual(
            {issue.code for issue in issues},
            {
                "duplicate_clause_reference",
                "unknown_clause_reference",
                "unused_clause",
            },
        )
        evaluation = evaluate_evidence_contract(contract, ())
        self.assertEqual(evaluation.lifecycle, EvaluationLifecycle.ERROR)
        self.assertIsNone(evaluation.logical_result)
        self.assertEqual(evaluation.error_code, "contract_lint_failed")

    def test_invalid_expected_type_is_a_lint_error(self) -> None:
        clause = _clause(
            "bad-number",
            operator=PredicateOperator.LESS_THAN,
            expected="five",
        )
        contract = _contract((clause,), ClauseRef("bad-number"))

        self.assertEqual(lint_evidence_contract(contract)[0].code, "invalid_expected_type")
        self.assertEqual(
            evaluate_evidence_contract(contract, ()).lifecycle,
            EvaluationLifecycle.ERROR,
        )

        contains = _clause(
            "bad-contains",
            operator=PredicateOperator.CONTAINS,
            expected=("a",),
        )
        subset = _clause(
            "bad-subset",
            operator=PredicateOperator.SUBSET,
            expected="a",
        )
        for clause in (contains, subset):
            with self.subTest(operator=clause.operator):
                issues = lint_evidence_contract(
                    _contract((clause,), ClauseRef(clause.clause_id))
                )
                self.assertEqual(issues[0].code, "invalid_expected_type")

    def test_configurable_contract_complexity_limits_fail_closed(self) -> None:
        first = _clause("first")
        second = _clause("second")
        contract = _contract(
            (first, second),
            AllOf((ClauseRef("first"), Not(ClauseRef("second")))),
        )
        limits = EvidenceContractLimits(
            max_clauses=1,
            max_expression_nodes=2,
            max_expression_depth=2,
            max_contract_bytes=100,
            max_observations=1,
        )

        codes = {issue.code for issue in lint_evidence_contract(contract, limits=limits)}
        self.assertEqual(
            codes,
            {
                "too_many_clauses",
                "expression_too_large",
                "expression_too_deep",
                "contract_too_large",
            },
        )
        self.assertEqual(
            evaluate_evidence_contract(contract, (), limits=limits).lifecycle,
            EvaluationLifecycle.ERROR,
        )

    def test_observation_count_and_duplicate_ids_are_evaluation_errors(self) -> None:
        first = _clause("first")
        second = _clause("second")
        contract = _contract(
            (first, second),
            AllOf((ClauseRef("first"), ClauseRef("second"))),
        )
        same_id = (
            _observation(first, observation_id="same"),
            _observation(second, observation_id="same"),
        )
        duplicate = evaluate_evidence_contract(contract, same_id)
        limited = evaluate_evidence_contract(
            contract,
            (
                _observation(first, observation_id="one"),
                _observation(second, observation_id="two"),
            ),
            limits=EvidenceContractLimits(max_observations=1),
        )
        rejected_without_inspection = evaluate_evidence_contract(
            contract,
            (object(), object()),  # type: ignore[arg-type]
            limits=EvidenceContractLimits(max_observations=1),
        )

        self.assertEqual(duplicate.lifecycle, EvaluationLifecycle.ERROR)
        self.assertEqual(duplicate.error_code, "duplicate_observation_id")
        self.assertEqual(limited.lifecycle, EvaluationLifecycle.ERROR)
        self.assertEqual(limited.error_code, "too_many_observations")
        self.assertEqual(
            rejected_without_inspection.error_code,
            "too_many_observations",
        )
        with self.assertRaisesRegex(ValueError, "configured observation limit"):
            observation_set_digest(
                same_id,
                limits=EvidenceContractLimits(max_observations=1),
            )
        with self.assertRaisesRegex(TypeError, "immutable tuple"):
            evaluate_evidence_contract(contract, iter(()))  # type: ignore[arg-type]

    def test_parser_enforces_a_hard_expression_depth_limit(self) -> None:
        expression: dict[str, object] = {"kind": "clause_ref", "clause_id": "a"}
        for _ in range(64):
            expression = {"kind": "not", "child": expression}
        with self.assertRaisesRegex(ValueError, "hard depth limit"):
            expression_from_dict(expression)


class GapClosureRuleTests(unittest.TestCase):
    def test_gap_closure_rule_is_a_separate_authority_and_round_trips(self) -> None:
        clause = _clause("requirement-satisfied", expected="satisfied")
        contract = _contract((clause,), ClauseRef(clause.clause_id))
        rule = GapClosureRule(
            rule_id="closure-rule-1",
            version=1,
            requirement_id="requirement-1",
            gap_id="gap-1",
            clauses=(clause,),
            closure_condition=ClauseRef(clause.clause_id),
        )

        self.assertFalse(hasattr(contract, "closure_condition"))
        self.assertEqual(GapClosureRule.from_dict(rule.to_dict()), rule)
        self.assertEqual(lint_gap_closure_rule(rule), ())

        closed = evaluate_gap_closure_rule(
            rule,
            (_observation(clause, "satisfied"),),
            grounding_revision=7,
        )
        open_result = evaluate_gap_closure_rule(
            rule,
            (_observation(clause, "unsatisfied"),),
            grounding_revision=7,
        )
        unknown = evaluate_gap_closure_rule(
            rule,
            (),
            grounding_revision=7,
        )

        self.assertEqual(closed.closure_result, GapClosureResult.CLOSED)
        self.assertEqual(open_result.closure_result, GapClosureResult.NOT_CLOSED)
        self.assertEqual(unknown.closure_result, GapClosureResult.INDETERMINATE)
        self.assertEqual(closed.grounding_revision, 7)
        self.assertEqual(
            GapClosureEvaluation.from_dict(closed.to_dict()),
            closed,
        )

        tampered_rule = rule.to_dict()
        tampered_rule["version"] = 2
        with self.assertRaisesRegex(ValueError, "digest does not match"):
            GapClosureRule.from_dict(tampered_rule)
        tampered_evaluation = closed.to_dict()
        tampered_evaluation["grounding_revision"] = 8
        with self.assertRaisesRegex(ValueError, "digest does not match"):
            GapClosureEvaluation.from_dict(tampered_evaluation)

    def test_task_evaluation_cannot_be_parsed_as_gap_closure_evaluation(self) -> None:
        clause = _clause("green")
        task_evaluation = evaluate_evidence_contract(
            _contract((clause,), ClauseRef("green")),
            (_observation(clause),),
        )
        with self.assertRaisesRegex(ValueError, "unknown fields|missing fields"):
            GapClosureEvaluation.from_dict(task_evaluation.to_dict())

    def test_invalid_closure_rule_returns_evaluation_error_not_closed(self) -> None:
        clause = _clause("unused")
        rule = GapClosureRule(
            rule_id="closure-rule-invalid",
            version=1,
            requirement_id="requirement-1",
            gap_id="gap-1",
            clauses=(clause,),
            closure_condition=ClauseRef("missing"),
        )
        evaluation = evaluate_gap_closure_rule(
            rule,
            (),
            grounding_revision=0,
        )
        self.assertEqual(evaluation.lifecycle, EvaluationLifecycle.ERROR)
        self.assertIsNone(evaluation.closure_result)
        self.assertEqual(evaluation.error_code, "closure_rule_lint_failed")
        self.assertEqual(
            GapClosureEvaluation.from_dict(evaluation.to_dict()),
            evaluation,
        )

    def test_gap_closure_duplicate_observations_are_lifecycle_error(self) -> None:
        clause = _clause("closed")
        rule = GapClosureRule(
            rule_id="closure-rule-duplicate-observation",
            version=1,
            requirement_id="requirement-1",
            gap_id="gap-1",
            clauses=(clause,),
            closure_condition=ClauseRef("closed"),
        )
        evaluation = evaluate_gap_closure_rule(
            rule,
            (
                _observation(clause, observation_id="same"),
                _observation(clause, observation_id="same"),
            ),
            grounding_revision=1,
        )
        self.assertEqual(evaluation.lifecycle, EvaluationLifecycle.ERROR)
        self.assertIsNone(evaluation.closure_result)
        self.assertEqual(evaluation.error_code, "duplicate_observation_id")

    def test_gap_closure_evaluation_lifecycle_is_strict(self) -> None:
        rule_digest = "sha256:" + "3" * 64
        observation_digest = observation_set_digest(())
        base = {
            "rule_digest": rule_digest,
            "requirement_id": "requirement-1",
            "gap_id": "gap-1",
            "grounding_revision": 0,
            "observation_set_digest": observation_digest,
            "evaluator_version": "evaluator-v1",
            "evaluator_digest": _ARTIFACT,
            "lifecycle": EvaluationLifecycle.COMPLETED,
            "closure_result": GapClosureResult.CLOSED,
            "predicates": (),
            "error_code": None,
        }
        invalid = (
            ({**base, "closure_result": None}, "requires a result"),
            ({**base, "error_code": "error"}, "cannot have an error"),
            (
                {
                    **base,
                    "lifecycle": EvaluationLifecycle.ERROR,
                    "closure_result": GapClosureResult.CLOSED,
                    "error_code": "error",
                },
                "cannot have a result",
            ),
            (
                {
                    **base,
                    "lifecycle": EvaluationLifecycle.ERROR,
                    "closure_result": None,
                    "error_code": None,
                },
                "requires an error code",
            ),
        )
        for arguments, message in invalid:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                GapClosureEvaluation(**arguments)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
