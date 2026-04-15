# ClaudeTrade — Trading Bot

This repository runs a trading bot that reads bullish chart patterns from MarketMasters and places  orders on Alpaca (paper trading). This README explains how to set up a local `.env` for development and how to deploy the bot on GitHub Actions as a scheduled job.

**Files to know**
- `trading_bot.py` — main bot script
- `.github/workflows/run-bot.yml` — GitHub Actions workflow (daily schedule + manual trigger)
- `requirements.txt` — Python dependencies
- `.env.template` — example environment variables file

## Prerequisites
- Python 3.11 (or compatible)
- `pip` and (optionally) a virtual environment tool
- `gh` CLI (optional, for adding secrets from terminal)

## Local setup (development)

1. Copy the template and populate secrets:

```bash
cp .env.template .env
# Edit .env with your values (API keys, etc.)
open .env  # or use your preferred editor
```

2. Required environment variables
- `MARKETMASTERS_API_KEY` — MarketMasters API key - You will need a Premium member account to get your API key.
- 'MARKETMASTERS_URL` - This is the URL to the API call you want to make. This bot was built and tested with the stocks chart pattern API. https://api.marketmasters.ai/v1/stocks/patterns
- `ALPACA_KEY` — Alpaca API key
- `ALPACA_SECRET` — Alpaca API secret
- `ALPACA_BASE_URL` — optional; defaults to `https://paper-api.alpaca.markets/v2` when not set

Example `.env` (do NOT commit this file):

```env
# MarketMasters
MARKETMASTERS_API_KEY=your_marketmasters_key
MARKETMASTERS_URL=https://api.marketmasters.ai/v1/stocks/patterns

# Alpaca
ALPACA_KEY=your_alpaca_key
ALPACA_SECRET=your_alpaca_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets/v2
```

3. Install dependencies (recommended in a venv):

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

4. Run the bot locally (dry run / smoke test):

```bash
python trading_bot.py
```

The bot checks for required environment secrets at startup and will exit with a helpful message if any are missing.

## Deploying to GitHub Actions (scheduled run)

## Publish this folder to GitHub (create the repository)

If this is a new project folder and you haven't created a remote GitHub repository yet, run one of the following workflows.

Create repo and push using the GitHub CLI (recommended):

```bash
# login once: gh auth login
gh repo create OWNER/REPO --public --source=. --remote=origin --push
# or create private repo:
# gh repo create OWNER/REPO --private --source=. --remote=origin --push
```

Manual git workflow (works without `gh`):

```bash
git init
git add .
git commit -m "Initial commit"
# Choose your default branch name (main or master) and set branch name accordingly:
git branch -M main
git remote add origin https://github.com/OWNER/REPO.git
git push -u origin main
```

Notes:
- Replace `OWNER/REPO` with your GitHub username/org and repository name.
- Ensure the workflow YAML files are on the repository default branch (e.g. `main`) so GitHub Actions registers them.
- After pushing, go to the repository → Settings → Actions to allow Actions if your org/repo restricts them.


1. The repository already contains a workflow at `.github/workflows/run-bot.yml` that runs daily and supports manual runs via `workflow_dispatch`.

2. Add repository secrets in GitHub (UI):

- Go to your repository → Settings → Secrets and variables → Actions → New repository secret
- Add the same keys as in `.env`: 'MARKETMASTERS_URL', `MARKETMASTERS_API_KEY`, `ALPACA_KEY`, `ALPACA_SECRET`, `ALPACA_BASE_URL` (optional)

Or use the GitHub CLI:

```bash
gh auth login
gh secret set MARKETMASTERS_API_KEY --body "${MARKETMASTERS_API_KEY}" --repo OWNER/REPO
gh secret set ALPACA_KEY --body "${ALPACA_KEY}" --repo OWNER/REPO
gh secret set ALPACA_SECRET --body "${ALPACA_SECRET}" --repo OWNER/REPO
gh secret set ALPACA_BASE_URL --body "${ALPACA_BASE_URL}" --repo OWNER/REPO
```

Replace `OWNER/REPO` with your GitHub owner and repository name.

3. Trigger the workflow manually for testing:

- From the web UI: Actions → select **Run trading bot daily** → Run workflow
- Or via CLI:

```bash
gh workflow run run-bot.yml --repo OWNER/REPO --ref master
gh run watch <run-id> --repo OWNER/REPO
```

4. View logs: open Actions → select the workflow run → expand the job and steps to view output. The bot prints helpful messages if secrets are missing.

### Run in GitHub Actions (manual run, dry-run and verification)

If you want to run the bot on-demand from GitHub (recommended for testing), follow these steps.

- From the web UI (manual):
	- Go to Actions → select **Run trading bot daily** → **Run workflow** → choose branch (e.g., `main`) → Run workflow.

- From the CLI (manual):

```bash
# Trigger the workflow (requires gh CLI authenticated)
gh workflow run run-bot.yml --repo OWNER/REPO --ref main
# Optionally watch the run
gh run watch --repo OWNER/REPO
```

- Dry-run (safe verification):
	- Add a repository secret `DRY_RUN=true` (or set a repository variable) and modify the workflow step to pass `DRY_RUN` into the job environment. When `DRY_RUN` is set, the bot will log intended orders instead of submitting them. This allows end-to-end verification without placing orders.

- Verify environment variables are present before running (quick check step):

```yaml
- name: Verify required secrets
	run: |
		echo "MARKETMASTERS_API_KEY=$MARKETMASTERS_API_KEY"
		echo "ALPACA_KEY=${ALPACA_KEY:+SET}"
		echo "ALPACA_SECRET=${ALPACA_SECRET:+SET}"
		echo "ALPACA_BASE_URL=$ALPACA_BASE_URL"
```

- Troubleshooting tips:
	- If the workflow fails at startup complaining about missing secrets, ensure the workflow step maps `secrets.*` into `env:` (see `.github/workflows/run-bot.yml`).
	- Confirm the workflow YAML is on the repository default branch (`main`/`master`) so Actions registers it.
	- Inspect the Actions logs for step output and error messages.


## Notes & safety
- The repository `.gitignore` excludes `*.json` and `.env`, so local exported secrets and runtime state (e.g. `traded_patterns.json`) should not be committed, but be careful not to commit `.env`.
- The bot places real orders on Alpaca when secrets are valid — use paper trading endpoints and paper account credentials.
- If you prefer limit orders at a slight buffer above breakout price, edit `BREAKOUT_LIMIT_BUFFER` in `trading_bot.py`.

## Troubleshooting
- If a workflow does not appear in Actions, ensure the workflow YAML is on the repository default branch (usually `master` or `main`) and that Actions are allowed in repository/org settings.
- If the bot exits at startup, check the printed list of missing environment variables and add them to `.env` (for local) or GitHub Secrets (for Actions).

---

If you want, I can commit and push this README for you and trigger a workflow run for testing.
