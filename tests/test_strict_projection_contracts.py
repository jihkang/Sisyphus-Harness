from __future__ import annotations

from dataclasses import replace
import math
import unittest

from sisyphus_harness.contracts.artifacts import ArtifactRef
from sisyphus_harness.contracts.knowledge import (
    CandidateTaskStatus,
    DependencyInspection,
    DependencyState,
    GraphPathStep,
    KnowledgeEdgeType,
    KnowledgeNode,
    KnowledgeNodeType,
    KnowledgeProvenance,
    KnowledgeSearchHit,
    NextStepCandidate,
    NextStepContext,
    knowledge_graph_score,
    knowledge_score_explanation,
    next_step_candidate_explanation,
    weighted_node_terms,
)
from sisyphus_harness.contracts.verification import (
    CommandResult,
    CommandSpec,
    VerificationReceipt,
    VerificationRequest,
)
from sisyphus_harness.contracts.verification_service import (
    BundleVerificationRequest,
    VerificationProfile,
    VerificationServiceResult,
)
from sisyphus_harness.contracts.workspace import WorkspaceBundleRef


class TupleSubclass(tuple):
    """A tuple-shaped value that must not cross an exact-type trust boundary."""


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _command_spec(name: str = "check") -> CommandSpec:
    return CommandSpec(
        name=name,
        argv=("python", "-m", "unittest"),
        timeout_seconds=5,
        criteria=("tests pass",),
    )


def _command_result(*, passed: bool = True) -> CommandResult:
    return CommandResult(
        name="check",
        argv=("python", "-m", "unittest"),
        criteria=("tests pass",),
        passed=passed,
        timed_out=False,
        exit_code=0 if passed else 1,
        duration_ms=10,
        executable_path="/usr/bin/python",
        executable_sha256=_digest("1"),
        stdout_path="logs/stdout.txt",
        stderr_path="logs/stderr.txt",
        workspace_state_before="state-a",
        workspace_state_after="state-a",
        workspace_unchanged=True,
        failure_category=None if passed else "assertion_failure",
        error=None if passed else "test failed",
    )


def _receipt(
    *,
    command: CommandResult | None = None,
    schema_version: str = "sisyphus_harness.verification.v2",
    request_digest: str = _digest("2"),
) -> VerificationReceipt:
    result = command or _command_result()
    return VerificationReceipt(
        run_id="strict-run",
        workspace="/workspace",
        worktree_commit_sha="a" * 40,
        started_at="2026-07-21T00:00:00Z",
        finished_at="2026-07-21T00:00:01Z",
        passed=result.passed,
        commands=(result,),
        workspace_state_before="state-a",
        workspace_state_after="state-a",
        workspace_unchanged=True,
        request_digest=request_digest,
        schema_version=schema_version,
    )


def _bundle() -> WorkspaceBundleRef:
    archive_digest = _digest("3")
    return WorkspaceBundleRef(
        bundle_id=f"workspace:{archive_digest}",
        archive_sha256=archive_digest,
        size_bytes=1,
        source_commit_sha="b" * 40,
        source_state_hash=_digest("4"),
        tree_hash=_digest("5"),
        changed_paths=(),
        entry_count=0,
    )


def _profile() -> VerificationProfile:
    return VerificationProfile(profile_id="strict-profile", commands=(_command_spec(),))


def _bundle_request() -> BundleVerificationRequest:
    return BundleVerificationRequest(
        run_id="strict-run",
        workspace_bundle=_bundle(),
        profile=_profile(),
    )


def _service_result() -> VerificationServiceResult:
    request = _bundle_request()
    receipt = _receipt(request_digest=request.request_digest)
    return VerificationServiceResult(
        request_digest=request.request_digest,
        workspace_bundle_id=request.workspace_bundle.bundle_id,
        profile_digest=request.profile.profile_digest,
        receipt=receipt,
        receipt_artifact=ArtifactRef(
            artifact_id=f"{receipt.run_id}/receipt.json",
            sha256=_digest("6"),
            size_bytes=1,
            media_type="application/json",
        ),
    )


def _provenance() -> KnowledgeProvenance:
    return KnowledgeProvenance(
        source_id="source:strict",
        source_kind="test",
        source_digest=_digest("7"),
        producer="strict-contract-tests",
    )


