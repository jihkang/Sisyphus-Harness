import os
import sys

sys.path.insert(0, os.getcwd())

from cache_keys import normalize_cache_key


for value in (None, True, 42, b"alpha", [], {}):
    try:
        normalize_cache_key(value)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for non-string input")
