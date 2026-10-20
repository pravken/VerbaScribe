# Minimal stand-in for tests in an environment without PyTorch wheels
class _Cuda:
    @staticmethod
    def is_available(): return False
    @staticmethod
    def empty_cache(): pass
    @staticmethod
    def ipc_collect(): pass
cuda = _Cuda()
def no_grad():
    import contextlib
    return contextlib.nullcontext()
class Tensor: pass
class dtype: pass
class device:
    def __init__(self, *a, **k): pass
def __getattr__(name):
    raise AttributeError(name)
