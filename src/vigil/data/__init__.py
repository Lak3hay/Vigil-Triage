"""Dataset loaders, normalised to one canonical schema."""
from vigil.data.base import available, get_dataset, register  # noqa: F401
from vigil.data import mimic, nhamcs  # noqa: F401  (registers the loaders)

__all__ = ["get_dataset", "available", "register"]
