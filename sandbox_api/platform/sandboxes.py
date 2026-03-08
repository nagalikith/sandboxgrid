import sys

from .. import sandboxes as _impl

sys.modules[__name__] = _impl
