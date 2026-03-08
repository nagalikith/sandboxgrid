import sys

from ..api import app as _impl

sys.modules[__name__] = _impl
