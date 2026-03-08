import sys

from .. import dashboards as _impl

sys.modules[__name__] = _impl
