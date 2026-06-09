import os
import sys

# Put the project root on sys.path so tests can `import discover.*`.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
