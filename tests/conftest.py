import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import ingestion, uk_demo  # noqa: E402


def make_shifts(rows: list[dict], tz: str = "Europe/London") -> pd.DataFrame:
    """Build canonical shifts through the real importer from simple dicts."""
    header = ["Employee ID", "Name", "Site", "Role", "Age band", "Contract", "Rate", "Date", "Start", "End",
              "Break", "Opt out", "Contracted hours"]
    lines = [",".join(header)]
    for i, r in enumerate(rows):
        lines.append(",".join(str(x) for x in [
            r.get("id", "E1"), r.get("name", "Alex"), r.get("site", "Site A"), r.get("role", "Sales Advisor"),
            r.get("age", "21+"), r.get("contract", "Permanent"), r.get("rate", 12.71), r["date"], r["start"],
            r["end"], r.get("brk", 0), r.get("opt", "No"), r.get("contracted", "")]))
    data = ("\n".join(lines) + "\n").encode("utf-8")
    shifts, issues, warnings, info, mapping = ingestion.load_rota(data, "t.csv", tz_name=tz)
    assert issues.empty, issues.to_dict(orient="records")
    return shifts


@pytest.fixture(scope="session")
def demo_shifts():
    data = uk_demo.DEMO_FILE.read_bytes()
    shifts, issues, warnings, info, mapping = ingestion.load_rota(data, uk_demo.DEMO_FILE.name, "Planday export")
    assert issues.empty
    return shifts
