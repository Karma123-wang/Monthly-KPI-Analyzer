"""
Inpatient KPI Analyser – monthly BHSQA KPIs from the ePIS 'Admitted Discharge Patient Report'.
Run:  pip install -r requirements.txt
      streamlit run app.py
"""
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from kpi_calc import (ER_ROW, TARGETS, WARD, auto_findings, by_ward, data_notes, discharge_hours, er_monthly, load,
                      monthly, status)
from kpi_report import build_pdf, build_pptx, build_xlsx

st.set_page_config(page_title="Inpatient KPI Analyser", page_icon="🏥", layout="wide")

BLUE, NAVY, RED, GREEN, ORANGE = "#118DFF", "#12239E", "#D64550", "#1AAB40", "#E66C37"
FONT = "Segoe UI, Helvetica Neue, Arial, sans-serif"

st.markdown(f"""
<style>
  [data-testid="stAppViewContainer"], [data-testid="stHeader"] {{ background: #F3F2F1; }}
  .block-container {{ padding-top: 1.2rem; }}
  div[class*="st-key-card"] {{ background:#FFFFFF; border-radius:6px; padding:14px 16px 6px 16px;
      box-shadow:0 1px 3px rgba(0,0,0,.12); }}
  .pbi-header {{ background:{BLUE}; color:#FFF; padding:16px 22px; border-radius:6px; border-left:6px solid {NAVY};
      margin-bottom:14px; font-family:{FONT}; }}
  .pbi-header h1 {{ font-size:26px; margin:0; color:#FFF; font-weight:700; }}
  .pbi-header p {{ margin:4px 0 0 0; color:#E3F1FF; font-size:14px; }}
  .kpi {{ background:#FFF; border-radius:6px; padding:12px 14px; height:122px; box-shadow:0 1px 3px rgba(0,0,0,.12);
      border-top:4px solid {BLUE}; font-family:{FONT}; position:relative; }}
  .kpi.bad {{ border-top-color:{RED}; }} .kpi.good {{ border-top-color:{GREEN}; }}
  .kpi .v {{ font-size:30px; font-weight:700; color:#252423; line-height:1.1; }}
  .kpi.bad .v {{ color:{RED}; }} .kpi.good .v {{ color:{GREEN}; }}
  .kpi .l {{ font-size:12.5px; color:#605E5C; margin-top:6px; line-height:1.25; }}
  .kpi .s {{ position:absolute; top:10px; right:12px; font-size:10.5px; font-weight:700; }}
  .kpi.bad .s {{ color:{RED}; }} .kpi.good .s {{ color:{GREEN}; }}
  .card-title {{ font-family:{FONT}; font-size:15px; font-weight:600; color:#252423; margin-bottom:0; }}
</style>""", unsafe_allow_html=True)


def card_title(t):
    st.markdown(f'<p class="card-title">{t}</p>', unsafe_allow_html=True)


def kpi(col, value, label, ok=None):
    cls = "" if ok is None else ("good" if ok else "bad")
    badge = "" if ok is None else f'<div class="s">{"MET" if ok else "NOT MET"}</div>'
    col.markdown(f'<div class="kpi {cls}">{badge}<div class="v">{value}</div><div class="l">{label}</div></div>',
                 unsafe_allow_html=True)


def style(fig, height=330):
    fig.update_layout(height=height, font=dict(family=FONT, size=12, color="#252423"), paper_bgcolor="white",
                      plot_bgcolor="white", margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title_text=""))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="#EDEBE9", zeroline=False)
    return fig


# ---------------- Data ----------------
st.sidebar.header("Data")
files = st.sidebar.file_uploader("ePIS 'Admitted Discharge Patient Report' (.xlsx)", type=["xlsx"],
                                 accept_multiple_files=True,
                                 help="You can upload several monthly exports together; duplicates are removed.")
if not files:
    st.markdown('<div class="pbi-header"><h1>Inpatient KPI Analyser</h1>'
                '<p>Upload the ePIS Admitted Discharge Patient Report in the sidebar</p></div>', unsafe_allow_html=True)
    st.info("⬅️ Upload one or more monthly ePIS exports (.xlsx) to begin. Patient names are never shown.")
    st.stop()


@st.cache_data(show_spinner="Reading the file…")
def read(blobs):
    import io
    return load([io.BytesIO(b) for b in blobs])


try:
    df = read(tuple(f.getvalue() for f in files))
except ValueError as e:
    st.error(str(e))
    st.stop()

