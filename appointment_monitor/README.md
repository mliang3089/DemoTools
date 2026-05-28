# Greek Consulate NY Visa Appointment Monitor

Polls the SuperSaas schedule at
<https://www.supersaas.com/schedule/Greek_Consulate_New_York/Visa_Department>
and emails you when any appointment day opens up before a cutoff date
(default **2026-08-01**, i.e. anything in May, June, or July).

Standalone Python — does not depend on the rest of the `DemoTools` repo.

## Setup

```bash
cd appointment_monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env — fill in SMTP_USER and a Gmail App Password for SMTP_PASS
```

To get a Gmail App Password: enable 2-Step Verification on your Google
account, then create one at <https://myaccount.google.com/apppasswords>.

## Verify the parser before turning on email

The first time, run in dry-run mode and save the HTML so you can sanity-check
that the script correctly identifies available days:

```bash
set -a; source .env; set +a
python monitor.py --dry-run --debug-dir debug_html -v
```

The log will show `YYYY-MM: N available day(s) detected`. If the count looks
wrong, open `debug_html/2026-05.html` in a browser and compare with the live
calendar. If selectors need tweaking, edit `AVAILABLE_TOKENS` /
`UNAVAILABLE_TOKENS` / `parse_available_dates` in `monitor.py`.

## Run for real

```bash
set -a; source .env; set +a
python monitor.py
```

A run prints what's currently open and sends one email per newly-opened day.
Already-alerted dates are remembered in `seen_slots.json`; if a date closes
again it'll re-alert if it reopens.

## Schedule with GitHub Actions (no laptop required)

A workflow at `.github/workflows/appointment-monitor.yml` runs this script
every 15 minutes on GitHub's runners. Setup:

1. Repo → Settings → Secrets and variables → Actions → **New repository secret**.
   Add three secrets:
   - `SMTP_USER` — your Gmail address
   - `SMTP_PASS` — your 16-character Gmail App Password
   - `TO_EMAIL` — `annie.liang94@gmail.com`
2. Repo → Settings → Actions → General → **Workflow permissions** →
   set to **Read and write permissions** (so the workflow can commit the
   state file back).
3. Repo → Actions tab → enable workflows if prompted.
4. Trigger one manual run from the Actions tab using **Run workflow**
   (tick the *dry_run* box the first time to verify the parser without
   sending email).

The workflow commits `seen_slots.json` back to the branch on each run so
state survives between runs. Pick one of GitHub Actions or local cron —
running both will fight over the state file.

For private repos, every 15 minutes uses roughly 1500 of the 2000 free
Actions minutes/month — bump the cron to `*/30` in the workflow if that's
too tight.

## Schedule with local cron

Every 10 minutes (adjust to taste — don't hammer the site):

```cron
*/10 * * * * cd /full/path/to/appointment_monitor && /full/path/to/.venv/bin/python monitor.py >> monitor.log 2>&1
```

The cron job needs the SMTP env vars. Either source them in a wrapper script,
or put them directly in the crontab above the schedule line:

```cron
SMTP_USER=your.gmail.address@gmail.com
SMTP_PASS=xxxxxxxxxxxxxxxx
TO_EMAIL=annie.liang94@gmail.com
```

Alternatively, run it as a long-lived loop instead of via cron:

```bash
python monitor.py --interval 600
```

## Options

| Flag | Meaning |
| --- | --- |
| `--cutoff 2026-08-01` | Only alert on dates strictly before this. |
| `--interval 600` | Loop forever, polling every N seconds. Omit for one-shot (cron). |
| `--state-file path` | Where to remember already-alerted dates. |
| `--debug-dir path` | Save the raw HTML of each month for inspection. |
| `--dry-run` | Don't send email; print what would be sent. |
| `-v` | Verbose (DEBUG-level) logging. |

## Notes

- SuperSaas may rate-limit or block scrapers; the script uses a browser
  User-Agent and waits 1s between month fetches. Don't poll faster than
  every few minutes.
- If SuperSaas ever switches the public schedule to a JS-rendered view, the
  `requests`/`BeautifulSoup` approach will stop seeing slots. Fix is to swap
  in Playwright — but check `debug_html/` first to confirm.
- The script never books anything; you still have to grab the slot manually
  via the link in the alert email.
