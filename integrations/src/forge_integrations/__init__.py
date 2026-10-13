"""N-ATLAS Forge advanced integrations.

Evaluation Lab engine, N-ATLAS ASR and voice pipeline, and the Forge MCP server.

The core has no third-party dependencies. Everything that touches the rest of the
platform (the model gateway, API keys and projects, rate limiting, storage, audit)
is expressed as a small Protocol in ``forge_integrations.ports`` so the core platform
can satisfy it without this package redesigning any platform architecture.
"""

__version__ = "0.1.0"
