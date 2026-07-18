import os
import sys

sys.path.insert(0, os.getcwd())

from page_window import take_page


def rejects(offset, limit):
    try:
        take_page([1, 2, 3], offset, limit)
    except ValueError:
        return
    raise AssertionError(f"expected ValueError for offset={offset}, limit={limit}")


for invalid in ((-1, 1), (0, 0), (0, -1)):
    rejects(*invalid)
