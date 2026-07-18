import os
import sys

sys.path.insert(0, os.getcwd())

from math_utils import add


assert add(2, 3) == 5
assert add(-4, 7) == 3
assert add(0, 0) == 0
