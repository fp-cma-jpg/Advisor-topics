"""
Financial Advisor Weekly Digest
Fetches top discussions from r/CFP (Reddit) and specific LinkedIn profiles (Apify),
summarizes with Groq (Llama 3.3 70B), and emails a categorized digest.
"""

import os
import re
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
from apify_client import ApifyClient
from groq import Groq, RateLimitError, BadRequestError, APIStatusError

# Load .env file when running locally (no-op in GitHub Actions where secrets are env vars)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ---------------------------------------------------------------------------
# LinkedIn profiles to track — add more URLs to this list any time
# ---------------------------------------------------------------------------
LINKEDIN_PROFILES = [
    "https://www.linkedin.com/in/kevin-thompson-cfp%C2%AE%EF%B8%8F-ricp%C2%AE%EF%B8%8F-ea-74964428/",
    "https://www.linkedin.com/in/charlesfailla/",
    "https://www.linkedin.com/in/philip-waxelbaum-4849b5a/",
    "https://www.linkedin.com/in/bhenrymoreland/",
    "https://www.linkedin.com/in/cfpjudd/"
]


# ---------------------------------------------------------------------------
# Config — all values come from environment variables / GitHub secrets
# ---------------------------------------------------------------------------
GROQ_API_KEY            = os.environ["GROQ_API_KEY"]
APIFY_API_TOKEN         = os.environ.get("APIFY_API_TOKEN", "")
LINKEDIN_SESSION_COOKIE = os.environ.get("LINKEDIN_SESSION_COOKIE", "")
EMAIL_FROM              = os.environ["EMAIL_FROM"]
EMAIL_PASSWORD          = os.environ["EMAIL_PASSWORD"]
EMAIL_TO                = os.environ["EMAIL_TO"]   # comma-separated
SMTP_HOST               = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT               = int(os.environ.get("SMTP_PORT", "587"))

# ---------------------------------------------------------------------------
# Reddit — via Apify trudax/reddit-scraper-lite
# Avoids direct reddit.com requests which are blocked in GitHub Actions.
# Requires: APIFY_API_TOKEN
# ---------------------------------------------------------------------------
def collect_reddit(subreddit: str = "CFP", post_limit: int = 50, comments_per_post: int = 50) -> list[dict]:
    if not APIFY_API_TOKEN:
        print("  APIFY_API_TOKEN not set — skipping Reddit.")
        return []

    client = ApifyClient(APIFY_API_TOKEN)
    one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)

    run_input = {
        "startUrls":    [{"url": f"https://www.reddit.com/r/{subreddit}/top/?t=week"}],
        "maxPostCount": post_limit,
        "maxComments":  comments_per_post,
        "time":         "week",
        "proxy":        {"useApifyProxy": True},
    }

    print(f"  Running Apify actor for r/{subreddit}...")
    try:
        run = client.actor("trudax/reddit-scraper-lite").call(
            run_input=run_input,
            timeout_secs=180,
        )
    except Exception as e:
        print(f"  Apify Reddit actor failed: {e}")
        return []

    posts: dict[str, dict] = {}
    comments_by_post: dict[str, list[str]] = {}

    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        data_type = item.get("dataType")

        if data_type == "post":
            post_id = item.get("id", "")
            created = item.get("createdAt", "")
            try:
                post_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if post_dt < one_week_ago:
                    continue
            except (ValueError, AttributeError):
                pass
            posts[post_id] = {
                "title":        (item.get("title") or "").strip(),
                "text":         (item.get("body") or "")[:2000].strip(),
                "score":        item.get("upVotes", 0),
                "num_comments": item.get("numberOfComments", 0),
                "url":          item.get("url", ""),
                "source":       f"Reddit r/{subreddit}",
            }

        elif data_type == "comment":
            parent_id = item.get("parentId", "")
            body = (item.get("body") or "").strip()
            if body and body != "[deleted]":
                comments_by_post.setdefault(parent_id, []).append(body[:500])

    results = []
    for post_id, post in posts.items():
        top_comments = comments_by_post.get(post_id, [])[:comments_per_post]
        if top_comments:
            post["text"] = (post["text"] + "\nTop comments: " + " | ".join(top_comments)).strip()
        results.append(post)

    print(f"  Collected {len(results)} posts from r/{subreddit}")
    return results


