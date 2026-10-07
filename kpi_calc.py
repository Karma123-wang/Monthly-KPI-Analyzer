"""Monthly inpatient KPIs from the ePIS 'Admitted Discharge Patient Report'.

Definitions follow the BHSQA KPI user guide (QASD, MoH):
  KPI-50  ALOS = inpatient days of patients discharged in the month / patients discharged   (target <= 6 days)
  KPI-51  Bed occupancy = inpatient days in the month / available bed days x 100            (target 70-80 %)
  KPI-56  Discharged between 9 am and 11 am / all patients discharged x 100                  (target > 90 %)
Patients admitted and discharged the same day count as a 1-day stay.
"""
from __future__ import annotations

import pandas as pd

FMT = "%d/%m/%Y %H:%M"
REFERRED = "REFERRED TO OTHER FACILITY"
WARD, ER = "Ward (IPD)", "Emergency observation"
ER_ROW = "Emergency observation (ER IP)"

# name: (label, unit, target text, check function, higher_is_better)
TARGETS = {
    "KPI-56 Discharge 9–11 am %": ("KPI-56 Discharged 9–11 am", "%", "> 90%", lambda v: v > 90),
    "KPI-50 ALOS (days)": ("KPI-50 Average length of stay", "days", "≤ 6 days", lambda v: v <= 6),
    "KPI-51 Bed occupancy %": ("KPI-51 Bed occupancy rate", "%", "70–80%", lambda v: 70 <= v <= 80),
}


