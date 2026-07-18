import os
import sys

sys.path.insert(0, os.getcwd())

from page_window import take_page


assert take_page(("a", "b", "c"), 1, 5) == ["b", "c"]
assert take_page([], 0, 3) == []
assert take_page([1, 2], 2, 1) == []
assert take_page([1, 2], 20, 1) == []
