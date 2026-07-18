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


for invalid in ((-1, 10), (0, 0), (1, -1), (11, 10)):
    rejects(*invalid)
