import os
import sys

sys.path.insert(0, os.getcwd())

from labels import normalize_label


assert normalize_label(" -- Alpha -- ") == "alpha"
assert normalize_label("!!!") == ""
