import os
import sys

sys.path.insert(0, os.getcwd())

from page_window import take_page


values = [0, 1, 2, 3, 4]
result = take_page(values, 1, 2)
assert result == [1, 2]
assert result is not values
assert values == [0, 1, 2, 3, 4]
assert take_page(values, 0, 10) == values
