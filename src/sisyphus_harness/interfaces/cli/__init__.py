"""Command-line interface implementation."""

from .parser import build_parser
from .result import CliResult

__all__ = ["CliResult", "build_parser"]
