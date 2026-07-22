from __future__ import annotations

import json
import sqlite3
import sys
from typing import Sequence

from .config import ConfigError
from .interfaces.cli.dispatcher import dispatch
from .interfaces.cli.parser import build_parser
from .interfaces.cli.renderers import render_json


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _main(argv)
    except (
        ConfigError,
        json.JSONDecodeError,
        OSError,
        RuntimeError,
        sqlite3.Error,
        ValueError,
    ) as exc:
        render_json(
            {
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
            stream=sys.stderr,
        )
        return 2


def _main(argv: Sequence[str] | None) -> int:
    args = build_parser().parse_args(argv)
    result = dispatch(args)
    render_json(result.payload)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
