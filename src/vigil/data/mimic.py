"""MIMIC-IV-ED loader.

Handles both the open-access demo subset and the full credentialed release -
they share a schema, so only the path differs.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from vigil.data import schema
from vigil.data.base import register

# Celsius and Fahrenheit body-temperature ranges do not overlap (45 C < 77 F),
# so per-value unit detection is unambiguous rather than a guess.
_C_RANGE = (25.0, 45.0)
_F_RANGE = (77.0, 113.0)


def to_celsius(temp: pd.Series) -> pd.Series:
    """Normalise MIMIC-ED temperatures, which are charted in MIXED units.

    The column is mostly Fahrenheit but a minority of rows are already Celsius
    (6.2% of the demo subset). Converting the whole column turns a normal 36.8 C
    into 2.7 C, which then reads as a data error and gets discarded -- silently
    deleting the healthiest-looking observations.

    Values in neither plausible range are left as NaN for the plausibility filter
    to count and drop.
    """
    t = pd.to_numeric(temp, errors="coerce")
    out = pd.Series(pd.NA, index=t.index, dtype="Float64")
    is_c = t.between(*_C_RANGE)
    is_f = t.between(*_F_RANGE)
    out[is_c] = t[is_c]
    out[is_f] = (t[is_f] - 32.0) * 5.0 / 9.0
    return out.astype("float64")


@register("mimic-iv-ed")
class MimicIVED:
    """Loads MIMIC-IV-ED into the canonical schema.

    Parameters
    ----------
    root : path containing ``ed/`` with the six .csv.gz tables.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.ed = self.root / "ed"
        if not self.ed.is_dir():
            raise FileNotFoundError(
                f"expected {self.ed} to exist. Run `python -m vigil.data.fetch_demo` "
                "for the open-access subset."
            )
        self._plausibility_report: dict[str, int] = {}

    def _read(self, table: str) -> pd.DataFrame:
        return pd.read_csv(self.ed / f"{table}.csv.gz")

    def stays(self) -> pd.DataFrame:
        df = self._read("edstays")
        out = pd.DataFrame({
            "stay_id": df["stay_id"],
            "patient_id": df["subject_id"],
            "intime": pd.to_datetime(df["intime"]),
            "outtime": pd.to_datetime(df["outtime"]),
            "disposition": df["disposition"],
        })
        return schema.validate_stays(out)

    def triage(self) -> pd.DataFrame:
        df = self._read("triage")
        out = pd.DataFrame({
            "stay_id": df["stay_id"],
            "patient_id": df["subject_id"],
            "acuity": df["acuity"],
            "chief_complaint": df["chiefcomplaint"],
            "pain": pd.to_numeric(df["pain"], errors="coerce"),
        })
        return schema.validate_triage(out)

    def vitals(self, include_triage: bool = True) -> pd.DataFrame:
        """Repeated observations through the stay, in the canonical schema.

        Parameters
        ----------
        include_triage
            Prepend the triage observation as the t=0 reading. On by default and
            clinically correct: triage vitals *are* vital signs, they are simply
            stored in a different table. Excluding them drops the single most
            important early reading and pushes every stay's first observation
            later, which silently biases early landmarks toward patients who
            happened to be re-checked quickly.

            **Assumption, stated because it is not in the data:** the triage
            table carries no charttime, so it is timestamped at ``intime``.
        """
        df = self._read("vitalsign")
        out = pd.DataFrame({
            "stay_id": df["stay_id"],
            "patient_id": df["subject_id"],
            "charttime": pd.to_datetime(df["charttime"]),
            "hr": pd.to_numeric(df["heartrate"], errors="coerce"),
            "rr": pd.to_numeric(df["resprate"], errors="coerce"),
            "spo2": pd.to_numeric(df["o2sat"], errors="coerce"),
            "sbp": pd.to_numeric(df["sbp"], errors="coerce"),
            "dbp": pd.to_numeric(df["dbp"], errors="coerce"),
            # Charted in MIXED units - see to_celsius().
            "temp_c": to_celsius(df["temperature"]),
        })
        if include_triage:
            out = pd.concat([self._triage_as_observation(), out], ignore_index=True)

        out, self._plausibility_report = schema.apply_plausibility(out)
        # The raw table is unordered; downstream "last reading" logic depends on this.
        out = out.sort_values(["stay_id", "charttime"], kind="mergesort").reset_index(drop=True)
        return schema.validate_vitals(out)

    def _triage_as_observation(self) -> pd.DataFrame:
        """The triage row, shaped as a vitals observation timestamped at intime."""
        tri = self._read("triage")
        intime = self._read("edstays").set_index("stay_id")["intime"]
        return pd.DataFrame({
            "stay_id": tri["stay_id"],
            "patient_id": tri["subject_id"],
            "charttime": pd.to_datetime(tri["stay_id"].map(intime)),
            "hr": pd.to_numeric(tri["heartrate"], errors="coerce"),
            "rr": pd.to_numeric(tri["resprate"], errors="coerce"),
            "spo2": pd.to_numeric(tri["o2sat"], errors="coerce"),
            "sbp": pd.to_numeric(tri["sbp"], errors="coerce"),
            "dbp": pd.to_numeric(tri["dbp"], errors="coerce"),
            "temp_c": to_celsius(tri["temperature"]),
        }).dropna(subset=["charttime"])

    @property
    def plausibility_report(self) -> dict[str, int]:
        """Values nulled as physiologically impossible, per column. Populated by vitals()."""
        return dict(self._plausibility_report)
