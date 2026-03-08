from __future__ import annotations

from ..core.database import engine
from ..core.internal_auth import internal_auth_dependency
from ..sandboxes.orchestrator import SandboxOrchestrator
from ..sandboxes.storage import SandboxRepository


repository = SandboxRepository(engine)
orchestrator = SandboxOrchestrator(repository=repository)
require_internal_auth = internal_auth_dependency()