# ---------------------------------------------------------------------------
# LinkedIn — via Apify profile post scraper
# Actor: harvestapi/linkedin-profile-posts
# No cookies or LinkedIn account required.
# Requires: APIFY_API_TOKEN
# ---------------------------------------------------------------------------
def collect_linkedin() -> list[dict]:
    if not APIFY_API_TOKEN:
        print("  APIFY_API_TOKEN not set — skipping LinkedIn.")
        return []

    client = ApifyClient(APIFY_API_TOKEN)

    # Safety-net: also filter client-side in case actor returns anything outside the window
    one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)

    # maxPosts omitted (or 0) so the actor returns all posts within the postedLimit window
    run_input = {
        "targetUrls":  LINKEDIN_PROFILES,
        "postedLimit": "week",
    }

    print(f"  Running Apify actor for {len(LINKEDIN_PROFILES)} profiles...")
    try:
        run = client.actor("harvestapi/linkedin-profile-posts").call(
            run_input=run_input,
            timeout_secs=120,
        )
    except Exception as e:
        print(f"  Apify actor failed: {e}")
        return []

    results = []
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        text   = (item.get("content") or "").strip()
        url    = item.get("linkedinUrl") or item.get("socialContent", {}).get("shareUrl") or ""
        author = item.get("author", {}).get("name", "Unknown") if isinstance(item.get("author"), dict) else "Unknown"

        # postedAt is a nested object: {"timestamp": <ms>, "date": "2026-03-09T..."}
        posted_obj = item.get("postedAt") or {}
        posted_iso = posted_obj.get("date") or "" if isinstance(posted_obj, dict) else ""

        # Skip posts older than one week — skip if no parseable date
        if not posted_iso:
            continue
        try:
            post_dt = datetime.fromisoformat(posted_iso.replace("Z", "+00:00"))
            if post_dt < one_week_ago:
                continue
        except (ValueError, AttributeError):
            continue

        if not text:
            continue

        results.append({
            "title":  text[:100],
            "text":   text[:2000],
            "url":    url,
            "source": f"LinkedIn ({author})",
        })

    print(f"  Collected {len(results)} LinkedIn posts from the past week")
    return results


# ---------------------------------------------------------------------------
# Gemini summarization
# ---------------------------------------------------------------------------
CATEGORIES = [
    "Tax", "Retirement", "Investments", "Compliance", "Fintech",
    "Practice Management", "Insurance", "Estate Planning",
    "Client Relations", "Economics", "Regulation", "Education",
]

PROMPT_TEMPLATE = """You are an analyst summarizing what Certified Financial Planners (CFPs) and financial advisors are actively discussing this week.

Below are bullet-point summaries extracted from posts and discussions on Reddit (r/CFP) and LinkedIn. Each bullet includes [n] citation numbers referencing the original posts.

---
{content}
---

Identify the TOP 10 most discussed or important topics. At least 2-3 of the 10 topics MUST come primarily from LinkedIn posts — do not let Reddit dominate just because it has more posts. Weight LinkedIn topics by importance, not volume.

Format each topic EXACTLY like this — use a number, never a bullet. The square brackets around the category tags are REQUIRED — do not omit them:

1. **[Category, Category] Topic Title**
   - 2-3 sentence summary. You MUST preserve [n] citation numbers inline in square brackets where relevant, e.g. "Advisors are debating X [1][4]." Always use square brackets — never bare numbers alone.

The opening line must follow this pattern exactly: number, period, space, **, [categories], space, title, **
Example: 1. **[Tax, Retirement] SECURE 2.0 RMD Age Change**

Be specific in both the title and summary. If advisors are discussing a specific law, tax rule, software tool, custodian, regulation, or named product — use its exact name (e.g. "SECURE 2.0", "Orion", "RMD age 73", "Form ADV"). Do not summarize into vague generalities.

Valid category tags: {categories}
Use one or more tags per topic, comma-separated inside the brackets.

After the list, add a short section:
**Key Themes This Week**
2-4 sentences on the overarching story across all topics.

Be direct and specific. Avoid filler phrases like "financial advisors are discussing...". Just state what the issue is and what they're saying about it."""


