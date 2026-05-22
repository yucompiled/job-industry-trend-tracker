import streamlit as st
import plotly.express as px
import pandas as pd
from utils.db_connection import get_connection

st.set_page_config(page_title="Job Industry Trend Tracker", layout="wide")
st.title("Job Industry Trend Tracker")

# Each function loads one Gold table in full. Filtering happens in Python after loading,
# so changing the sidebar doesn't trigger a new database query each time.
def load_role_demand():
    conn = get_connection()
    query = "SELECT snapshot_date, category, country, total_postings, new_postings_today FROM gold_role_demand"
    df = pd.read_sql(query, conn)
    conn.close()
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"], utc=True).dt.tz_convert(None).dt.normalize()
    return df.sort_values("snapshot_date")

def load_salary_trend():
    conn = get_connection()
    query = "SELECT snapshot_date, category, country, avg_midpoint FROM gold_salary_trend"
    df = pd.read_sql(query, conn)
    conn.close()
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"], utc=True).dt.tz_convert(None).dt.normalize()
    return df.sort_values("snapshot_date")

def load_skill_frequency():
    conn = get_connection()
    query = "SELECT snapshot_date, skill, country, category, pct_of_postings, skill_posting_count, total_postings FROM gold_skill_frequency"
    df = pd.read_sql(query, conn)
    conn.close()
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"], utc=True).dt.tz_convert(None).dt.normalize()
    return df.sort_values("snapshot_date")

# Load all three Gold tables once at startup.
df_role = load_role_demand()
df_salary = load_salary_trend()
df_skill = load_skill_frequency()

# Sidebar filters. Options are pulled from the data itself so new countries or
# categories show up automatically as the dataset grows.
with st.sidebar:
    st.header("Filters")
    selected_country = st.selectbox("Select Country", options=sorted(df_role["country"].unique()))
    selected_category = st.selectbox("Select Category", options=sorted(df_role["category"].unique()))

# Apply sidebar selections to each dataset. No new queries, just filtering in memory.
df_role_filtered = df_role[(df_role["country"] == selected_country) & (df_role["category"] == selected_category)]
df_salary_filtered = df_salary[(df_salary["country"] == selected_country) & (df_salary["category"] == selected_category)]
df_skill_filtered = df_skill[(df_skill["country"] == selected_country) & (df_skill["category"] == selected_category)]

# st.dataframe(df_role_filtered)

# Trend charts side by side.
col1, col2 = st.columns(2)
with col1:
    fig_role = px.line(df_role_filtered, x="snapshot_date", y="new_postings_today", title="New Postings Per Day", markers=True, labels={"new_postings_today": "New Unique Postings", "snapshot_date": "Date"})
    fig_role.update_layout(yaxis=dict(rangemode="tozero"))
    st.plotly_chart(fig_role, use_container_width=True)

with col2:
    fig_salary = px.line(df_salary_filtered, x="snapshot_date", y="avg_midpoint", title="Average Salary Midpoint Over Time", labels={"avg_midpoint": "Average Salary Midpoint", "snapshot_date": "Date"})
    st.plotly_chart(fig_salary, use_container_width=True)

# Skill frequency snapshot for the most recent date only.
# Filtering to one date avoids duplicate bars when multiple snapshots exist.
if df_skill_filtered.empty:
    st.info("No skill data available for the selected filters.")
else:
    latest_date = df_skill_filtered["snapshot_date"].max()
    st.subheader(f"Latest Top Skills Demand - {latest_date.strftime('%Y-%m-%d')}")
    df_skill_top = df_skill_filtered[df_skill_filtered["snapshot_date"] == latest_date].sort_values(by="pct_of_postings", ascending=False).head(15).sort_values(by="pct_of_postings", ascending=True)
    sample_size = df_skill_top["total_postings"].iloc[0]

    fig_skill = px.bar(df_skill_top, x="pct_of_postings", y="skill", orientation="h", title="Top 15 Skills by Percentage of Job Postings", labels={"pct_of_postings": "% of Job Postings", "skill": "Skill"})
    fig_skill.update_layout(xaxis_tickformat=".2f")
    st.plotly_chart(fig_skill, use_container_width=True)

    st.caption(f"Based on {sample_size} total postings")
