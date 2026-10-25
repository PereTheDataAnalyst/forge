"""MCP: a Model Context Protocol server with scoped credentials and no administrative tools.

The server itself is the `forge_integrations.mcp` library, served by this process at /mcp. This
module connects it to the platform: platform API keys are the credentials, each client carries a
list of allowed tools, and tool calls go through the normal gateway. See docs/mcp.md.
"""

from app.modules.mcp.routes import router
from app.modules.mcp.runtime import register

__all__ = ["register", "router"]
