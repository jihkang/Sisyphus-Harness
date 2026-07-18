import os
import sys

sys.path.insert(0, os.getcwd())

from backoff import retry_delay


for attempt, base_delay in ((0, 1), (9, 1), (1, 0), (1, 31), (-1, 4)):
    try:
        retry_delay(attempt, base_delay)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for an out-of-range input")
