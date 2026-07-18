import os
import sys

sys.path.insert(0, os.getcwd())

from ratio import completion_percent


assert completion_percent(0, 5) == 0.0
assert completion_percent(5, 5) == 100.0
assert completion_percent(0.5, 2.0) == 25.0