beds = st.sidebar.number_input("Number of ward beds", min_value=1, value=40, step=1)
all_months = list(monthly(df, beds).index)
if not all_months:
    st.warning("No ward (IPD) admissions found in this file.")
    st.stop()
start, end = st.sidebar.select_slider("Months", options=all_months, value=(all_months[0], all_months[-1]))
months = all_months[all_months.index(start): all_months.index(end) + 1]
ward_names = sorted(df.loc[df["setting"] == WARD, "ward"].unique())
wards_sel = st.sidebar.multiselect("Wards", ward_names, default=ward_names)
st.sidebar.caption("KPIs use ward inpatients (IPD) only. Emergency observation (ER IP) is shown separately "
                   "in its own tab.")

m = monthly(df, beds, wards=wards_sel)
m = m.loc[[x for x in months if x in m.index]]
if m.empty:
    st.warning("No data for the selected months / wards.")
    st.stop()
wards_tbl = by_ward(df[df["ward"].isin(wards_sel)], months)            # ward KPIs (reports)
wards_all = by_ward(df[df["ward"].isin(wards_sel + [ER_ROW])], months, include_er=True)  # + ER obs row
er_all = er_monthly(df)
er = er_all.loc[[x for x in m.index if x in er_all.index]] if not er_all.empty else er_all
hours = discharge_hours(df[df["ward"].isin(wards_sel)], months)
notes_data = data_notes(df)
period = f"{m.index[0]} – {m.index[-1]}" if len(m) > 1 else m.index[0]

st.markdown(f'<div class="pbi-header"><h1>Inpatient KPI Dashboard</h1>'
            f'<p>{period} · Ward inpatients · {beds} beds'
            f'{"" if len(wards_sel) == len(ward_names) else " · " + str(len(wards_sel)) + " wards selected"}</p></div>',
            unsafe_allow_html=True)

# ---------------- KPI cards for one month ----------------
month = st.selectbox("Month shown on the cards", list(m.index), index=len(m) - 1)
r = m.loc[month]
row1 = st.columns(4)
kpi(row1[0], f"{int(r['Admissions']):,}", "Admissions")
kpi(row1[1], f"{int(r['Discharges']):,}", "Discharges")
kpi(row1[2], f"{int(r['Referred out'])}", f"Referred to other facility<br>({r['Referral %']:.1f}% of discharges)")
kpi(row1[3], f"{int(r['Patient days']):,}", f"Patient days<br>(of {int(r['Bed days available']):,} bed days)")
st.write("")
row2 = st.columns(4)
for col, (k, (label, unit, target, _)) in zip(row2, TARGETS.items()):
    v = r[k]
    kpi(col, f"{v:.1f}{'%' if unit == '%' else ' days'}", f"{label}<br>Target {target}", status(k, v))
er_adm = int(er.loc[month, "Admissions"]) if month in er.index else 0
kpi(row2[3], f"{int(r['Admissions']) + er_adm:,}",
    f"All admissions<br>ward {int(r['Admissions'])} + emergency obs. {er_adm}")
for n in notes_data:
    st.caption("ℹ️ " + n)
st.write("")

tab_table, tab_trend, tab_ward, tab_time, tab_er, tab_export = st.tabs(
    ["Monthly KPIs", "Trends", "By ward", "Discharge timing", "Emergency observation", "✏️ Edit & export"])

