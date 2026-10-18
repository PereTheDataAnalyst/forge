"""The Evaluation Lab: dataset storage, runs through the gateway, results and export.

The engine is the standalone library `forge_integrations.evaluation` (see integrations/). This
package adapts it to the platform: database storage, the gateway and the HTTP routes. The
extension loader mounts `router` under /api/v1 and calls `register`. See
docs/evaluation-lab.md.
"""

from app.modules.evaluation.routes import router
from app.modules.evaluation.runtime import register

__all__ = ["register", "router"]
