import requests
from bs4 import BeautifulSoup
import re
import os
from datetime import datetime, timezone, timedelta
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from tenacity import retry, stop_after_attempt, wait_exponential

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("anime_news_bot.log"),
        logging.StreamHandler(),
    ],
)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Fetch from environment variables
CHAT_ID = os.getenv("CHAT_ID")  # Fetch from environment variables
POSTED_TITLES_FILE = "posted_titles.json"
BASE_URL = "https://www.animenewsnetwork.com"
DEBUG_MODE = False  # Set to True to disable date filtering for debugging

# Ensure posted titles file exists
if not os.path.exists(POSTED_TITLES_FILE):
    with open(POSTED_TITLES_FILE, "w", encoding="utf-8") as file:
        json.dump([], file)

# Create a session for HTTP requests to reuse connections
session = requests.Session()

def escape_markdown(text):
    """Escape special characters for Markdown formatting in Telegram."""
    return re.sub(r"([_*[\]()~`>#\+\-=|{}.!\\])", r"\\\1", text or "")

def load_posted_titles():
    """Load previously posted article titles from a JSON file."""
    try:
        with open(POSTED_TITLES_FILE, "r", encoding="utf-8") as file:
            return set(json.load(file))
    except Exception as e:
        logging.error(f"Error loading posted titles: {e}")
        return set()

def save_posted_title(title):
    """Save newly posted article titles."""
    try:
        titles = load_posted_titles()
        titles.add(title)
        with open(POSTED_TITLES_FILE, "w", encoding="utf-8") as file:
            json.dump(list(titles), file)
    except Exception as e:
        logging.error(f"Error saving posted title: {e}")

def normalize_date(date_text):
    """Normalize ANN's article date to a comparable format (UTC timezone)."""
    try:
        logging.info(f"Raw date text: {date_text}")

        # ANN usually formats dates as "Mar 16, 06:11"
        date_obj = datetime.strptime(date_text, "%b %d, %H:%M")
        
        # Attach the current year and convert to UTC timezone
        now = datetime.now(timezone.utc)
        date_obj = date_obj.replace(year=now.year, tzinfo=timezone.utc)
        
        logging.info(f"Normalized date: {date_obj}")
        return date_obj
    except Exception as e:
        logging.error(f"Error normalizing date: {e}")
        return None

def fetch_anime_news():
    """Fetch the latest anime news and filter today's articles."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    logging.info(f"Fetching news from ANN for today: {today_start} - {today_end}")

    try:
        response = session.get(BASE_URL, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        articles = []
        for article in soup.find_all("div", class_="herald box news t-news"):
            title_tag = article.find("h3")
            date_tag = article.find("time")

            if not title_tag or not date_tag:
                continue

            title = title_tag.get_text(strip=True)
            date_text = date_tag.get_text(strip=True)

            # Normalize the date and filter today's articles
            normalized_date = normalize_date(date_text)
            if not normalized_date or (not DEBUG_MODE and not (today_start <= normalized_date <= today_end)):
                logging.info(f"Skipping old article: {title} ({date_text})")
                continue

            link = title_tag.find("a")
            article_url = f"{BASE_URL}{link['href']}" if link and link.has_attr("href") else None

            articles.append({"title": title, "url": article_url, "summary": "Fetching summary..."})

        logging.info(f"Total articles found: {len(articles)}")
        return articles

    except requests.RequestException as e:
        logging.error(f"Fetch error: {e}")
        return []

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_article_details(article):
    """Fetch summary from individual article pages."""
    if not article["url"]:
        return article  # Skip if no URL

    try:
        response = session.get(article["url"], timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        content_div = soup.find("div", class_="meat") or soup.find("div", class_="content")
        if content_div:
            first_paragraph = content_div.find("p")
            if first_paragraph:
                article["summary"] = first_paragraph.get_text(strip=True)[:200] + "..."

        return article

    except requests.RequestException as e:
        logging.error(f"Error fetching article: {e}")
        return article

def fetch_selected_articles(articles):
    """Fetch details for articles that haven't been posted yet."""
    posted_titles = load_posted_titles()
    articles_to_fetch = [a for a in articles if a["title"] not in posted_titles]

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(fetch_article_details, articles_to_fetch))

    return results

def send_to_telegram(article):
    """Send article details to Telegram."""
    title, summary = article["title"], article["summary"]
    safe_title = escape_markdown(title)
    safe_summary = escape_markdown(summary)

    caption = f"✨ *{safe_title}* ✨\n\n📖 {safe_summary}\n\n🌟Powered By: `@TheAnimeTimes_acn`"

    params = {
        "chat_id": CHAT_ID,
        "caption": caption,
        "parse_mode": "MarkdownV2",
    }

    try:
        response = session.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"text": caption, **params},
            timeout=5,
        )
        response.raise_for_status()
        save_posted_title(title)
        logging.info(f"Successfully posted: {title}")
    except requests.RequestException as e:
        logging.error(f"Telegram post failed: {e}")

def run_once():
    """Main execution: fetches, processes, and posts news."""
    logging.info("Starting Anime News Bot...")
    
    articles = fetch_anime_news()
    if not articles:
        logging.info("No new articles found.")
        return

    detailed_articles = fetch_selected_articles(articles)
    
    for article in detailed_articles:
        if article["title"] not in load_posted_titles():
            send_to_telegram(article)

if __name__ == "__main__":
    run_once()
