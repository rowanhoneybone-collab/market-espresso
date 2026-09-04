# Market Espresso — GitHub Pages Edition

This version uses **GitHub Pages + GitHub Actions**. Vercel is not required.

## What happens automatically

1. GitHub Pages hosts the newspaper.
2. GitHub Actions runs on a schedule.
3. The Action securely reads your `FINNHUB_API_KEY` repository secret.
4. It fetches prices and headlines.
5. It creates `data/edition.json`.
6. GitHub Pages republishes the newest edition.

Your Finnhub key is never placed in the webpage.

## One-time setup

### 1. Create a GitHub repository
Suggested name: `market-espresso`

Upload the **contents** of this package to the repository root. `index.html` should be visible at the top level.

### 2. Add the Finnhub API key as a GitHub secret
In your repository go to:

**Settings → Secrets and variables → Actions → New repository secret**

Name:

`FINNHUB_API_KEY`

Value:

Paste your Finnhub API token.

### 3. Turn on GitHub Pages
Go to:

**Settings → Pages**

Under **Build and deployment** set:

**Source: GitHub Actions**

### 4. Run the first edition
Go to:

**Actions → Brew Market Espresso → Run workflow → Run workflow**

When the workflow finishes, the deployment step provides the GitHub Pages URL.

It will generally look like:

`https://YOUR-USERNAME.github.io/market-espresso/`

Bookmark that URL.

## Updating

The Action is scheduled to refresh every 30 minutes on weekdays during the broad U.S. market-day window and once on weekends.

The **Refresh Latest Edition** button simply reloads the newest edition that GitHub has published. It does not expose or call your Finnhub API key from the browser.

## Manual refresh

At any time:

**Actions → Brew Market Espresso → Run workflow**

Then refresh the newspaper after the workflow finishes.
