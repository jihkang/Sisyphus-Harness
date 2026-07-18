import math
import os
import sys

sys.path.insert(0, os.getcwd())

from ratio import completion_percent


def rejects(*args):
    try:
        completion_percent(*args)
    except ValueError:
        return
    raise AssertionError(f"expected ValueError for {args!r}")


for invalid in (
    (True, 2),
    (1, False),
    ("1", 2),
    (1, None),
    (math.nan, 2),
    (1, math.inf),
):
    rejects(*invalid)
