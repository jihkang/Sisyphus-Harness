import os
import sys

sys.path.insert(0, os.getcwd())

from labels import normalize_label


assert normalize_label("Hello, world!") == "hello_world"
assert normalize_label("build@2026") == "build2026"