# Models tried in order — falls back to the next if rate-limited or decommissioned
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "qwen/qwen3-32b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
]
CHUNK_SIZE = 3           # posts per intermediate call (keeps requests under 6k TPM fallback limits)
CHUNK_DELAY_SECS = 22   # pause between calls to stay within 12k TPM free-tier limit


def _fix_bare_citations(text: str) -> str:
    """Convert bare citation numbers the LLM emits without brackets into [n] format.

    Catches patterns like "...some text 3." or "...text 1, 2, 3."
    Skips list numbering like "1. Topic title" by requiring a preceding word char.
    """
    return re.sub(
        r'(?<=\w) (\d+(?:,\s*\d+)*)(?=[.,;]?\s*(?:$|\n|\[))',
        lambda m: " " + "".join(f"[{n.strip()}]" for n in m.group(1).split(",")),
        text,
    )


def _build_content(chunk: list[dict], start_num: int = 1) -> str:
    parts = []
    for i, p in enumerate(chunk):
        num = start_num + i
        part = f"[{num}] [{p['source']}]\nTitle: {p['title']}"
        if p.get("text"):
            part += f"\n{p['text']}"
        parts.append(part)
    return "\n\n---\n\n".join(parts)


def _groq_complete(client: Groq, messages: list[dict], max_tokens: int) -> str:
    """Call Groq chat completions, falling back through GROQ_MODELS on rate-limit errors."""
    for model in GROQ_MODELS:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content.strip()
        except (RateLimitError, BadRequestError, APIStatusError) as e:
            print(f"  Skipping {model}: {e}. Trying next model...")
    raise RuntimeError("All Groq models are rate-limited. Try again later.")


def summarize_with_groq(posts: list[dict]) -> str:
    client = Groq(api_key=GROQ_API_KEY)
    chunks = [posts[i:i + CHUNK_SIZE] for i in range(0, len(posts), CHUNK_SIZE)]

    # --- Pass 1: extract bullet-point topics from each chunk ---
    intermediate_summaries = []
    start_num = 1
    for i, chunk in enumerate(chunks):
        if i > 0:
            print(f"  Waiting {CHUNK_DELAY_SECS}s between requests...")
            time.sleep(CHUNK_DELAY_SECS)

        chunk_prompt = (
            "Below are numbered posts from financial advisor communities. "
            "Extract the key topics/issues as concise bullet points. "
            "IMPORTANT: After each bullet you MUST cite the source post number(s) using square brackets, "
            "e.g. [1] or [1][3]. Always use square brackets — never bare numbers. Be specific.\n\n"
            + _build_content(chunk, start_num=start_num)
        )
        start_num += len(chunk)
        text = _groq_complete(client, [{"role": "user", "content": chunk_prompt}], max_tokens=600)
        intermediate_summaries.append(text)
        print(f"  Chunk {i + 1}/{len(chunks)} summarized.")

    # --- Pass 2: synthesize into final structured digest ---
    if len(chunks) > 1:
        print(f"  Waiting {CHUNK_DELAY_SECS}s before final synthesis...")
        time.sleep(CHUNK_DELAY_SECS)

    combined = "\n\n".join(intermediate_summaries)
    final_prompt = PROMPT_TEMPLATE.format(
        content=combined,
        categories=", ".join(CATEGORIES),
    )
    text = _groq_complete(client, [{"role": "user", "content": final_prompt}], max_tokens=4096)
    return _fix_bare_citations(text)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
