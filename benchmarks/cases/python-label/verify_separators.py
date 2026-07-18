import os
import sys

sys.path.insert(0, os.getcwd())

from labels import normalize_label


assert normalize_label("alpha   beta") == "alpha_beta"
assert normalize_label("alpha___beta") == "alpha_beta"
assert normalize_label("alpha--beta") == "alpha_beta"
assert normalize_label("alpha\t-_ beta") == "alpha_beta"
