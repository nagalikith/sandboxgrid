import sys

from .. import web as _impl

sys.modules[__name__] = _impl