CATEGORY_COLORS = {
    "Tax":                 ("#dbeafe", "#1d4ed8"),
    "Retirement":          ("#dcfce7", "#15803d"),
    "Investments":         ("#ede9fe", "#6d28d9"),
    "Compliance":          ("#fee2e2", "#b91c1c"),
    "Fintech":             ("#ccfbf1", "#0f766e"),
    "Practice Management": ("#f3f4f6", "#374151"),
    "Insurance":           ("#fef9c3", "#854d0e"),
    "Estate Planning":     ("#d1fae5", "#065f46"),
    "Client Relations":    ("#fce7f3", "#9d174d"),
    "Economics":           ("#ffedd5", "#c2410c"),
    "Regulation":          ("#fee2e2", "#991b1b"),
    "Education":           ("#e0e7ff", "#3730a3"),
}
_DEFAULT_CAT_COLOR = ("#f3f4f6", "#374151")


def _cat_pill(cat: str) -> str:
    bg, fg = CATEGORY_COLORS.get(cat.strip(), _DEFAULT_CAT_COLOR)
    return (
        f'<span style="display:inline-block;padding:2px 9px;margin:0 4px 4px 0;'
        f'border-radius:12px;font-size:11px;font-weight:700;letter-spacing:0.5px;'
        f'font-family:sans-serif;background:{bg};color:{fg};">{cat.strip().upper()}</span>'
    )


def _add_citations(text: str, posts: list[dict]) -> str:
    """Convert [n] inline references to superscript links pointing directly to the source post.

    Citations that appear immediately before a period are moved after it so the
    superscript sits outside the sentence: "text.[1]" rather than "text[1]."
    """
    # Move citation groups that sit right before a period: "text [1][4]." → "text.[1][4]"
    text = re.sub(r'\s*((?:\[\d+\])+)\.', lambda m: '.' + m.group(1), text)

    def _replace(m: re.Match) -> str:
        n = int(m.group(1))
        url = posts[n - 1]["url"] if 1 <= n <= len(posts) else "#"
        return (
            f'<sup><a href="{url}" target="_blank" '
            f'style="color:#1a3a5c;text-decoration:none;font-size:10px;">'
            f'{n}</a></sup>'
        )
    return re.sub(r'\[(\d+)\]', _replace, text)


