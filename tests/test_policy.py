from __future__ import annotations

import json
from pathlib import Path
import stat
import tempfile
import unittest

from sisyphus_harness.config import CadencePolicy
from sisyphus_harness.evolution import CandidatePolicy
from sisyphus_harness.policy import PolicyError, PolicyRegistry


def write_result(
    path: Path,
    candidate: CandidatePolicy,
    *,
    accepted: bool = True,
    evolution_id: str = "evolution-1",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "sisyphus_harness.evolution_result.v1",
                "evolution_id": evolution_id,
                "accepted": accepted,
                "status": "proposed" if accepted else "rejected",
                "candidate": candidate.to_dict(),
            }
        ),
        encoding="utf-8",
    )


class PolicyRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        root = Path(self.temporary_directory.name)
        self.registry = PolicyRegistry(root / "policies")
        self.result_path = root / "evolution" / "result.json"
        self.candidate = CandidatePolicy(
            strategy_prompt="Inspect hashes before editing.",
            cadence=CadencePolicy(),
        )
        write_result(self.result_path, self.candidate)

    def test_approval_activation_and_signed_active_load(self) -> None:
        approval = self.registry.approve(self.result_path, note="reviewed")
        active = self.registry.activate(self.result_path, approval)
        loaded = self.registry.load_active()

        self.assertEqual(loaded, self.candidate)
        self.assertEqual(stat.S_IMODE(self.registry.key_path.stat().st_mode), 0o600)
        self.assertTrue(active.is_file())
        payload = json.loads(active.read_text(encoding="utf-8"))
        self.assertTrue(payload["signature"].startswith("hmac-sha256:"))

    def test_tampered_approval_and_active_policy_are_rejected(self) -> None:
        approval = self.registry.approve(self.result_path)
        payload = json.loads(approval.read_text(encoding="utf-8"))
        payload["note"] = "tampered"
        approval.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(PolicyError, "signature is invalid"):
            self.registry.activate(self.result_path, approval)

        clean_approval_root = Path(self.temporary_directory.name) / "other-policies"
        registry = PolicyRegistry(clean_approval_root)
        clean_approval = registry.approve(self.result_path)
        active = registry.activate(self.result_path, clean_approval)
        active_payload = json.loads(active.read_text(encoding="utf-8"))
        active_payload["evolution_id"] = "tampered"
        active.write_text(json.dumps(active_payload), encoding="utf-8")
        with self.assertRaisesRegex(PolicyError, "signature is invalid"):
            registry.load_active()

    def test_rejected_candidate_cannot_be_approved(self) -> None:
        rejected = Path(self.temporary_directory.name) / "rejected.json"
        write_result(rejected, self.candidate, accepted=False)

        with self.assertRaisesRegex(PolicyError, "accepted proposed"):
            self.registry.approve(rejected)

    def test_duplicate_approval_is_rejected(self) -> None:
        self.registry.approve(self.result_path)

        with self.assertRaisesRegex(PolicyError, "already has"):
            self.registry.approve(self.result_path)

    def test_unsafe_evolution_id_cannot_escape_approval_root(self) -> None:
        unsafe = Path(self.temporary_directory.name) / "unsafe-result.json"
        write_result(unsafe, self.candidate, evolution_id="../../escaped")

        with self.assertRaisesRegex(RuntimeError, "unsafe"):
            self.registry.approve(unsafe)
        self.assertFalse(
            (Path(self.temporary_directory.name) / "escaped").exists()
        )

    def test_candidate_tampering_after_approval_is_detected(self) -> None:
        approval = self.registry.approve(self.result_path)
        payload = json.loads(self.result_path.read_text(encoding="utf-8"))
        payload["candidate"]["strategy_prompt"] = "tampered"
        self.result_path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "hash does not match"):
            self.registry.activate(self.result_path, approval)

    def test_missing_active_policy_returns_none(self) -> None:
        self.assertIsNone(self.registry.load_active())


if __name__ == "__main__":
    unittest.main()
