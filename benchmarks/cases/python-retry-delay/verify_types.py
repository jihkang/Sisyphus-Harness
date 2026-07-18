import os
import sys

sys.path.insert(0, os.getcwd())

from backoff import retry_delay


for attempt, base_delay in (
    (True, 1),
    (1, False),
    (1.0, 1),
    (1, 1.0),
    ("1", 1),
    (1, "1"),
    (None, 1),
):
    try:
        retry_delay(attempt, base_delay)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unsupported input types")
