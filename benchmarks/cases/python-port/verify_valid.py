import os
import sys

sys.path.insert(0, os.getcwd())

from ports import parse_port


assert parse_port(1) == 1
assert parse_port(65535) == 65535
assert parse_port("8080") == 8080
