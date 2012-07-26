from django.core.cache import cache


class NoCache(object):
    """
    A simplified, swappable base class for caching.
    
    Does nothing save for simulating the cache API.
    """
    def get(self, key):
        """
        Always returns ``None``.
        """
        return None
    
    def set(self, key, value, timeout=60):
        """
        No-op for setting values in the cache.
        """
        pass

