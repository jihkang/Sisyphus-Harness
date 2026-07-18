import os
import sys

sys.path.insert(0, os.getcwd())

from cache_keys import normalize_cache_key


assert normalize_cache_key("Hello, world!") == "hello-world"
assert normalize_cache_key("build@2026") == "build2026"
assert normalize_cache_key("delta-Δ-value") == "delta-value"
