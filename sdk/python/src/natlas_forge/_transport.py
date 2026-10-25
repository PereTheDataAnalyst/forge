"""The default HTTP transport, built on the standard library.

It never follows redirects and never reads proxy settings from the environment, so an API key is
only ever sent to the address that was configured. A custom transport can be passed to the client
if a proxy or a different HTTP library is needed.
"""

from __future__ import annotations

import http.client
import socket
import ssl
from dataclasses import dataclass
from typing import Callable, Dict, Mapping, Optional
from urllib.parse import urlsplit

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class RawResponse:
    status: int
    headers: Dict[str, str]  # keys are lower case
    body: bytes


class TransportError(Exception):
    """The request did not produce a response.

    ``connect_stage`` is True when nothing was sent, so repeating the request cannot do it twice.
    """

    def __init__(self, reason: str, *, connect_stage: bool, timed_out: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.connect_stage = connect_stage
        self.timed_out = timed_out


class ResponseTooLarge(Exception):
    pass


# method, url, headers, body, timeout seconds -> response
Transport = Callable[[str, str, Mapping[str, str], Optional[bytes], float], RawResponse]


def default_transport(
    method: str, url: str, headers: Mapping[str, str], body: Optional[bytes], timeout: float
) -> RawResponse:
    parts = urlsplit(url)
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    if parts.scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            parts.hostname, parts.port, timeout=timeout, context=ssl.create_default_context()
        )
    else:
        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
    response: Optional[http.client.HTTPResponse] = None
    try:
        try:
            connection.connect()
        except (socket.timeout, TimeoutError) as exc:
            raise TransportError(type(exc).__name__, connect_stage=True, timed_out=True) from None
        except (OSError, ssl.SSLError) as exc:
            raise TransportError(type(exc).__name__, connect_stage=True) from None
        try:
            connection.request(method, path, body=body, headers=dict(headers))
            response = connection.getresponse()
            data = response.read(MAX_RESPONSE_BYTES + 1)
        except (socket.timeout, TimeoutError) as exc:
            raise TransportError(type(exc).__name__, connect_stage=False, timed_out=True) from None
        except (OSError, http.client.HTTPException) as exc:
            raise TransportError(type(exc).__name__, connect_stage=False) from None
        if len(data) > MAX_RESPONSE_BYTES:
            raise ResponseTooLarge()
        return RawResponse(
            status=response.status,
            headers={name.lower(): value for name, value in response.getheaders()},
            body=data,
        )
    finally:
        # A response read only in part (too large, or a timeout while reading) keeps its file
        # handle open until it is closed, so close it explicitly as well as the connection.
        if response is not None:
            response.close()
        connection.close()