def load(files) -> pd.DataFrame:
    """Read one or more ePIS exports. Every admission row is counted, including readmissions."""
    frames = []
    for f in files if isinstance(files, (list, tuple)) else [files]:
        frames.append(pd.read_excel(f, dtype=str))
    df = pd.concat(frames, ignore_index=True).fillna("")
    df = df.apply(lambda s: s.str.strip())
    needed = ["Admission Date & Time", "Actual Discharge Date & Time", "Visit Type", "Condition on Discharge"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError("This file is missing the columns: " + ", ".join(missing))
    # Every admission counts (readmissions included). Only rows that are identical in EVERY column are dropped -
    # that only happens when the same export is uploaded twice.
    df = df.drop_duplicates()
    df["adm"] = pd.to_datetime(df["Admission Date & Time"], format=FMT, errors="coerce")
    df["dis"] = pd.to_datetime(df["Actual Discharge Date & Time"], format=FMT, errors="coerce")
    df["outcome"] = df["Condition on Discharge"].replace("", pd.NA)
    df["setting"] = df["Visit Type"].map({"IPD": WARD, "ER IP": ER}).fillna("Other")
    df["ward"] = df.get("Ward Name", pd.Series("", index=df.index)).replace("", "Unknown")
    df.loc[df["setting"] == ER, "ward"] = ER_ROW
    df["los"] = ((df["dis"].dt.normalize() - df["adm"].dt.normalize()).dt.days).clip(lower=1)
    return df[df["adm"].notna()].copy()


def data_notes(df: pd.DataFrame) -> list:
    """Plain-language data-quality notes (no patient details)."""
    d = df[df["setting"] == WARD]
    notes = []
    still = d[d["dis"].isna() & (d.get("Status", "") == "Admitted")]
    if len(still):
        notes.append(f"{len(still)} patient(s) still admitted at the end of the data; counted in patient days only.")
    nodate = d[d["dis"].isna() & (d.get("Status", "") != "Admitted")]
    if len(nodate):
        notes.append(f"{len(nodate)} record(s) without a discharge date were left out of discharge KPIs.")
    first = d["adm"].min()
    if pd.notna(first) and first.day <= 3:
        notes.append(f"Patients admitted before {first:%d %b %Y} are not in the file, so patient days and bed "
                     f"occupancy for {first:%b %Y} may be understated.")
    return notes


def _patient_days(d: pd.DataFrame, end: pd.Timestamp) -> pd.Series:
    """Bed-days per calendar month: each night (or same-day stay) counts as one day."""
    days = []
    for adm, dis in zip(d["adm"], d["dis"]):
        last = dis if pd.notna(dis) else end
        start, stop = adm.normalize(), last.normalize()
        days.append(pd.date_range(start, max(start, stop - pd.Timedelta(days=1))))
    if not days:
        return pd.Series(dtype=float)
    idx = days[0].append(days[1:]) if len(days) > 1 else days[0]
    s = pd.Series(1, index=idx)
    return s.groupby(s.index.to_period("M")).sum()


def monthly(df: pd.DataFrame, beds: int = 40, include_er: bool = False, wards=None) -> pd.DataFrame:
    d = df[df["setting"].isin([WARD, ER] if include_er else [WARD])]
    if wards:
        d = d[d["ward"].isin(wards)]
    if d.empty:
        return pd.DataFrame()
    end = d["adm"].max().normalize() + pd.Timedelta(days=1)
    disc = d[d["dis"].notna()]
    dm = disc["dis"].dt.to_period("M")
    m = pd.DataFrame(index=pd.period_range(d["adm"].min(), d["adm"].max(), freq="M"))
    m["Admissions"] = d.groupby(d["adm"].dt.to_period("M")).size()
    m["Discharges"] = disc.groupby(dm).size()
    m["Referred out"] = disc[disc["outcome"] == REFERRED].groupby(dm).size()
    m["Referral %"] = m["Referred out"] / m["Discharges"] * 100
    m["Deaths"] = disc[disc["outcome"] == "EXPIRED"].groupby(dm).size()
    m["LAMA / absconded"] = disc[disc["outcome"].isin(["LAMA", "ABSCONDED"])].groupby(dm).size()
    h = disc["dis"].dt.hour
    m["Discharged 9–11 am"] = disc[(h >= 9) & (h < 11)].groupby(dm).size()
    m["KPI-56 Discharge 9–11 am %"] = m["Discharged 9–11 am"] / m["Discharges"] * 100
    m["Discharged before 9 am"] = disc[h < 9].groupby(dm).size()
    m["Discharged after 11 am"] = disc[h >= 11].groupby(dm).size()
    m["Inpatient days of discharged"] = disc.groupby(dm)["los"].sum()
    m["KPI-50 ALOS (days)"] = m["Inpatient days of discharged"] / m["Discharges"]
    m["Long stays (> 30 days)"] = disc[disc["los"] > 30].groupby(dm).size()
    m["Patient days"] = _patient_days(d, end)
    m["Bed days available"] = [beds * p.days_in_month for p in m.index]
    m["KPI-51 Bed occupancy %"] = m["Patient days"] / m["Bed days available"] * 100
    m = m.fillna(0)
    for c in ["Admissions", "Discharges", "Referred out", "Deaths", "LAMA / absconded", "Discharged 9–11 am",
              "Discharged before 9 am", "Discharged after 11 am", "Inpatient days of discharged",
              "Long stays (> 30 days)", "Patient days", "Bed days available"]:
        m[c] = m[c].astype(int)
    m.index = m.index.strftime("%b %Y")
    m.index.name = "Month"
    return m


def er_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """Emergency observation (ER IP) – reported separately, not part of the ward KPIs."""
    d = df[df["setting"] == ER]
    if d.empty:
        return pd.DataFrame()
    disc = d[d["dis"].notna()].copy()
    disc["hours"] = (disc["dis"] - disc["adm"]).dt.total_seconds() / 3600
    dm = disc["dis"].dt.to_period("M")
    m = pd.DataFrame(index=pd.period_range(d["adm"].min(), d["adm"].max(), freq="M"))
    m["Admissions"] = d.groupby(d["adm"].dt.to_period("M")).size()
    m["Discharges"] = disc.groupby(dm).size()
    m["Referred out"] = disc[disc["outcome"] == REFERRED].groupby(dm).size()
    m["Referral %"] = m["Referred out"] / m["Discharges"] * 100
    m["Sent to ward"] = disc[disc["outcome"] == "TO BE ADMITTED"].groupby(dm).size()
    m["Sessions (day care)"] = disc[disc["outcome"].isin(["SESSION COMPLETED", "INCOMPLETE SESSION"])].groupby(dm).size()
    m["Deaths"] = disc[disc["outcome"] == "EXPIRED"].groupby(dm).size()
    m["LAMA / absconded"] = disc[disc["outcome"].isin(["LAMA", "ABSCONDED"])].groupby(dm).size()
    m["Median stay (hours)"] = disc.groupby(dm)["hours"].median()
    m["Stayed > 24 hours"] = disc[disc["hours"] > 24].groupby(dm).size()
    m = m.fillna(0)
    for c in ["Admissions", "Discharges", "Referred out", "Sent to ward", "Sessions (day care)", "Deaths",
              "LAMA / absconded", "Stayed > 24 hours"]:
        m[c] = m[c].astype(int)
    m.index = m.index.strftime("%b %Y")
    m.index.name = "Month"
    return m


def by_ward(df: pd.DataFrame, months=None, include_er: bool = False) -> pd.DataFrame:
    d = df[df["setting"].isin([WARD, ER] if include_er else [WARD])]
    disc = d[d["dis"].notna()]
    if months:
        d = d[d["adm"].dt.strftime("%b %Y").isin(months)]
        disc = disc[disc["dis"].dt.strftime("%b %Y").isin(months)]
    g = pd.DataFrame({
        "Admissions": d.groupby("ward").size(),
        "Discharges": disc.groupby("ward").size(),
        "Referred out": disc[disc["outcome"] == REFERRED].groupby("ward").size(),
        "ALOS (days)": disc.groupby("ward")["los"].mean(),
        "Discharge 9–11 am %": disc.groupby("ward")["dis"].apply(lambda s: ((s.dt.hour >= 9) & (s.dt.hour < 11)).mean() * 100),
    }).fillna(0)
    g["Admissions"] = g["Admissions"].astype(int)
    g["Discharges"] = g["Discharges"].astype(int)
    g["Referred out"] = g["Referred out"].astype(int)
    if ER_ROW in g.index:                       # ALOS in days / 9–11 am rule are ward measures
        g.loc[ER_ROW, ["ALOS (days)", "Discharge 9–11 am %"]] = float("nan")
    g.index.name = "Ward"
    g = g.sort_values("Admissions", ascending=False)
    return pd.concat([g.drop(index=ER_ROW, errors="ignore"), g.loc[[x for x in [ER_ROW] if x in g.index]]])


def discharge_hours(df: pd.DataFrame, months=None, include_er: bool = False) -> pd.Series:
    d = df[df["setting"].isin([WARD, ER] if include_er else [WARD]) & df["dis"].notna()]
    if months:
        d = d[d["dis"].dt.strftime("%b %Y").isin(months)]
    return d["dis"].dt.hour.value_counts().reindex(range(24), fill_value=0)


def status(kpi: str, value: float) -> bool | None:
    """True = target met, False = not met, None = no target."""
    if kpi not in TARGETS or pd.isna(value):
        return None
    return bool(TARGETS[kpi][3](value))


def auto_findings(m: pd.DataFrame) -> str:
    """Draft findings from the monthly table (the user can edit them)."""
    if m.empty:
        return ""
    out = [f"{m['Admissions'].sum():,} ward admissions and {m['Discharges'].sum():,} discharges "
           f"from {m.index[0]} to {m.index[-1]}."]
    for kpi, (label, unit, target, _) in TARGETS.items():
        vals = m[kpi]
        met = sum(status(kpi, v) for v in vals)
        rng = f"{vals.min():.1f}–{vals.max():.1f}{'%' if unit == '%' else ' days'}"
        out.append(f"{label}: target {target} met in {met} of {len(vals)} months (range {rng}).")
    worst_ref = m["Referral %"].idxmax()
    out.append(f"Referrals to other facilities: {m['Referred out'].sum()} patients "
               f"({m['Referred out'].sum() / max(1, m['Discharges'].sum()) * 100:.1f}% of discharges); "
               f"highest in {worst_ref} ({m.loc[worst_ref, 'Referral %']:.1f}%).")
    if m["Long stays (> 30 days)"].sum():
        months = ", ".join(m.index[m["Long stays (> 30 days)"] > 0])
        out.append(f"{m['Long stays (> 30 days)'].sum()} long stays over 30 days raised ALOS in: {months}.")
    return "\n".join(out)
