# SDKs

Typed clients for the N-ATLAS Forge gateway. Both wrap the same three calls (`models.list`, `chat.create`, `audio.transcribe`), expose request IDs on every result and every error, retry only when repeating a call cannot do the work twice, and never follow a redirect with your API key.

| Language | Folder | Install |
| --- | --- | --- |
| Python 3.9 or newer | [`python`](python/README.md) | `python -m pip install ./sdk/python` |
| JavaScript and TypeScript, Node 18 or newer | [`javascript`](javascript/README.md) | `npm pack` in `sdk/javascript`, then install the tarball |

Neither package is published to PyPI or npm, and both names are provisional. The version is `0.1.0`.