def build_html_email(summary: str, date_str: str, posts: list[dict]) -> str:
    # Primary: 1. **[Category, Category] Title**
    topic_re = re.compile(r'^(?:\*\s+|\d+\.\s*)?\*\*\[([^\]]+)\]\s*(.+?)\*\*\s*$')
    # Fallback: 1. **Category, Category** Title  (LLM sometimes omits square brackets)
    topic_re_alt = re.compile(r'^(?:\*\s+|\d+\.\s*)?\*\*([A-Za-z ,]+)\*\*\s+(.+)$')
    lines = summary.split("\n")
    html_lines = []

    for line in lines:
        stripped = line.strip()
        topic_match = topic_re.match(stripped) or topic_re_alt.match(stripped)

        if topic_match:
            cats_raw, title = topic_match.group(1), topic_match.group(2)
            pills = "".join(_cat_pill(c) for c in cats_raw.split(","))
            html_lines.append(
                f'<div style="margin-top:28px;">'
                f'<div style="margin-bottom:4px;">{pills}</div>'
                f'<div style="font-size:16px;font-weight:700;color:#1a3a5c;">'
                f'{_add_citations(title, posts)}</div></div>'
            )
        elif stripped.startswith("**") and stripped.endswith("**"):
            header = stripped.strip("*").strip()
            html_lines.append(
                f'<h3 style="margin-top:36px;color:#1a3a5c;'
                f'border-top:1px solid #eee;padding-top:18px;">{header}</h3>'
            )
        elif stripped.startswith("- "):
            content = _add_citations(stripped[2:], posts)
            html_lines.append(
                f'<p style="margin:6px 0 0 0;line-height:1.75;color:#333;">{content}</p>'
            )
        elif stripped == "---":
            html_lines.append("<hr>")
        elif stripped:
            html_lines.append(
                f'<p style="line-height:1.75;color:#333;">{_add_citations(stripped, posts)}</p>'
            )

    sources_html = ""

    linkedin_names = " &bull; ".join(
        url.rstrip("/").split("/in/")[-1].replace("%C2%AE", "®").replace("%EF%B8%8F", "")
        for url in LINKEDIN_PROFILES
    )

    return (
        f'<!DOCTYPE html><html><head></head>'
        f'<body style="font-family:Georgia,serif;max-width:700px;margin:auto;'
        f'color:#222;padding:24px;">'
        f'<h2 style="color:#1a3a5c;border-bottom:2px solid #1a3a5c;padding-bottom:8px;">'
        f'Financial Advisor Weekly Digest</h2>'
        f'<p style="color:#888;font-size:13px;font-family:sans-serif;">'
        f'Top issues discussed by advisors on Reddit &amp; LinkedIn &mdash; week of {date_str}</p>'
        f'<hr>'
        f'{"".join(html_lines)}'
        f'{sources_html}'
        f'<div style="color:#aaa;font-size:12px;margin-top:40px;border-top:1px solid #eee;'
        f'padding-top:12px;font-family:sans-serif;">'
        f'Sources: r/CFP (Reddit) &bull; LinkedIn: {linkedin_names}<br>'
        f'Summarized by Groq (Llama 3.3 70B) &bull; Data via Apify'
        f'</div></body></html>'
    )


def send_email(summary: str, posts: list[dict]) -> None:
    recipients = [e.strip() for e in EMAIL_TO.split(",") if e.strip()]
    date_str = datetime.now().strftime("%B %d, %Y")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Advisor Weekly Digest — {date_str}"
    msg["From"]    = EMAIL_FROM
    msg["To"]      = ", ".join(recipients)

    msg.attach(MIMEText(summary, "plain"))
    msg.attach(MIMEText(build_html_email(summary, date_str, posts), "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, recipients, msg.as_string())

    print(f"Email sent to: {', '.join(recipients)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    print("=== Financial Advisor Weekly Digest ===")
    print(f"Run date: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}\n")

    TEST_MODE = False  # Set True for a quick low-cost test run (5 posts, no LinkedIn)

    print("Fetching Reddit r/CFP...")
    reddit_posts = collect_reddit(
        subreddit="CFP",
        post_limit=10 if TEST_MODE else 50,
        comments_per_post=3 if TEST_MODE else 50,
    )

    linkedin_posts = []
    if not TEST_MODE:
        print("\nFetching LinkedIn posts via Apify...")
        linkedin_posts = collect_linkedin()
    else:
        print("\nTEST_MODE: skipping LinkedIn.")

    all_posts = reddit_posts + linkedin_posts
    print(f"\nTotal items collected: {len(all_posts)}")

    if not all_posts:
        print("No content collected. Exiting without sending email.")
        return

    print("\n--- COLLECTED POSTS ---")
    for i, p in enumerate(all_posts, 1):
        print(f"\n[{i}] {p['source']}")
        print(f"    Title : {p['title'][:120]}")
        print(f"    Text  : {p['text'][:200].replace(chr(10), ' ')!r}")
        print(f"    URL   : {p['url']}")
    print("--- END OF POSTS ---\n")

    print("\nSummarizing with Groq...")
    summary = summarize_with_groq(all_posts)

    print("\n--- DIGEST PREVIEW ---")
    print(summary)
    print("----------------------\n")

    print("Sending email...")
    send_email(summary, all_posts)

    print("\nDone.")


if __name__ == "__main__":
    main()
