import sys

from .. import worker as _impl

sys.modules[__name__] = _impl
