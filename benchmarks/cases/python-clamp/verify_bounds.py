import os
import sys

sys.path.insert(0, os.getcwd())

from bounds import clamp


assert clamp(-1, 0, 10) == 0
assert clamp(4, 0, 10) == 4
assert clamp(20, 0, 10) == 10