def _node(
    node_id: str,
    *,
    task_status: CandidateTaskStatus | None = None,
    title: str = "Alpha task",
) -> KnowledgeNode:
    return KnowledgeNode(
        node_id=node_id,
        node_type=(
            KnowledgeNodeType.TASK
            if task_status is not None
            else KnowledgeNodeType.KNOWLEDGE
        ),
        title=title,
        content="alpha implementation evidence",
        provenance=_provenance(),
        task_status=task_status,
        metadata={"topic": "alpha"},
    )


def _step(
    source: str,
    target: str,
    *,
    edge_type: KnowledgeEdgeType = KnowledgeEdgeType.SUPPORTS,
    forward: bool = True,
) -> GraphPathStep:
    return GraphPathStep(
        source_node_id=source,
        target_node_id=target,
        edge_type=edge_type,
        edge_digest=_digest("8"),
        traversed_forward=forward,
    )


def _search_hit(*, reverse: bool = False) -> KnowledgeSearchHit:
    anchor = "anchor"
    task = _node("task-alpha", task_status=CandidateTaskStatus.READY)
    step = (
        _step(task.node_id, anchor, forward=False)
        if reverse
        else _step(anchor, task.node_id)
    )
    matched_terms = ("alpha",)
    lexical_score = weighted_node_terms(task)["alpha"]
    graph_score = knowledge_graph_score(1)
    total_score = lexical_score * 100 + graph_score
    return KnowledgeSearchHit(
        anchor_id=anchor,
        index_revision_digest=_digest("9"),
        node=task,
        depth=1,
        path_node_ids=(anchor, task.node_id),
        path_steps=(step,),
        matched_terms=matched_terms,
        lexical_score=lexical_score,
        graph_score=graph_score,
        total_score=total_score,
        explanation=knowledge_score_explanation(
            depth=1,
            path_steps=(step,),
            matched_terms=matched_terms,
            lexical_score=lexical_score,
            graph_score=graph_score,
            total_score=total_score,
        ),
    )


def _dependency_inspection(task_id: str = "task-alpha") -> DependencyInspection:
    prerequisite = "task-prerequisite"
    step = _step(
        task_id,
        prerequisite,
        edge_type=KnowledgeEdgeType.DEPENDS_ON,
    )
    state = DependencyState(
        node_id=prerequisite,
        task_status=CandidateTaskStatus.COMPLETED,
        depth=1,
        path_node_ids=(task_id, prerequisite),
        path_steps=(step,),
        satisfied=True,
    )
    return DependencyInspection(
        task_id=task_id,
        max_depth=1,
        index_revision_digest=_digest("9"),
        dependencies=(state,),
        all_satisfied=True,
        truncated=False,
        unmet_reasons=(),
    )


def _candidate(
    *,
    task_id: str = "task-alpha",
    rank: int = 1,
) -> NextStepCandidate:
    anchor = "anchor"
    task = _node(task_id, task_status=CandidateTaskStatus.READY)
    step = _step(anchor, task.node_id)
    terms = ("alpha",)
    lexical_score = weighted_node_terms(task)["alpha"]
    graph_score = knowledge_graph_score(1)
    total_score = lexical_score * 100 + graph_score
    inspection = _dependency_inspection(task.node_id)
    return NextStepCandidate(
        rank=rank,
        anchor_id=anchor,
        task=task,
        depth=1,
        path_node_ids=(anchor, task.node_id),
        path_steps=(step,),
        matched_terms=terms,
        lexical_score=lexical_score,
        graph_score=graph_score,
        total_score=total_score,
        dependency_inspection=inspection,
        eligible=True,
        unmet_dependency_reasons=(),
        explanation=next_step_candidate_explanation(
            task=task,
            depth=1,
            path_steps=(step,),
            matched_terms=terms,
            lexical_score=lexical_score,
            graph_score=graph_score,
            total_score=total_score,
            eligible=True,
            unmet_reasons=(),
        ),
    )


