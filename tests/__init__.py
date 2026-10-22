# Tests never touch the real user data folder: everything goes to a throw-away directory.
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="verbascribe-tests-")
os.environ["VERBASCRIBE_DATA_DIR"] = os.path.join(_TMP, "data")  # even when the shell points to real data
os.environ["VERBASCRIBE_MODELS_DIR"] = os.path.join(_TMP, "models")
os.environ["VERBASCRIBE_SKIP_MIGRATION"] = "1"

# PyTorch (CUDA wheels, gigabytes) is not installed for the tests: a small stand-in reports "no GPU"
try:
    import torch  # noqa: F401
except ImportError:
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "stubs"))
