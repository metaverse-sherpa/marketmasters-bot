#!/usr/bin/env bash
set -euo pipefail
# Ensure GitHub repository secrets exist by reading values from .env and
# creating them via the `gh` CLI if they are not present yet.
# Usage: ./scripts/ensure_github_secrets.sh [OWNER/REPO]

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI not found. Install and authenticate: https://cli.github.com/"
  exit 1
fi

repo=${1:-}
if [ -z "$repo" ]; then
  origin=$(git config --get remote.origin.url || true)
  if [ -z "$origin" ]; then
    echo "Repository not specified and git remote origin not found."
    echo "Usage: $0 OWNER/REPO" >&2
    exit 1
  fi
  # parse origin URL to owner/repo
  if [[ "$origin" =~ ^git@github.com:([^/]+/[^/]+)(\.git)?$ ]]; then
    repo="${BASH_REMATCH[1]}"
  else
    repo=$(echo "$origin" | sed -E 's#https?://[^/]+/([^/]+/[^/]+)(\.git)?#\1#')
  fi
fi

if [ -z "$repo" ]; then
  echo "Could not determine repository. Specify OWNER/REPO as argument." >&2
  exit 1
fi

# Strip trailing .git if present (user may pass or origin may include it)
repo="${repo%.git}"

if [ ! -f .env ]; then
  echo ".env not found in current directory; create it from .env.template and fill values." >&2
  exit 1
fi

get_env() {
  # Return the unquoted value of KEY from .env (first match)
  local key="$1"
  awk -F= -v k="$key" '$0 ~ "^"k"=" {sub(/^[^=]+=\/?/,""); print substr($0, index($0,$2)) ; exit}' .env 2>/dev/null || true
}

required=(MARKETMASTERS_API_KEY ALPACA_KEY ALPACA_SECRET)
optional=(ALPACA_BASE_URL MARKETMASTERS_URL MARKETMASTERS_PARAMS ALPCACA_PERCENTAGE_PER_TRADE BREAKOUT_LIMIT_BUFFER TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID ALERT_GITHUB_USERNAME)

echo "Using repository: $repo"

existing=$(gh secret list --repo "$repo" --limit 1000 2>/dev/null || true)

create_secret_if_missing(){
  local name="$1"
  local value="$2"
  if [ -z "$value" ]; then
    echo "  - Skipping $name (no value in .env)"
    return
  fi

  if echo "$existing" | awk '{print $1}' | grep -xq "$name"; then
    echo "  - Secret $name already exists in $repo; skipping"
  else
    echo "  - Creating secret $name..."
    # Use gh to set secret; --body is safe (doesn't leak to logs)
    gh secret set "$name" --body "$value" --repo "$repo"
    echo "    -> created"
  fi
}

echo "Checking required secrets..."
for k in "${required[@]}"; do
  v=$(get_env "$k")
  create_secret_if_missing "$k" "$v"
done

echo "Checking optional secrets..."
for k in "${optional[@]}"; do
  v=$(get_env "$k")
  create_secret_if_missing "$k" "$v"
done

echo "Done. Review repository secrets in GitHub Settings → Secrets and variables → Actions." 
