from __future__ import annotations


class CandidateError(ValueError):
    """Raised when an evolved policy candidate violates its wire contract."""
