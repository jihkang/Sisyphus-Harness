from __future__ import annotations

import argparse
from pathlib import Path

from ....authority import (
    authority_database_path,
    verifier_asset_bundle_root,
)
from ....config import load_harness_config
from ....contracts.verification_service import VerificationProfile
from ....database import Database
from ....infra.verifier_assets import FilesystemVerifierAssetBundleStore
from ..io import repo_path
from ..result import CliResult


def handle_setup(args: argparse.Namespace, repo_root: Path) -> CliResult:
    if args.command == "init":
        database_path = authority_database_path(repo_root)
        Database(database_path).initialize()
        return CliResult(
            {"database_path": str(database_path), "status": "initialized"}
        )
    if args.command == "verifier-assets-create":
        reference = FilesystemVerifierAssetBundleStore(
            verifier_asset_bundle_root(repo_root)
        ).create(repo_path(repo_root, args.source))
        return CliResult(reference.to_dict())
    if args.command == "verification-profile-create":
        config = load_harness_config(repo_path(repo_root, args.config))
        reference = FilesystemVerifierAssetBundleStore(
            verifier_asset_bundle_root(repo_root)
        ).load(args.asset_bundle_id)
        profile = VerificationProfile(
            profile_id=args.profile_id,
            commands=config.verification.selected_commands,
            asset_bundle=reference,
            schema_version="sisyphus_harness.verification_profile.v2",
        )
        return CliResult(profile.to_dict())
    raise AssertionError(f"unhandled setup command: {args.command}")
