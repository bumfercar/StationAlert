"""Audio source errors that avoid exposing private file paths."""


class AudioSourceError(Exception):
    """Raised when audio cannot be normalized or replayed safely."""
