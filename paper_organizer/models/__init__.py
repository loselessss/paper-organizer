"""Data models shared by the core and user interfaces."""

from .paper import DocumentIdentity, DuplicateKind, DuplicateMatch, WrapperPage

__all__ = [
    "DocumentIdentity",
    "DuplicateKind",
    "DuplicateMatch",
    "WrapperPage",
]
