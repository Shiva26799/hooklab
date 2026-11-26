# Dog Trend Finder: Setup Guide

You type a topic. The system finds Instagram reels, reads them (screenshots, transcript, on-screen text), works out what is winning, and writes a report with video ideas.

## What you need (one time, about 15 minutes)

1. **Python 3.10 or newer.** Get it at https://www.python.org/downloads/ (on Windows, tick "Add Python to PATH" during install).
2. **Your three keys:**
   - SocialCrawl: https://www.socialcrawl.dev (finds the reels)
   - FrameFetch: https://framefetch.net (screenshots, transcript, on-screen text)
   - Gemini: https://aistudio.google.com (reads the reels and writes the report)

## Install

Open a terminal (Windows: search "Command Prompt") inside this folder, then run:

```
py -m pip install -r requirements.txt
copy .env.example .env
notepad .env
```

On Mac or Linux use `python3 -m pip install -r requirements.txt`, `cp .env.example .env`, and open `.env` in any text editor.

In the `.env` file, paste each key after its `=` sign. Save and close.

## Test your keys

```
py dogtrend.py check
```

You should see three lines saying OK. If one says PROBLEM, fix that key and run it again.

## Run it

### Option A: One-Click Web Dashboard (Recommended)

```
streamlit run app.py
```
This opens the visual DogTrend dashboard in your browser. Type any topic, click **Run Analysis**, and see the finished video scripts and reports on the same page with a 1-click Download button.

### Option B: Command Line

```
python dogtrend.py run "dog longevity"
```

It takes a few minutes. When it finishes, open the newest file in the `reports` folder.

## What each step costs

- SocialCrawl: about 1 credit per page of search results. The default cap is 15 credits per run (you get 100 free).
- FrameFetch: a small charge per reel. The default is 20 reels per run.
- Gemini: very small. The free tier works for testing.

Change `MAX_REELS`, `SC_MAX_CREDITS` and other limits in `.env` any time.

## Run it every week (optional)

Windows: open "Task Scheduler", create a weekly task, and set the action to run `py dogtrend.py run "dog longevity"` in this folder.

## If something fails on the first run

Run it again with `--debug`:

```
py dogtrend.py --debug run "dog longevity"
```

This saves the raw replies from each service in `data/debug`. Your keys are never saved there, but open a file and check before you share it with anyone.

## Rules to follow

- Never paste your keys in chat, email or a shared document. Never share the `.env` file.
- A person must check every health claim in a script before a video goes out.
- The system keeps only transcripts, text, numbers and its own notes. It does not save videos or screenshots. Do not repost other creators' content.
- If a team member shares keys, passwords or client data, tell your admin.
- Free Gemini keys may let Google use your prompts to improve its products. Use the system only on public reels, never on client or private data, until you move to a paid key.

## Good to know

- The first version covers Instagram. Reddit is the next phase.
- With fewer than about 20 reels the patterns are early signals, not proof. The report says so.
- Some reels fail to load. The system skips them and carries on.
