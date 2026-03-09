# Financial Advisor Weekly Digest

A Python script that runs automatically every Monday via GitHub Actions. It scrapes top discussions from **r/CFP** (Reddit) and **LinkedIn profiles** of specific financial advisors, summarizes them using an LLM (Groq), and emails a styled, categorized digest.

## What It Does

1. **Fetches Reddit posts** — top 50 posts from r/CFP for the past week, including top comments
2. **Fetches LinkedIn posts** — recent posts from a tracked list of advisor profiles via Apify (past 7 days only)
3. **Summarizes with Groq** — uses a map-reduce approach to chunk posts, extract key topics, then synthesize a final digest with category tags and inline citations
4. **Emails the digest** — sends a styled HTML email with colored category pills, superscript citation links, and a "Key Themes" summary section

## Example Output

Each topic in the email looks like:

> **PRACTICE MANAGEMENT** &nbsp; **CLIENT RELATIONS**
>
> **Finding New Clients**
> Advisors are sharing strategies for finding clients beyond personal networks, emphasizing elite service to drive referrals.<sup>3</sup><sup>7</sup>

Superscript numbers link directly to the original Reddit post or LinkedIn post.

## Setup

### 1. Clone the repo

```bash
git clone <your-repo-url>
cd Advisor-Topics
pip install -r requirements.txt
```

### 2. Create a `.env` file

```bash
cp .env.example .env
```

Fill in your credentials:

```env
GROQ_API_KEY=your_groq_api_key
APIFY_API_TOKEN=your_apify_token
EMAIL_FROM=you@gmail.com
EMAIL_PASSWORD=your_gmail_app_password
EMAIL_TO=recipient1@example.com,recipient2@example.com
```

### 3. Run locally

```bash
python advisor_digest.py
```

## Configuration

### LinkedIn profiles to track

Edit the `LINKEDIN_PROFILES` list at the top of `advisor_digest.py`:

```python
LINKEDIN_PROFILES = [
    "https://www.linkedin.com/in/charlesfailla/",
    # add more here
]
```

### Email recipients

Set `EMAIL_TO` to a comma-separated list of addresses in your `.env` or GitHub secret.

### Data limits

These can be tuned in `main()`:

| Parameter | Default | Description |
|---|---|---|
| `post_limit` | 50 | Reddit posts fetched |
| `comments_per_post` | 50 | Top comments per post |
| LinkedIn posts | all | All posts from the past 7 days per profile |

## GitHub Actions (Automated Weekly Run)

The workflow runs every **Monday at 9am ET** (1pm UTC). To enable it:

1. Push this repo to GitHub
2. Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Description |
|---|---|
| `GROQ_API_KEY` | From [console.groq.com](https://console.groq.com) |
| `APIFY_API_TOKEN` | From [console.apify.com](https://console.apify.com) |
| `EMAIL_FROM` | Gmail address to send from |
| `EMAIL_PASSWORD` | Gmail [App Password](https://support.google.com/accounts/answer/185833) (not your regular password) |
| `EMAIL_TO` | Comma-separated recipient addresses |

You can also trigger it manually from the **Actions** tab → **Weekly CFP Advisor Digest** → **Run workflow**.

## Dependencies

| Package | Purpose |
|---|---|
| `groq` | LLM summarization (Llama 3.3 70B with model fallbacks) |
| `apify-client` | LinkedIn post scraping via `harvestapi/linkedin-profile-posts` |
| `requests` | Reddit public JSON API |
| `python-dotenv` | Local `.env` loading |

## Notes

- **Groq free tier**: 100k tokens/day limit. The script uses model fallbacks (`llama-3.3-70b-versatile` → `llama-4-maverick` → `qwen3-32b` → etc.) if rate limits are hit.
- **Apify**: The LinkedIn actor costs ~$2/1k posts. With `postedLimit: "week"`, only posts from the past 7 days are fetched.
- **Gmail**: Requires a 2FA-enabled account and an [App Password](https://support.google.com/accounts/answer/185833) — your regular Gmail password won't work.
