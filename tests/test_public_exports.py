from __future__ import annotations

import unittest

import sisyphus_harness.adapters as adapters
import sisyphus_harness.contracts as contracts
import sisyphus_harness.infra as infra
import sisyphus_harness.ports as ports
import sisyphus_harness.services as services


class PublicExportTests(unittest.TestCase):
    def test_package_exports_are_unique_and_resolve(self) -> None:
        for module in (contracts, ports, adapters, infra, services):
            with self.subTest(module=module.__name__):
                exported = module.__all__
                self.assertEqual(len(exported), len(set(exported)))
                for name in exported:
                    self.assertTrue(
                        hasattr(module, name),
                        f"{module.__name__}.{name} is listed but unavailable",
                    )

    def test_evidence_and_graph_foundation_is_public(self) -> None:
        expected_contracts = {
            "ContractEvaluation",
            "EvidenceClause",
            "EvidenceContract",
            "EvidenceObservation",
            "GapClosureEvaluation",
            "GapClosureRule",
            "KnowledgeEdge",
            "KnowledgeNode",
            "KnowledgeSearchHit",
            "NextStepContext",
        }
        expected_ports = {
            "EvidenceAdjudicationRequest",
            "EvidenceContractAdjudicationPort",
            "KnowledgeIndexPort",
            "ReceiptObservationPort",
            "VerificationServicePort",
        }
        expected_infra = {"SQLiteKnowledgeIndex"}
        expected_adapters = {"ReceiptObservationAdapter"}
        expected_services = {"ControlEvidenceContractService"}

        self.assertTrue(expected_contracts.issubset(contracts.__all__))
        self.assertTrue(expected_ports.issubset(ports.__all__))
        self.assertTrue(expected_infra.issubset(infra.__all__))
        self.assertTrue(expected_adapters.issubset(adapters.__all__))
        self.assertTrue(expected_services.issubset(services.__all__))


if __name__ == "__main__":
    unittest.main()
