from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..contracts.artifacts import ArtifactRef
from ..contracts.verification import VerificationReceipt
from ..contracts.verification_service import (
    BundleVerificationRequest,
    VerificationServiceResult,
    VerifierExecutionIdentity,
)


@runtime_checkable
class VerificationExecutorPort(Protocol):
    """Execute a fully admitted request and expose its authoritative receipt."""

    def execute(
        self,
        request: BundleVerificationRequest,
    ) -> VerificationServiceResult:
        ...

    def read_receipt(self, reference: ArtifactRef) -> VerificationReceipt:
        """Read and validate the authoritative receipt artifact by reference."""

        ...


@runtime_checkable
class VerificationServicePort(VerificationExecutorPort, Protocol):
    """Admit and execute a profile against an immutable workspace bundle.

    The port deliberately exposes no task, requirement, or contract mutation API.
    A verifier reports observations; Control remains responsible for adjudication.
    Unlike the in-container executor, this host-side boundary can resolve the
    immutable runtime identity before Control constructs the request.
    """

    def execution_identity(self) -> VerifierExecutionIdentity:
        """Resolve the immutable runtime identity used for the next request."""

        ...

@runtime_checkable
class TimeoutBoundVerificationServicePort(Protocol):
    """A host-side transport that can be clamped to the Agent's remaining budget."""

    def execute_with_timeout(
        self,
        request: BundleVerificationRequest,
        *,
        timeout_seconds: float,
    ) -> VerificationServiceResult:
        ...
