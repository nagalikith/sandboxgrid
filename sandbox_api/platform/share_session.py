import sys

from .. import share_session as _impl

sys.modules[__name__] = _impl