# ---------------- Monthly table ----------------
with tab_table:
    with st.container(key="card_table"):
        card_title("Monthly KPIs – red = target not met, green = target met")
        show = m[["Admissions", "Discharges", "Referred out", "Referral %", "Discharged 9–11 am",
                  "KPI-56 Discharge 9–11 am %", "KPI-50 ALOS (days)", "Patient days", "KPI-51 Bed occupancy %",
                  "Deaths", "LAMA / absconded", "Long stays (> 30 days)"]]

        def colour(v, col):
            s = status(col, v)
            return "" if s is None else ("background-color:#E3F5E8" if s else "background-color:#FBE3E5;color:#D64550;font-weight:600")

        total_row = show.sum(numeric_only=True)
        total_row["Referral %"] = total_row["Referred out"] / max(1, total_row["Discharges"]) * 100
        total_row["KPI-56 Discharge 9–11 am %"] = total_row["Discharged 9–11 am"] / max(1, total_row["Discharges"]) * 100
        total_row["KPI-50 ALOS (days)"] = m["Inpatient days of discharged"].sum() / max(1, total_row["Discharges"])
        total_row["KPI-51 Bed occupancy %"] = total_row["Patient days"] / max(1, m["Bed days available"].sum()) * 100
        show = pd.concat([show, total_row.to_frame(f"TOTAL ({len(m)} months)").T])
        fmt = {c: "{:,.0f}" for c in show.columns}
        fmt.update({c: "{:.1f}" for c in ["Referral %", "KPI-56 Discharge 9–11 am %", "KPI-50 ALOS (days)",
                                         "KPI-51 Bed occupancy %"]})
        styler = show.style.format(fmt).apply(
            lambda r: ["font-weight:700;background-color:#EAF4FF" if str(r.name).startswith("TOTAL") else ""
                       for _ in r], axis=1)
        for c in TARGETS:
            styler = styler.apply(lambda s, c=c: [colour(v, c) for v in s], subset=[c])
        st.dataframe(styler, use_container_width=True)
        tot = m[["Admissions", "Discharges", "Referred out", "Patient days"]].sum()
        st.caption(f"Period total: {int(tot['Admissions']):,} admissions · {int(tot['Discharges']):,} discharges · "
                   f"{int(tot['Referred out'])} referred out · {int(tot['Patient days']):,} patient days. "
                   "Targets: KPI-56 > 90% · KPI-50 ≤ 6 days · KPI-51 70–80% (BHSQA KPI user guide).")

# ---------------- Trends ----------------
with tab_trend:
    c1, c2, c3 = st.columns(3)
    specs = [(c1, "KPI-56 Discharge 9–11 am %", "KPI-56 Discharged 9–11 am (> 90%)", 90, None, "%"),
             (c2, "KPI-50 ALOS (days)", "KPI-50 ALOS (≤ 6 days)", 6, None, ""),
             (c3, "KPI-51 Bed occupancy %", "KPI-51 Bed occupancy (70–80%)", None, (70, 80), "%")]
    for col, k, title, line, band, suf in specs:
        with col:
            with st.container(key=f"card_trend_{k[:6]}"):
                card_title(title)
                vals = m[k]
                fig = go.Figure()
                if band:
                    fig.add_hrect(y0=band[0], y1=band[1], fillcolor=GREEN, opacity=0.12, line_width=0)
                if line is not None:
                    fig.add_hline(y=line, line_dash="dash", line_color=GREEN)
                fig.add_scatter(x=list(m.index), y=vals, mode="lines+markers+text", line=dict(color=BLUE, width=3),
                                marker=dict(size=10, color=[GREEN if status(k, v) else RED for v in vals],
                                            line=dict(color="white", width=1.5)),
                                text=[f"{v:.1f}{suf}" for v in vals], textposition="top center",
                                hovertemplate="%{x}: %{y:.1f}" + suf + "<extra></extra>", showlegend=False)
                st.plotly_chart(style(fig, 320), use_container_width=True, key=f"trend_{k}")
    st.write("")
    c4, c5 = st.columns([1.3, 1])
    with c4:
        with st.container(key="card_flow"):
            card_title("Admissions and discharges")
            fig = go.Figure()
            fig.add_bar(x=list(m.index), y=m["Admissions"], name="Admissions", marker_color=BLUE,
                        text=m["Admissions"], textposition="outside")
            fig.add_bar(x=list(m.index), y=m["Discharges"], name="Discharges", marker_color=NAVY,
                        text=m["Discharges"], textposition="outside")
            fig.update_layout(barmode="group", bargroupgap=0.1)
            st.plotly_chart(style(fig, 340), use_container_width=True, key="flow")
    with c5:
        with st.container(key="card_ref"):
            card_title("Referred to other facility")
            fig = go.Figure()
            fig.add_bar(x=list(m.index), y=m["Referred out"], marker_color=ORANGE,
                        text=[f"{n} ({p:.0f}%)" for n, p in zip(m["Referred out"], m["Referral %"])],
                        textposition="outside", hovertemplate="%{x}: %{y} patients<extra></extra>")
            fig.update_yaxes(range=[0, max(1, m["Referred out"].max()) * 1.35])
            st.plotly_chart(style(fig, 340), use_container_width=True, key="ref")
    st.write("")
    with st.container(key="card_pdays"):
        card_title("Patient days vs available bed days")
        fig = go.Figure()
        fig.add_bar(x=list(m.index), y=m["Patient days"], name="Patient days", marker_color=BLUE,
                    text=[f"{p:,}<br>{o:.0f}%" for p, o in zip(m["Patient days"], m["KPI-51 Bed occupancy %"])],
                    textposition="outside")
        fig.add_scatter(x=list(m.index), y=m["Bed days available"], name="Bed days available", mode="lines",
                        line=dict(color="#605E5C", dash="dash"))
        fig.update_yaxes(range=[0, m["Bed days available"].max() * 1.15])
        st.plotly_chart(style(fig, 340), use_container_width=True, key="pdays")

