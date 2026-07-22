from __future__ import annotations

import json
from typing import TextIO


def render_json(payload: object, *, stream: TextIO | None = None) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True), file=stream)
