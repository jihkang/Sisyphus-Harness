import os
import sys

sys.path.insert(0, os.getcwd())

from labels import normalize_label


assert normalize_label("Release 42") == "release_42"
assert normalize_label("ALPHA9") == "alpha9"
