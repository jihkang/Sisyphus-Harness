import os
import sys

sys.path.insert(0, os.getcwd())

from slug import slugify


assert slugify("  MIXED   Case---Words  ") == "mixed-case-words"
