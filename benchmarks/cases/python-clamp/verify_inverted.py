import os
import sys

sys.path.insert(0, os.getcwd())

from bounds import clamp


try:
    clamp(4, 10, 0)
except ValueError:
    pass
else:
    raise AssertionError("inverted bounds must raise ValueError")
