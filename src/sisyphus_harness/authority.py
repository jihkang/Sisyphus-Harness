from __future__ import annotations

from pathlib import Path
import subprocess


class AuthorityError(RuntimeError):
    pass


def git_common_dir(repo_root: Path) -> Path:
    root = repo_root.resolve()
    if not root.is_dir():
        raise AuthorityError(f"repository root does not exist: {repo_root}")
    completed = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise AuthorityError(
            completed.stderr.strip()
            or completed.stdout.strip()
            or f"not a Git repository: {root}"
        )
    common = Path(completed.stdout.strip())
    if not common.is_absolute():
        common = root / common
    return common.resolve()


def authority_root(repo_root: Path) -> Path:
    common = git_common_dir(repo_root)
    root = (common / "sisyphus-harness").resolve()
    try:
        root.relative_to(common)
    except ValueError as exc:
        raise AuthorityError("authority path escapes the Git common directory") from exc
    return root


def authority_database_path(repo_root: Path) -> Path:
    return authority_root(repo_root) / "authority.sqlite3"


def knowledge_index_path(repo_root: Path) -> Path:
    """Return the rebuildable GraphRAG index path for this Git authority."""

    return authority_root(repo_root) / "knowledge-index.sqlite3"


def verification_artifact_root(repo_root: Path) -> Path:
    return authority_root(repo_root) / "artifacts" / "verification"


def agent_artifact_root(repo_root: Path) -> Path:
    return authority_root(repo_root) / "artifacts" / "agent"


def evolution_artifact_root(repo_root: Path) -> Path:
    return authority_root(repo_root) / "artifacts" / "evolution"


def workspace_bundle_root(repo_root: Path) -> Path:
    return authority_root(repo_root) / "artifacts" / "workspace-bundles"


def verifier_asset_bundle_root(repo_root: Path) -> Path:
    return authority_root(repo_root) / "artifacts" / "verifier-assets"


def attempt_workspace_root(repo_root: Path) -> Path:
    return authority_root(repo_root) / "attempts"


def policy_root(repo_root: Path) -> Path:
    return authority_root(repo_root) / "policies"