# ---------------- By ward ----------------
with tab_ward:
    ward_month = st.selectbox("Show wards for", ["All selected months"] + list(m.index), key="ward_month")
    wm = list(m.index) if ward_month == "All selected months" else [ward_month]
    wlabel = period if ward_month == "All selected months" else ward_month
    w_ipd = by_ward(df[df["ward"].isin(wards_sel)], wm)
    w_all = by_ward(df[df["ward"].isin(wards_sel + [ER_ROW])], wm, include_er=True)
    with st.container(key="card_ward"):
        card_title(f"By ward – {wlabel}")
        wt = w_ipd[w_ipd["Admissions"] + w_ipd["Discharges"] > 0]
        wa = w_all[w_all["Admissions"] + w_all["Discharges"] > 0]
        wsub = wa.drop(index=ER_ROW, errors="ignore")

        def total_row(t, name):
            s = t[["Admissions", "Discharges", "Referred out"]].sum()
            return pd.DataFrame([{**s.to_dict(), "ALOS (days)": float("nan"), "Discharge 9–11 am %": float("nan")}],
                                index=[name])

        rows = [wsub, total_row(wsub, "WARD TOTAL (IPD)")]
        if ER_ROW in wa.index:
            rows += [wa.loc[[ER_ROW]], total_row(wa, "ALL ADMISSIONS (ward + ER obs)")]
        table = pd.concat(rows)
        table.index.name = "Ward"
        st.dataframe(table.style.format({"Admissions": "{:,.0f}", "Discharges": "{:,.0f}", "Referred out": "{:,.0f}",
                                         "ALOS (days)": "{:.1f}", "Discharge 9–11 am %": "{:.1f}"}, na_rep="–")
                     .apply(lambda r: ["font-weight:700;background-color:#EAF4FF" if str(r.name).isupper() else
                                       ("background-color:#F3EEFC" if r.name == ER_ROW else "") for _ in r], axis=1),
                     use_container_width=True)
        st.caption("ALOS and the 9–11 am discharge rate are ward measures, so they are not shown (–) for emergency "
                   "observation. Ward KPIs never include emergency observation.")
        fig = go.Figure()
        fig.add_bar(y=list(wt.index), x=wt["ALOS (days)"], orientation="h",
                    marker_color=[RED if v > 6 else BLUE for v in wt["ALOS (days)"]],
                    text=[f"{v:.1f} d" for v in wt["ALOS (days)"]], textposition="outside")
        fig.add_vline(x=6, line_dash="dash", line_color=GREEN)
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(style(fig, 60 + 38 * len(wt)), use_container_width=True, key="ward_alos")
        st.caption("ALOS by ward (red = above the 6-day target).")
    st.write("")
    with st.container(key="card_ward_month"):
        card_title("Admissions by ward and month")
        wd = df[df["ward"].isin(wards_sel + [ER_ROW])]
        wd = wd[wd["adm"].dt.strftime("%b %Y").isin(list(m.index))]
        xt = pd.crosstab(wd["ward"], wd["adm"].dt.to_period("M"))
        xt.columns = [c.strftime("%b %Y") for c in xt.columns]
        xt["Total"] = xt.sum(axis=1)
        ward_part = xt.drop(index=ER_ROW, errors="ignore").sort_values("Total", ascending=False)
        parts = [ward_part, ward_part.sum().to_frame("WARD TOTAL (IPD)").T]
        if ER_ROW in xt.index:
            parts += [xt.loc[[ER_ROW]], xt.sum().to_frame("ALL ADMISSIONS (ward + ER obs)").T]
        xt = pd.concat(parts)
        xt.index.name = "Ward"
        st.dataframe(xt.style.apply(lambda r: ["font-weight:700;background-color:#EAF4FF" if str(r.name).isupper()
                                               else ("background-color:#F3EEFC" if r.name == ER_ROW else "")
                                               for _ in r], axis=1), use_container_width=True)
        st.caption("WARD TOTAL matches the Admissions in the Monthly KPIs table; ALL ADMISSIONS adds emergency "
                   "observation.")

