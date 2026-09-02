"""Dataset registry - the seam that makes the data source swappable."""
from __future__ import annotations

from typing import Protocol

import pandas as pd


class EDDataset(Protocol):
    """Any ED dataset, normalised to the canonical schema."""

    name: str

    def stays(self) -> pd.DataFrame: ...
    def triage(self) -> pd.DataFrame: ...
    def vitals(self) -> pd.DataFrame: ...


_REGISTRY: dict[str, type] = {}


def register(name: str):
    def deco(cls):
        _REGISTRY[name] = cls
        cls.name = name
        return cls
    return deco


def get_dataset(name: str, **kwargs) -> EDDataset:
    """Build a dataset by name. This is the only place a source is chosen."""
    if name not in _REGISTRY:
        raise KeyError(f"unknown dataset {name!r}; registered: {sorted(_REGISTRY)}")
    return _REGISTRY[name](**kwargs)


def available() -> list[str]:
    return sorted(_REGISTRY)
