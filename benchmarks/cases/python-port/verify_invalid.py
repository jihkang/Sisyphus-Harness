import os
import sys

sys.path.insert(0, os.getcwd())

from ports import parse_port


for invalid in (True, False, 0, 65536, " 80", "80 ", "8.0", "", None):
    try:
        parse_port(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError(f"expected ValueError for {invalid!r}")
