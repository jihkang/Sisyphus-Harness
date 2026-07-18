import os
import sys

sys.path.insert(0, os.getcwd())

from slug import slugify


assert slugify("Hello, World!") == "hello-world"
assert slugify("Python_3.14") == "python314"
assert slugify("---") == ""
