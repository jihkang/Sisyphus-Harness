import os
import sys

sys.path.insert(0, os.getcwd())

from ratio import completion_percent


assert completion_percent(1, 4) == 25.0
assert completion_percent(2.5, 10) == 25.0
assert completion_percent(3, 8) == 37.5
assert isinstance(completion_percent(1, 4), float)
