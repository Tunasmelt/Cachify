from abc import ABC, abstractmethod
from collections.abc import Mapping

from pcg.adapters.types import CacheProfile, CanonicalRequest, Usage
from pcg.config import WireFormat


class ProviderAdapter(ABC):
    @property
    @abstractmethod
    def wire_format(self) -> WireFormat:
        """Return the wire format implemented by this adapter."""

    @abstractmethod
    def ingress_routes(self) -> list[str]:
        """Return the routes accepted for this wire format."""

    @abstractmethod
    def auth_header_names(self) -> list[str]:
        """Return the client authentication header names to forward."""

    @abstractmethod
    def parse_request(self, raw_body: bytes, headers: Mapping[str, str]) -> CanonicalRequest:
        """Parse a wire request into its canonical representation."""

    @abstractmethod
    def cache_profile(self) -> CacheProfile:
        """Return provider cache behavior for this wire format."""

    @abstractmethod
    def is_cacheable_response(self, response: object) -> bool:
        """Return whether a completed response is safe to cache."""

    @abstractmethod
    def extract_usage(self, response: object) -> Usage:
        """Normalize provider usage from a response."""

    @abstractmethod
    def render_response(self, entry: object) -> object:
        """Render a stored response in this adapter's wire format."""

    @abstractmethod
    def synthesize_sse(self, entry: object) -> object:
        """Render a stored response as this adapter's SSE dialect."""
