import os
import sys

sys.path.insert(0, os.getcwd())

from cache_keys import normalize_cache_key


assert normalize_cache_key("Build 42") == "build-42"
assert normalize_cache_key("ALPHA9") == "alpha9"
