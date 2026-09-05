"""Data-source adapters.

Each adapter reports its own availability so the daily brief can state
exactly which sources contributed and which were unreachable, instead of
silently degrading.
"""

from swingtrader.providers.base import Bar, History, ProviderStatus, Fundamental

__all__ = ["Bar", "History", "ProviderStatus", "Fundamental"]
