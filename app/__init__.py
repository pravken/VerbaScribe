# VerbaScribe package
__version__ = "0.4.5"

# The GPU build of ONNX Runtime (GigaAM / Parakeet on the video card) goes first on sys.path,
# before anything imports onnxruntime (app/onnx_gpu.py)
from app import onnx_gpu as _onnx_gpu  # noqa: E402

_onnx_gpu.activate()
