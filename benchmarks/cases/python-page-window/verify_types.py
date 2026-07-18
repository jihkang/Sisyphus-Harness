import os
import sys

sys.path.insert(0, os.getcwd())

from page_window import take_page


def rejects(*args):
    try:
        take_page(*args)
    except ValueError:
        return
    raise AssertionError(f"expected ValueError for {args!r}")


for invalid in (
    ("abc", 0, 1),
    ({"a": 1}, 0, 1),
    ([1], True, 1),
    ([1], 0, False),
    ([1], 0.0, 1),
    ([1], 0, "1"),
):
    rejects(*invalid)
