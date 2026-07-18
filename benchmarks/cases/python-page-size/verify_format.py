import os
import sys

sys.path.insert(0, os.getcwd())

from pagination import parse_page_size


for value in (" 10", "10 ", "+10", "-10", "1.0", "", "abc", "１２"):
    try:
        parse_page_size(value)
    except ValueError:
        pass
    else:
        raise AssertionError(f"expected ValueError for {value!r}")
