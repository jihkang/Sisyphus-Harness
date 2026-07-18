from __future__ import annotations

from pathlib import Path
import subprocess


def run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            completed.stderr.strip()
            or completed.stdout.strip()
            or f"git command failed: {' '.join(args)}"
        )
    return completed.stdout.strip()


def create_git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    run_git(path, "init", "-q")
    run_git(path, "config", "user.name", "Sisyphus Harness Tests")
    run_git(path, "config", "user.email", "tests@example.invalid")
    (path / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    run_git(path, "add", "tracked.txt")
    run_git(path, "commit", "-q", "-m", "initial")
    return path
