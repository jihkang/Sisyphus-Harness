import os
import sys

sys.path.insert(0, os.getcwd())

from backoff import retry_delay


assert retry_delay(8, 30) == 60
assert retry_delay(6, 3) == 60
assert retry_delay(3, 20) == 60
