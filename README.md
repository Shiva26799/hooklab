# 🎬 HookLab: Short-Form Content Intelligence Studio

HookLab is an automated short-form video intelligence platform. Given any topic, keyword, or creator vertical, HookLab discovers high-performing Instagram reels, extracts transcripts, screenshots, and on-screen text, and generates ready-to-record video blueprints (0–2s hooks, spoken scripts, visual B-roll framing, and audio direction) grounded in real engagement data.

---

## ⚡ Key Capabilities

- **Automated Instagram Discovery**: Generates high-intent search terms and crawls top-performing reels via SocialCrawl.
- **Multimodal Video Extraction**: Pulls transcripts, on-screen text overlays, and key visual frames via FrameFetch.
- **Parallel Processing Engine**: Uses multi-threaded extraction (`ThreadPoolExecutor`) for high-speed batch processing.
- **Production-Ready Blueprints**:
  - **Hook (0–2s)**: Scroll-stopping verbal & visual pattern interrupts.
  - **The Script**: 30–40s natural word-for-word spoken dialogue.
  - **Video Direction**: Exact camera setup, POV prep, B-roll cuts, and text overlays.
  - **Audio Direction**: Voice delivery tone, sound effects / ASMR, and background music styling.
  - **The Data**: Grounded in real likes, comments, and engagement scores from winning reels.
- **Zero-Loss Persistence**: Caches all discovered and analyzed reels in a local SQLite database for instant, zero-credit report rebuilding.

---

## 🚀 Quickstart (Local)

1. **Clone & Install**:
   ```bash
   git clone https://github.com/Shiva26799/hooklab.git
   cd hooklab
   pip install -r requirements.txt
   ```

2. **Configure API Keys**:
   Copy `.env.example` to `.env` and insert your keys:
   ```env
   SOCIALCRAWL_API_KEY=your_key
   FRAMEFETCH_API_KEY=your_key
   GEMINI_API_KEY=your_key
   ```

3. **Launch the Dashboard**:
   ```bash
   streamlit run app.py
   ```
   Open `http://localhost:8501` in your browser.

---

## ☁️ Deployment (Streamlit Cloud)

1. Connect your repository to **Streamlit Community Cloud**.
2. In **Advanced Settings ➔ Secrets**, add:
   ```toml
   SOCIALCRAWL_API_KEY = "your_key"
   FRAMEFETCH_API_KEY = "your_key"
   GEMINI_API_KEY = "your_key"
   GEMINI_MODEL = "gemini-3.5-flash"
   ```
3. Click **Deploy**.
