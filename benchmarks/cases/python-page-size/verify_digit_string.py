import os
import sys

sys.path.insert(0, os.getcwd())

from pagination import parse_page_size


assert parse_page_size("1") == 1
assert parse_page_size("050") == 50
assert parse_page_size("500") == 500
