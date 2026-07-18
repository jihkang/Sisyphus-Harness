import os
import sys

sys.path.insert(0, os.getcwd())

from cache_keys import normalize_cache_key


assert normalize_cache_key(" -- Alpha -- ") == "alpha"
assert normalize_cache_key("!!!") == ""
assert normalize_cache_key("") == ""
