import os
import sys

sys.path.insert(0, os.getcwd())

from pagination import parse_page_size


for value in (True, False, 1.0, None, [], {}):
    try:
        parse_page_size(value)
    except ValueError:
        pass
    else:
        raise AssertionError(f"expected ValueError for {value!r}")