# ---------------- Discharge timing ----------------
with tab_time:
    with st.container(key="card_time"):
        total = max(1, hours.sum())
        card_title(f"Discharges by hour – {hours.loc[9:10].sum() / total * 100:.0f}% between 9 and 11 am, "
                   f"{hours.loc[11:].sum() / total * 100:.0f}% after 11 am")
        h = hours.loc[6:22]
        fig = go.Figure()
        fig.add_bar(x=[f"{i}:00" for i in h.index], y=h.values,
                    marker_color=[GREEN if 9 <= i < 11 else (ORANGE if i >= 11 else "#A19F9D") for i in h.index],
                    text=h.values, textposition="outside", hovertemplate="%{x}: %{y} discharges<extra></extra>")
        st.plotly_chart(style(fig, 380), use_container_width=True, key="hours")
        st.caption("Green = 9–11 am window counted by KPI-56 · orange = after 11 am · grey = before 9 am.")

# ---------------- Emergency observation ----------------
with tab_er:
    if er.empty:
        st.info("No emergency observation (ER IP) records in the selected months.")
    else:
        with st.container(key="card_er"):
            card_title("Emergency observation (ER IP) – reported separately, not part of the ward KPIs")
            er_show = er.copy()
            er_show["All admissions (ward + ER obs)"] = er_show["Admissions"] + m.loc[er_show.index, "Admissions"]
            tot = er_show.sum(numeric_only=True)
            tot["Referral %"] = tot["Referred out"] / max(1, tot["Discharges"]) * 100
            ed = df[(df["setting"] != WARD) & df["dis"].notna() & df["dis"].dt.strftime("%b %Y").isin(list(er.index))]
            tot["Median stay (hours)"] = ((ed["dis"] - ed["adm"]).dt.total_seconds() / 3600).median()
            er_show = pd.concat([er_show, tot.to_frame(f"TOTAL ({len(er)} months)").T])
            fmt = {c: "{:,.0f}" for c in er_show.columns}
            fmt.update({"Referral %": "{:.1f}", "Median stay (hours)": "{:.1f}"})
            st.dataframe(er_show.style.format(fmt).apply(
                lambda r: ["font-weight:700;background-color:#EAF4FF" if str(r.name).startswith("TOTAL") else ""
                           for _ in r], axis=1), use_container_width=True)
            st.caption("Sent to ward = discharged from observation as 'TO BE ADMITTED'. Sessions = day-care sessions "
                       "given in the emergency unit.")
        st.write("")
        e1, e2 = st.columns([1.3, 1])
        with e1:
            with st.container(key="card_er_adm"):
                card_title("Admissions: ward vs emergency observation")
                fig = go.Figure()
                fig.add_bar(x=list(er.index), y=m.loc[er.index, "Admissions"], name="Ward (IPD)", marker_color=BLUE)
                fig.add_bar(x=list(er.index), y=er["Admissions"], name="Emergency observation", marker_color="#744EC2")
                fig.update_layout(barmode="stack")
                fig.update_traces(texttemplate="%{y}", textposition="inside")
                st.plotly_chart(style(fig, 340), use_container_width=True, key="er_adm")
        with e2:
            with st.container(key="card_er_ref"):
                card_title("Emergency observation – referred to other facility")
                fig = go.Figure()
                fig.add_bar(x=list(er.index), y=er["Referred out"], marker_color=ORANGE,
                            text=[f"{n} ({p:.0f}%)" for n, p in zip(er["Referred out"], er["Referral %"])],
                            textposition="outside")
                fig.update_yaxes(range=[0, max(1, er["Referred out"].max()) * 1.35])
                st.plotly_chart(style(fig, 340), use_container_width=True, key="er_ref")

# ---------------- Edit & export ----------------
SECTIONS = {"summary": "Summary (KPI cards)", "notes": "Key findings & recommendations", "table": "Monthly KPI table",
            "trends": "Trend charts", "wards": "Wards and discharge timing",
            "er": "Emergency observation (separate)"}
ss = st.session_state
ss.setdefault("ed_title", "Inpatient KPI Report")
ss.setdefault("ed_role", "QA Unit – Tsirang Hospital")
ss.setdefault("ed_author", "Karma Wangchuk")
ss.setdefault("ed_recs", "")
ss.setdefault("ed_sections", list(SECTIONS))

