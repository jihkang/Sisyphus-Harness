import os
import sys

sys.path.insert(0, os.getcwd())

from cache_keys import normalize_cache_key


assert normalize_cache_key("alpha   beta") == "alpha-beta"
assert normalize_cache_key("alpha___beta") == "alpha-beta"
assert normalize_cache_key("alpha--beta") == "alpha-beta"
assert normalize_cache_key("alpha\t-_ beta") == "alpha-beta"
