import pytest

from vigil.data import available, get_dataset


def test_both_sources_registered():
    assert {"mimic-iv-ed", "nhamcs"} <= set(available())


def test_unknown_dataset_names_the_alternatives():
    with pytest.raises(KeyError, match="mimic-iv-ed"):
        get_dataset("not-a-dataset")


def test_nhamcs_refuses_trajectory_work_with_a_reason():
    """Plan B must fail loudly on the one thing it cannot support, not return junk."""
    from vigil.data.nhamcs import NHAMCS

    assert NHAMCS.SUPPORTS_TRAJECTORY is False
    with pytest.raises(NotImplementedError, match="one observation set per visit"):
        NHAMCS(root=".").vitals()
