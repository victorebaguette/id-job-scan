# ID Job Scanner

Daily automated industrial design job scanner. Runs on AWS via systemd timer at 08:00 CET.

## Sources
- LinkedIn Guest API (15 queries × multiple locations)
- leManoosh (design-focused job board)
- RemoteOK (remote positions)
- 80+ curated company watchlist

## Features
- 🔍 Scores each job for industrial design relevance (Rhino, KeyShot, SolidWorks = +2, UX/UI = -2)
- 🆕 Daily diff — only reports NEW jobs since yesterday
- 📦 Auto-pushes reports to this repo
- 📨 Sends rich Telegram summary with collapsible panels to MTQ Tickers

## Output
- `reports/Job_Scan_YYYY-MM-DD.md` — full daily report

