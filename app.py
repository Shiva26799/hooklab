import os
import sqlite3
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv, set_key

import dogtrend

# Page Configuration
st.set_page_config(
    page_title="HookLab | Content Intelligence & Script Direction",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

ROOT = Path(__file__).parent
ENV_PATH = ROOT / ".env"
REPORTS_DIR = ROOT / "reports"
DB_PATH = ROOT / "data" / "dogtrend.db"

load_dotenv(dotenv_path=ENV_PATH)

# Sync Streamlit Cloud secrets to environment if deployed
if hasattr(st, "secrets"):
    try:
        for k, v in st.secrets.items():
            if isinstance(v, str):
                os.environ[k] = v
    except Exception:
        pass


def get_db_stats():
    """Return counts of reels in the database."""
    if not DB_PATH.exists():
        return {"total": 0, "done": 0, "found": 0, "failed": 0}
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute("SELECT status, count(*) FROM reels GROUP BY status").fetchall()
        con.close()
        counts = dict(rows)
        return {
            "total": sum(counts.values()),
            "done": counts.get("done", 0),
            "found": counts.get("found", 0),
            "failed": counts.get("failed", 0),
        }
    except Exception:
        return {"total": 0, "done": 0, "found": 0, "failed": 0}


def get_past_reports():
    """List all saved markdown reports sorted newest first."""
    if not REPORTS_DIR.exists():
        return []
    files = list(REPORTS_DIR.glob("report_*.md"))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


# ----------------------------------------------------------------- Sidebar
with st.sidebar:
    st.title("🎬 HookLab")
    st.caption("Short-form content research & creative direction")

    # DB Stats
    stats = get_db_stats()
    st.subheader("📊 Local Database")
    col1, col2 = st.columns(2)
    col1.metric("Analyzed", stats["done"])
    col2.metric("Discovered", stats["total"])

    st.divider()

    # Past Reports
    st.subheader("📁 Saved Reports")
    past_reports = get_past_reports()
    selected_report_file = None
    if past_reports:
        report_options = {f.name: f for f in past_reports}
        selected_report_name = st.selectbox(
            "Select a report to view",
            options=list(report_options.keys()),
            index=0,
        )
        selected_report_file = report_options[selected_report_name]
    else:
        st.info("No saved reports yet. Run your first topic on the right!")

    st.divider()

    # Settings Accordion
    with st.expander("⚙️ Settings & API Keys"):
        sc_key = st.text_input("SocialCrawl Key", value=os.getenv("SOCIALCRAWL_API_KEY", ""), type="password")
        ff_key = st.text_input("FrameFetch Key", value=os.getenv("FRAMEFETCH_API_KEY", ""), type="password")
        gemini_key = st.text_input("Gemini API Key", value=os.getenv("GEMINI_API_KEY", ""), type="password")
        gemini_model = st.selectbox(
            "Gemini Model",
            options=["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.5-flash"],
            index=0 if os.getenv("GEMINI_MODEL", "") == "gemini-3.5-flash-lite" else 1,
        )
        channel_goal = st.text_input("Creator Goal", value=os.getenv("GOAL", "grow views, retain watch time, and build high audience trust"))

        if st.button("Save Settings", use_container_width=True):
            set_key(str(ENV_PATH), "SOCIALCRAWL_API_KEY", sc_key)
            set_key(str(ENV_PATH), "FRAMEFETCH_API_KEY", ff_key)
            set_key(str(ENV_PATH), "GEMINI_API_KEY", gemini_key)
            set_key(str(ENV_PATH), "GEMINI_MODEL", gemini_model)
            set_key(str(ENV_PATH), "GOAL", channel_goal)
            st.success("Settings saved to .env!")
            st.rerun()


# ----------------------------------------------------------------- Main App
st.title("HookLab: Content Intelligence & Script Studio")
st.markdown("Enter any topic or niche below. HookLab discovers outlier Instagram reels, analyzes visual hooks, transcripts & audio direction, and writes production-ready scripts.")

tab_run, tab_view = st.tabs(["🚀 Run New Analysis", "📄 View Past Report"])

with tab_run:
    col_input, col_limit = st.columns([3, 1])
    with col_input:
        topic_input = st.text_input("Topic", value="dog longevity", placeholder="e.g. dog longevity, healthy meal prep, puppy biting, fitness workouts...")
    with col_limit:
        limit_input = st.slider("Reels to Read", min_value=3, max_value=20, value=5, help="Keeps API costs low and ensures quick runs")

    col_btn1, col_btn2 = st.columns([1, 1])
    run_btn = col_btn1.button("▶️ Run Analysis", type="primary", use_container_width=True)
    rebuild_btn = col_btn2.button("🔄 Rebuild Report from Existing Data", use_container_width=True, help="Builds a fresh report from all analyzed reels in the database with 0 API credit cost")

    if run_btn:
        if not topic_input.strip():
            st.error("Please enter a topic.")
        else:
            con = dogtrend.db()
            progress_bar = st.progress(0, text="Starting analysis...")

            with st.status(f"Analyzing reels for '{topic_input}'...", expanded=True) as status_box:
                # Stage 1: Search phrases
                status_box.write("🧠 **Step 1/4:** Generating search phrases with AI...")
                progress_bar.progress(15, text="Generating search phrases...")
                phrases = dogtrend.make_phrases(topic_input)
                status_box.write(f"Search phrases: `{', '.join(phrases)}`")

                # Stage 2: Finding reels
                status_box.write("🔍 **Step 2/4:** Discovering Instagram reels...")
                progress_bar.progress(35, text="Discovering reels on Instagram...")
                try:
                    found_count = dogtrend.discover(con, topic_input, phrases)
                    status_box.write(f"Found **{found_count}** new reels.")
                except Exception as e:
                    status_box.write(f"⚠️ Search note: {e} (Continuing with discovered reels in database)")

                # Stage 3: Reading reels
                status_box.write("📹 **Step 3/4:** Reading reel screenshots, transcripts & AI notes...")
                progress_bar.progress(55, text="Reading reels (screenshots & transcripts)...")
                todo = dogtrend.choose_reels(con, limit_input, topic=topic_input)

                if not todo:
                    status_box.write("No new reels available to read. Building report from existing database records...")
                else:
                    status_box.write(f"Reading {len(todo)} reels in parallel (fast mode)...")

                    def on_progress(done, total, url):
                        status_box.write(f"✓ Read {done}/{total}: `{url}`")
                        progress_bar.progress(55 + int(30 * (done / total)), text=f"Read {done}/{total} reels...")

                    dogtrend.process_reels_parallel(todo, max_workers=3, on_progress=on_progress)

                # Stage 4: Writing report
                status_box.write("📝 **Step 4/4:** Writing report and video scripts...")
                progress_bar.progress(90, text="Generating weekly report...")
                try:
                    report_path = dogtrend.build_report(con, topic_input)
                except Exception as e:
                    status_box.write(f"⚠️ Initial report generation hit high demand. Retrying in 5 seconds...")
                    import time
                    time.sleep(5)
                    report_path = dogtrend.build_report(con, topic_input)
                progress_bar.progress(100, text="Analysis complete!")
                status_box.update(label="Analysis finished successfully!", state="complete", expanded=False)

            if report_path and Path(report_path).exists():
                report_content = Path(report_path).read_text(encoding="utf-8")
                st.success(f"Report saved: `{Path(report_path).name}`")

                # Download button
                st.download_button(
                    label="📥 Download Report (.md)",
                    data=report_content,
                    file_name=Path(report_path).name,
                    mime="text/markdown",
                )

                st.markdown("---")
                st.markdown(report_content)
            else:
                st.error("Report could not be generated. Please check database entries.")

    elif rebuild_btn:
        with st.spinner("Compiling report from analyzed reels in database..."):
            con = dogtrend.db()
            report_path = dogtrend.build_report(con, topic_input)
            if report_path and Path(report_path).exists():
                report_content = Path(report_path).read_text(encoding="utf-8")
                st.success(f"Report updated: `{Path(report_path).name}`")
                st.download_button(
                    label="📥 Download Report (.md)",
                    data=report_content,
                    file_name=Path(report_path).name,
                    mime="text/markdown",
                )
                st.markdown("---")
                st.markdown(report_content)
            else:
                st.warning("No analyzed reels found in the database yet. Run an analysis first.")

with tab_view:
    if selected_report_file and selected_report_file.exists():
        content = selected_report_file.read_text(encoding="utf-8")
        st.subheader(f"Viewing: {selected_report_file.name}")
        st.download_button(
            label="📥 Download This Report (.md)",
            data=content,
            file_name=selected_report_file.name,
            mime="text/markdown",
            key="dl_past_report",
        )
        st.markdown("---")
        st.markdown(content)
    else:
        st.info("No report selected or reports folder is empty.")