with tab_export:
    suggested = auto_findings(m)
    if ss.pop("refill_findings", False):
        ss["findings_auto"] = True
        ss["ed_findings"] = ""
    if ss.get("ed_findings", "") not in ("", ss.get("last_suggested", "")):
        ss["findings_auto"] = False
    if ss.get("findings_auto", True):
        ss["ed_findings"] = suggested
    ss["last_suggested"] = suggested

    with st.expander("📂 Load saved edits"):
        saved = st.file_uploader("Saved edits file (.json)", type=["json"], key="edits_file")
        if saved is not None and ss.get("edits_id") != saved.file_id:
            try:
                e = json.loads(saved.getvalue().decode("utf-8"))
                for k in ("title", "role", "author", "findings", "recs"):
                    if k in e:
                        ss[f"ed_{k}"] = e[k]
                if e.get("sections"):
                    ss["ed_sections"] = [x for x in e["sections"] if x in SECTIONS]
                ss["edits_id"] = saved.file_id
                ss["findings_auto"] = False
                st.success("Edits loaded.")
            except Exception:
                st.error("This file could not be read.")

    with st.container(key="card_cover"):
        card_title("1 · Cover page")
        a, b, c = st.columns([2, 1.4, 1.2])
        a.text_input("Report title", key="ed_title")
        b.text_input("Unit / role", key="ed_role")
        c.text_input("Prepared by", key="ed_author")
    st.write("")
    with st.container(key="card_notes"):
        card_title("2 · Key findings & recommendations")
        st.caption("One point per line. Findings are drafted from the data – edit or add to them.")
        n1, n2 = st.columns(2)
        n1.text_area("Key findings", key="ed_findings", height=230)
        n2.text_area("Recommendations", key="ed_recs", height=230,
                     placeholder="e.g. Start ward discharge rounds at 8 am so patients leave between 9 and 11 am")
        if n1.button("↺ Refill findings from the data"):
            ss["refill_findings"] = True
            st.rerun()
    st.write("")
    with st.container(key="card_export"):
        card_title("3 · Pages and download")
        st.multiselect("Pages to include", list(SECTIONS), key="ed_sections", format_func=lambda k: SECTIONS[k])
        lines = lambda t: [x.strip(" •-*\t") for x in str(t).splitlines() if x.strip(" •-*\t")]
        notes = {"findings": lines(ss["ed_findings"]), "recommendations": lines(ss["ed_recs"])}
        cover = {"title": ss["ed_title"].strip(), "role": ss["ed_role"].strip(), "author": ss["ed_author"].strip(),
                 "period_line": f"{period}  ·  Ward inpatients  ·  {beds} beds"}
        sections = ss["ed_sections"] or list(SECTIONS)
        if "notes" in sections and not (notes["findings"] or notes["recommendations"]):
            st.warning("The findings page will be left out because both boxes are empty.")
        edits = {"title": ss["ed_title"], "role": ss["ed_role"], "author": ss["ed_author"],
                 "findings": ss["ed_findings"], "recs": ss["ed_recs"], "sections": sections}
        sig = json.dumps([edits, list(m.index), month, beds, wards_sel, [f.name for f in files]], sort_keys=True)
        if ss.get("sig") != sig:
            for ext in ("pdf", "pptx", "xlsx"):
                ss.pop(ext, None)
            ss["sig"] = sig
        stem = f"{ss['ed_title'] or 'Inpatient KPI Report'} - {period}".replace("/", "-")
        exports = [
            ("PDF", "pdf", "application/pdf",
             lambda: build_pdf(m, wards_tbl, hours, month, cover, notes, sections, notes_data, er)),
            ("PowerPoint", "pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation",
             lambda: build_pptx(m, wards_tbl, hours, month, cover, notes, sections, notes_data, er)),
            ("Excel (KPI table)", "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             lambda: build_xlsx(m, wards_all, cover, notes_data, er)),
        ]
        for col, (label, ext, mime, make) in zip(st.columns(3), exports):
            with col:
                if st.button(f"Create {label}", use_container_width=True):
                    with st.spinner(f"Building {label}…"):
                        ss[ext] = make()
                if ext in ss:
                    st.download_button(f"⬇️ Download {label}", ss[ext], f"{stem}.{ext}", mime,
                                       use_container_width=True)
        st.caption(f"The summary page uses the month chosen above the cards ({month}). "
                   "Reports contain totals only – no patient names or details.")
        st.divider()
        st.download_button("💾 Save my edits (to reuse next month)", json.dumps(edits, indent=2, ensure_ascii=False),
                           "KPI report edits.json", "application/json")
