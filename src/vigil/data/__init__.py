"""Dataset loaders, normalised to one canonical schema."""
from vigil.data import mimic, nhamcs  # noqa: F401  (registers the loaders)
from vigil.data.base import available, get_dataset, register  # noqa: F401

__all__ = ["get_dataset", "available", "register"]
