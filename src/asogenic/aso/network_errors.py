"""Network error taxonomy for the ASO research clients. Port of
reference/swift/Services/ASO/ASONetworkError.swift.

Research swallows every one of these per-seed/per-term — a partial pool degrades the field, a
thrown error never fails the run.
"""

from __future__ import annotations


class ASONetworkError(Exception):
    pass


class UnsupportedStorefrontError(ASONetworkError):
    def __init__(self, locale: str) -> None:
        super().__init__(f"unsupported storefront for locale '{locale}'")
        self.locale = locale


class InvalidResponseError(ASONetworkError):
    pass


class TransportError(ASONetworkError):
    def __init__(self, underlying: str) -> None:
        super().__init__(f"transport error: {underlying}")
        self.underlying = underlying


class HTTPStatusError(ASONetworkError):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


class RateLimitedError(ASONetworkError):
    pass


class DecodingError(ASONetworkError):
    def __init__(self, underlying: str) -> None:
        super().__init__(f"decoding error: {underlying}")
        self.underlying = underlying
