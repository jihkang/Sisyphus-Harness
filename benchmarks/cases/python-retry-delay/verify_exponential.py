import os
import sys

sys.path.insert(0, os.getcwd())

from backoff import retry_delay


assert retry_delay(1, 3) == 3
assert retry_delay(2, 3) == 6
assert retry_delay(4, 2) == 16
assert retry_delay(5, 1) == 16