class VerificationProjectionContractTests(unittest.TestCase):
    def test_valid_request_receipt_profile_and_service_round_trip(self) -> None:
        request = VerificationRequest(
            run_id="strict-run",
            workspace="/workspace",
            workspace_state_before="state-a",
            commands=(_command_spec(),),
        )
        self.assertEqual(VerificationRequest.from_dict(request.to_dict()), request)

        for schema in (
            "sisyphus_harness.verification.v1",
            "sisyphus_harness.verification.v2",
        ):
            with self.subTest(schema=schema):
                receipt = _receipt(schema_version=schema)
                decoded = VerificationReceipt.from_dict(receipt.to_dict())
                self.assertEqual(decoded.receipt_digest, receipt.receipt_digest)

        profile = _profile()
        self.assertEqual(VerificationProfile.from_dict(profile.to_dict()), profile)
        bundle_request = _bundle_request()
        self.assertEqual(
            BundleVerificationRequest.from_dict(bundle_request.to_dict()),
            bundle_request,
        )
        result = _service_result()
        self.assertEqual(VerificationServiceResult.from_dict(result.to_dict()), result)

    def test_valid_failed_command_and_receipt_remain_consistent(self) -> None:
        command = _command_result(passed=False)
        receipt = _receipt(command=command)
        self.assertFalse(receipt.passed)
        self.assertEqual(
            VerificationReceipt.from_dict(receipt.to_dict()).commands,
            (command,),
        )

    def test_command_spec_rejects_malformed_direct_values(self) -> None:
        valid = _command_spec()
        cases = {
            "non-string name": {"name": 1},
            "blank name": {"name": " "},
            "argv tuple subclass": {"argv": TupleSubclass(valid.argv)},
            "empty argv": {"argv": ()},
            "blank argv": {"argv": ("",)},
            "nul argv": {"argv": ("bad\0arg",)},
            "non-string argv": {"argv": (1,)},
            "boolean timeout": {"timeout_seconds": True},
            "non-numeric timeout": {"timeout_seconds": "5"},
            "infinite timeout": {"timeout_seconds": math.inf},
            "zero timeout": {"timeout_seconds": 0},
            "criteria tuple subclass": {"criteria": TupleSubclass(valid.criteria)},
            "empty criteria": {"criteria": ()},
            "blank criterion": {"criteria": ("",)},
            "non-string criterion": {"criteria": (1,)},
            "duplicate criteria": {"criteria": ("same", "same")},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    replace(valid, **changes)

    def test_command_spec_wire_shape_is_strict(self) -> None:
        payload = _command_spec().to_dict()
        cases = {
            "boolean timeout": {"timeout_seconds": True},
            "tuple argv": {"argv": ("python",)},
            "non-string argv": {"argv": [1]},
            "tuple criteria": {"criteria": ("passes",)},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    CommandSpec.from_dict(payload | changes)

    def test_command_result_rejects_malformed_direct_values(self) -> None:
        valid = _command_result()
        cases = {
            "argv tuple subclass": {"argv": TupleSubclass(valid.argv)},
            "empty argv": {"argv": ()},
            "blank argv": {"argv": ("",)},
            "nul argv": {"argv": ("bad\0arg",)},
            "non-string argv": {"argv": (1,)},
            "criteria tuple subclass": {"criteria": TupleSubclass(valid.criteria)},
            "blank criterion": {"criteria": ("",)},
            "non-string criterion": {"criteria": (1,)},
            "non-boolean passed": {"passed": 1},
            "non-boolean timeout": {"timed_out": 0},
            "boolean exit code": {"exit_code": True},
            "fractional exit code": {"exit_code": 1.5},
            "boolean duration": {"duration_ms": False},
            "fractional duration": {"duration_ms": 1.5},
            "negative duration": {"duration_ms": -1},
            "blank executable": {"executable_path": ""},
            "non-string digest": {"executable_sha256": 1},
            "malformed digest": {"executable_sha256": "sha256:nope"},
            "non-boolean unchanged": {"workspace_unchanged": 1},
            "state claim mismatch": {"workspace_state_after": "state-b"},
            "unsupported failure": {"failure_category": "unknown"},
            "blank error": {"error": ""},
            "passing timeout": {"timed_out": True},
            "passing nonzero": {"exit_code": 2},
            "passing failure category": {"failure_category": "command_failure"},
            "passing error": {"error": "unexpected"},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    replace(valid, **changes)

        with self.assertRaises(ValueError):
            replace(_command_result(passed=False), failure_category=None)

    def test_command_result_rejects_unsafe_artifact_paths(self) -> None:
        valid = _command_result()
        for path in ("", "../stdout", "/stdout", "logs\\stdout", "logs/../stdout"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    replace(valid, stdout_path=path)

    def test_command_result_wire_shape_is_strict(self) -> None:
        payload = _command_result().to_dict()
        cases = {
            "boolean exit": {"exit_code": True},
            "fractional exit": {"exit_code": 1.5},
            "boolean duration": {"duration_ms": True},
            "negative duration": {"duration_ms": -1},
            "bad digest": {"executable_sha256": "bad"},
            "bad failure": {"failure_category": "bad"},
            "non-boolean passed": {"passed": 1},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    CommandResult.from_dict(payload | changes)

    def test_process_leak_failure_category_round_trips(self) -> None:
        result = replace(
            _command_result(passed=False),
            failure_category="process_leak",
        )

        self.assertEqual(
            CommandResult.from_dict(result.to_dict()).failure_category,
            "process_leak",
        )

    def test_request_rejects_mutable_duplicate_and_invalid_commands(self) -> None:
        command = _command_spec()
        valid = VerificationRequest(
            run_id="strict-run",
            workspace="/workspace",
            workspace_state_before="state-a",
            commands=(command,),
        )
        cases = {
            "list": {"commands": [command]},
            "tuple subclass": {"commands": TupleSubclass((command,))},
            "empty": {"commands": ()},
            "foreign item": {"commands": (object(),)},
            "duplicates": {"commands": (command, command)},
            "schema": {"schema_version": "future"},
            "run id": {"run_id": "../unsafe"},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    replace(valid, **changes)

        payload = valid.to_dict()
        with self.assertRaises(ValueError):
            VerificationRequest.from_dict(payload | {"commands": ()})
        with self.assertRaises(ValueError):
            VerificationRequest.from_dict(payload | {"request_digest": _digest("f")})

    def test_receipt_rejects_projection_and_exact_tuple_violations(self) -> None:
        valid = _receipt()
        cases = {
            "blank workspace": {"workspace": ""},
            "non-boolean passed": {"passed": 1},
            "non-boolean unchanged": {"workspace_unchanged": 1},
            "state mismatch": {"workspace_state_after": "state-b"},
            "schema": {"schema_version": "future"},
            "request digest": {"request_digest": "bad"},
            "list commands": {"commands": list(valid.commands)},
            "tuple subclass": {"commands": TupleSubclass(valid.commands)},
            "foreign command": {"commands": (object(),)},
            "pass projection": {"passed": False},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    replace(valid, **changes)

    def test_receipt_wire_projection_and_digest_are_strict(self) -> None:
        receipt = _receipt()
        payload = receipt.to_dict()
        cases = {
            "not object": [],
            "unsupported schema": payload | {"schema_version": "future"},
            "commands not list": payload | {"commands": ()},
            "criteria mismatch": payload | {"criteria": []},
            "receipt digest mismatch": payload | {"receipt_digest": _digest("f")},
        }
        for label, raw in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    VerificationReceipt.from_dict(raw)

    def test_profile_and_bundle_request_reject_substitution(self) -> None:
        command = _command_spec()
        profile = _profile()
        profile_cases = {
            "list": {"commands": [command]},
            "tuple subclass": {"commands": TupleSubclass((command,))},
            "empty": {"commands": ()},
            "foreign": {"commands": (object(),)},
            "duplicate names": {"commands": (command, command)},
            "schema": {"schema_version": "future"},
            "unsafe id": {"profile_id": "."},
        }
        for label, changes in profile_cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    replace(profile, **changes)

        profile_payload = profile.to_dict()
        with self.assertRaises(ValueError):
            VerificationProfile.from_dict(profile_payload | {"commands": ()})
        with self.assertRaises(ValueError):
            VerificationProfile.from_dict(
                profile_payload | {"profile_digest": _digest("f")}
            )

        request = _bundle_request()
        request_cases = {
            "workspace substitution": {"workspace_bundle": object()},
            "profile substitution": {"profile": object()},
            "schema": {"schema_version": "future"},
        }
        for label, changes in request_cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    replace(request, **changes)
        with self.assertRaises(ValueError):
            BundleVerificationRequest.from_dict(
                request.to_dict() | {"request_digest": _digest("f")}
            )

    def test_service_result_rejects_unbound_or_substituted_components(self) -> None:
        valid = _service_result()
        wrong_artifact = replace(
            valid.receipt_artifact,
            artifact_id="other/receipt.json",
        )
        cases = {
            "receipt substitution": {"receipt": object()},
            "artifact substitution": {"receipt_artifact": object()},
            "unbound receipt": {"request_digest": _digest("f")},
            "wrong artifact id": {"receipt_artifact": wrong_artifact},
            "schema": {"schema_version": "future"},
            "bad profile digest": {"profile_digest": "bad"},
            "blank bundle id": {"workspace_bundle_id": ""},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    replace(valid, **changes)


class KnowledgeProjectionContractTests(unittest.TestCase):
    def test_valid_forward_and_reverse_search_paths(self) -> None:
        self.assertTrue(_search_hit().explanation.endswith("derived_candidate_only"))
        reverse = _search_hit(reverse=True)
        self.assertFalse(reverse.path_steps[0].traversed_forward)

    def test_search_hit_rejects_tuple_subclasses_and_invalid_node(self) -> None:
        valid = _search_hit()
        cases = {
            "path ids": {"path_node_ids": TupleSubclass(valid.path_node_ids)},
            "path steps": {"path_steps": TupleSubclass(valid.path_steps)},
            "terms": {"matched_terms": TupleSubclass(valid.matched_terms)},
            "node": {"node": object()},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    replace(valid, **changes)

    def test_search_path_rejects_invalid_shape_and_connectivity(self) -> None:
        valid = _search_hit()
        cases = {
            "depth": {"depth": 0},
            "length": {"path_node_ids": ("anchor",)},
            "non-string node": {"path_node_ids": ("anchor", 1)},
            "repeated node": {"path_node_ids": ("anchor", "anchor")},
            "wrong endpoint": {"path_node_ids": ("anchor", "other")},
            "foreign step": {"path_steps": (object(),)},
            "disconnected step": {"path_steps": (_step("anchor", "other"),)},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    replace(valid, **changes)

    def test_search_score_rejects_untrusted_projection_values(self) -> None:
        valid = _search_hit()
        cases = {
            "absent term": {"matched_terms": ("missing",)},
            "empty terms": {"matched_terms": ()},
            "unnormalized term": {"matched_terms": ("Alpha",)},
            "duplicate terms": {"matched_terms": ("alpha", "alpha")},
            "too many terms": {"matched_terms": tuple(f"t{i}" for i in range(129))},
            "negative lexical": {"lexical_score": -1},
            "boolean graph": {"graph_score": True},
            "wrong graph": {"graph_score": 0},
            "wrong total": {"total_score": 0},
            "wrong explanation": {"explanation": "trusted because LLM said so"},
            "authority escalation": {"authority": "authoritative"},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    replace(valid, **changes)

    def test_dependency_state_rejects_non_dependency_and_status_claims(self) -> None:
        inspection = _dependency_inspection()
        state = inspection.dependencies[0]
        cases = {
            "path ids tuple subclass": {
                "path_node_ids": TupleSubclass(state.path_node_ids)
            },
            "path steps tuple subclass": {
                "path_steps": TupleSubclass(state.path_steps)
            },
            "reverse dependency": {
                "path_steps": (
                    _step(
                        state.node_id,
                        inspection.task_id,
                        edge_type=KnowledgeEdgeType.DEPENDS_ON,
                        forward=False,
                    ),
                )
            },
            "non-dependency edge": {
                "path_steps": (_step(inspection.task_id, state.node_id),)
            },
            "non-boolean satisfied": {"satisfied": 1},
            "inconsistent satisfied": {"satisfied": False},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    replace(state, **changes)

    def test_dependency_inspection_rejects_untrusted_collections_and_claims(self) -> None:
        valid = _dependency_inspection()
        state = valid.dependencies[0]
        later = replace(
            state,
            node_id="task-z",
            path_node_ids=(valid.task_id, "task-z"),
            path_steps=(
                _step(
                    valid.task_id,
                    "task-z",
                    edge_type=KnowledgeEdgeType.DEPENDS_ON,
                ),
            ),
        )
        earlier = replace(
            state,
            node_id="task-a",
            path_node_ids=(valid.task_id, "task-a"),
            path_steps=(
                _step(
                    valid.task_id,
                    "task-a",
                    edge_type=KnowledgeEdgeType.DEPENDS_ON,
                ),
            ),
        )
        cases = {
            "dependencies tuple subclass": {
                "dependencies": TupleSubclass(valid.dependencies)
            },
            "foreign dependency": {"dependencies": (object(),)},
            "wrong task path": {"task_id": "different-task"},
            "depth budget": {"max_depth": 0},
            "unordered": {"dependencies": (later, earlier)},
            "duplicate": {"dependencies": (state, state)},
            "non-boolean satisfied": {"all_satisfied": 1},
            "non-boolean truncated": {"truncated": 0},
            "reasons tuple subclass": {
                "unmet_reasons": TupleSubclass(valid.unmet_reasons)
            },
            "invalid reason": {"unmet_reasons": ("",)},
            "reason projection": {"unmet_reasons": ("invented",)},
            "satisfaction projection": {"all_satisfied": False},
            "authority escalation": {"authority": "authoritative"},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    replace(valid, **changes)

    def test_candidate_rejects_substitution_and_projection_mismatches(self) -> None:
        valid = _candidate()
        other_inspection = _dependency_inspection("task-other")
        cases = {
            "bad rank": {"rank": 0},
            "knowledge not task": {"task": _node("knowledge")},
            "path ids tuple subclass": {
                "path_node_ids": TupleSubclass(valid.path_node_ids)
            },
            "path steps tuple subclass": {
                "path_steps": TupleSubclass(valid.path_steps)
            },
            "terms tuple subclass": {"matched_terms": TupleSubclass(valid.matched_terms)},
            "inspection substitution": {"dependency_inspection": object()},
            "inspection for other task": {"dependency_inspection": other_inspection},
            "non-boolean eligible": {"eligible": 1},
            "reasons tuple subclass": {
                "unmet_dependency_reasons": TupleSubclass(())
            },
            "invented reasons": {"unmet_dependency_reasons": ("invented",)},
            "eligibility projection": {"eligible": False},
            "explanation projection": {"explanation": "LLM-approved"},
            "authority escalation": {"authority": "authoritative"},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    replace(valid, **changes)

    def test_context_rejects_tuple_subclasses_and_context_mismatches(self) -> None:
        candidate = _candidate()
        valid = NextStepContext(
            anchor_id="anchor",
            query_terms=("alpha",),
            candidate_max_depth=1,
            dependency_max_depth=1,
            index_revision_digest=_digest("9"),
            candidates=(candidate,),
        )
        cases = {
            "query tuple subclass": {"query_terms": TupleSubclass(("alpha",))},
            "candidate tuple subclass": {
                "candidates": TupleSubclass((candidate,))
            },
            "foreign candidate": {"candidates": (object(),)},
            "rank gap": {"candidates": (replace(candidate, rank=2),)},
            "wrong anchor": {"anchor_id": "other-anchor"},
            "candidate budget": {"candidate_max_depth": 0},
            "dependency budget": {"dependency_max_depth": 2},
            "revision": {"index_revision_digest": _digest("a")},
            "query terms": {"query_terms": ("beta",)},
            "authority escalation": {"authority": "authoritative"},
            "schema": {"schema_version": "future"},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    replace(valid, **changes)

    def test_context_rejects_duplicate_and_nondeterministic_candidates(self) -> None:
        alpha = _candidate(task_id="task-alpha", rank=1)
        duplicate = replace(alpha, rank=2)
        with self.assertRaises(ValueError):
            NextStepContext(
                anchor_id="anchor",
                query_terms=("alpha",),
                candidate_max_depth=1,
                dependency_max_depth=1,
                index_revision_digest=_digest("9"),
                candidates=(alpha, duplicate),
            )

        zulu = _candidate(task_id="task-zulu", rank=1)
        alpha_second = _candidate(task_id="task-alpha", rank=2)
        with self.assertRaises(ValueError):
            NextStepContext(
                anchor_id="anchor",
                query_terms=("alpha",),
                candidate_max_depth=1,
                dependency_max_depth=1,
                index_revision_digest=_digest("9"),
                candidates=(zulu, alpha_second),
            )

    def test_node_and_graph_helpers_reject_boundary_values(self) -> None:
        node = _node("knowledge")
        with self.assertRaises(ValueError):
            replace(node, metadata=[])
        with self.assertRaises(ValueError):
            KnowledgeNode.from_dict(node.to_dict() | {"title": 1})
        with self.assertRaises(ValueError):
            knowledge_graph_score(0)
        with self.assertRaises(ValueError):
            knowledge_graph_score(True)


if __name__ == "__main__":
    unittest.main()
