import requests
from bs4 import BeautifulSoup
import time
import re
import os
import json
import logging
import pytz
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from tenacity import retry, stop_after_attempt, wait_exponential

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("anime_news_bot.log"), logging.StreamHandler()],
)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Fetch from environment variables
CHAT_ID = os.getenv("CHAT_ID")  # Fetch from environment variables
POSTED_TITLES_FILE = "posted_titles.json"
BASE_URL = "https://www.animenewsnetwork.com"
DEBUG_MODE = False  # Set True to test without date filter
TEST_MODE = False   # Set True to log messages without posting to Telegram
RATE_LIMIT_DELAY = 2  # Delay between Telegram posts to avoid rate limiting (in seconds)

if not BOT_TOKEN or not CHAT_ID:
    logging.error("BOT_TOKEN or CHAT_ID is missing. Check environment variables.")
    exit(1)

# Time Zone Handling
utc_tz = pytz.utc
local_tz = pytz.timezone("Asia/Kolkata")  # Change if needed
today_local = datetime.now(local_tz).date()

session = requests.Session()

def escape_markdown(text):
    """Escapes Telegram MarkdownV2 special characters."""
    if not text or not isinstance(text, str):
        return ""
    # Telegram MarkdownV2 special characters that need escaping
    special_chars = r"([_*[\]()~`>#\+\-=|{}.!\\])"
    return re.sub(special_chars, r"\\\1", text)

def load_posted_titles():
    """Loads posted titles from file."""
    try:
        if os.path.exists(POSTED_TITLES_FILE):
            with open(POSTED_TITLES_FILE, "r", encoding="utf-8") as file:
                return set(json.load(file))
        return set()
    except (json.JSONDecodeError, FileNotFoundError):
        logging.error("Error decoding posted_titles.json. Resetting file.")
        return set()

def save_posted_title(title):
    """Saves a title to prevent duplicate posting."""
    try:
        titles = load_posted_titles()
        titles.add(title)
        with open(POSTED_TITLES_FILE, "w", encoding="utf-8") as file:
            json.dump(list(titles), file)
    except Exception as e:
        logging.error(f"Error saving posted title: {e}")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_anime_news():
    """Fetches latest anime news from ANN."""
    try:
        response = session.get(BASE_URL, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        news_list = []
        all_articles = soup.find_all("div", class_="herald box news t-news")
        logging.info(f"Total articles found: {len(all_articles)}")

        for article in all_articles:
            title_tag = article.find("h3")
            date_tag = article.find("time")
            
            if not title_tag or not date_tag or "datetime" not in date_tag.attrs:
                continue

            title = title_tag.get_text(strip=True)
            date_str = date_tag["datetime"]
            news_date = datetime.fromisoformat(date_str).astimezone(local_tz).date()

            if DEBUG_MODE or news_date == today_local:
                link = title_tag.find("a")
                article_url = f"{BASE_URL}{link['href']}" if link else None
                news_list.append({"title": title, "article_url": article_url, "article": article})
                logging.info(f"✅ Found today's news: {title}")
            else:
                logging.info(f"⏩ Skipping (not today's news): {title}")

        logging.info(f"Filtered today's articles: {len(news_list)}")
        return news_list

    except requests.RequestException as e:
        logging.error(f"Fetch error: {e}")
        return []

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_article_details(article_url, article):
    """Fetches article image and summary."""
    image_url = None
    summary = "No summary available."

    # Fetch image URL
    thumbnail = article.find("div", class_="thumbnail")
    if thumbnail and thumbnail.get("data-src"):
        image_url = thumbnail["data-src"]
        if not image_url.startswith("http"):
            image_url = f"{BASE_URL}{image_url}"
        # Validate the image URL
        try:
            image_response = session.head(image_url, timeout=3)
            if image_response.status_code != 200 or "image" not in image_response.headers.get("Content-Type", ""):
                logging.warning(f"Invalid image URL: {image_url}")
                image_url = None
        except requests.RequestException as e:
            logging.warning(f"Failed to validate image URL {image_url}: {e}")
            image_url = None

    # Fetch summary
    if article_url:
        try:
            article_response = session.get(article_url, timeout=5)
            article_response.raise_for_status()
            article_soup = BeautifulSoup(article_response.text, "html.parser")
            content_div = article_soup.find("div", class_="meat") or article_soup.find("div", class_="content")
            if content_div:
                first_paragraph = content_div.find("p")
                if first_paragraph:
                    summary = first_paragraph.get_text(strip=True)[:200] + "..."
        except requests.RequestException as e:
            logging.error(f"Error fetching article content: {e}")

    return {"image": image_url, "summary": summary}

def send_to_telegram(title, image_url, summary):
    """Posts news to Telegram with fallback to plain text if Markdown fails."""
    # First attempt with MarkdownV2
    safe_title = escape_markdown(title)
    safe_summary = escape_markdown(summary)
    caption = f"*{safe_title}*\n\n\"{safe_summary}\"\n\n🍁 | @TheAnimeTimes_acn"
    params = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "MarkdownV2"}

    try:
        logging.info(f"Attempting to post: {title} with image: {image_url}")

        if TEST_MODE:
            logging.info(f"TEST MODE: Would post to Telegram with caption: {caption}")
            return

        if image_url:
            response = session.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={"photo": image_url, **params},
                timeout=5
            )
        else:
            response = session.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={"text": caption, **params},
                timeout=5
            )
        
        response.raise_for_status()
        save_posted_title(title)
        logging.info(f"✅ Posted: {title}")

    except requests.RequestException as e:
        # Log the error with full response
        if hasattr(e, "response") and e.response is not None:
            error_msg = e.response.text
            logging.error(f"Telegram post failed (MarkdownV2): {e} - Response: {error_msg}")
        else:
            logging.error(f"Telegram post failed (MarkdownV2): {e}")

        # Fallback to plain text
        logging.info("Falling back to plain text due to MarkdownV2 failure...")
        caption = f"⚡{title}⚡\n\n\"{summary}\"\n\n🍁 | @TheAnimeTimes_acn"
        params = {"chat_id": CHAT_ID, "caption": caption}  # No parse_mode

        try:
            if TEST_MODE:
                logging.info(f"TEST MODE: Would post to Telegram with plain text caption: {caption}")
                return

            if image_url:
                response = session.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                    data={"photo": image_url, **params},
                    timeout=5
                )
            else:
                response = session.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    data={"text": caption, **params},
                    timeout=5
                )
            
            response.raise_for_status()
            save_posted_title(title)
            logging.info(f"✅ Posted (plain text): {title}")
        except requests.RequestException as e:
            if hasattr(e, "response") and e.response is not None:
                logging.error(f"Telegram post failed (plain text): {e} - Response: {e.response.text}")
            else:
                logging.error(f"Telegram post failed (plain text): {e}")
            raise  # Re-raise the exception to stop processing if both attempts fail

def run_once():
    """Runs the bot once to fetch and post today’s news."""
    logging.info("Fetching latest anime news...")
    news_list = fetch_anime_news()
    if not news_list:
        logging.info("No new articles to post.")
        return

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(fetch_article_details, news["article_url"], news["article"]): news for news in news_list}
        for future in futures:
            try:
                result = future.result(timeout=10)
                news = futures[future]
                send_to_telegram(news["title"], result["image"], result["summary"])
                # Add delay to avoid Telegram rate limiting
                time.sleep(RATE_LIMIT_DELAY)
            except Exception as e:
                logging.error(f"Error processing article: {e}")

if __name__ == "__main__":
    if not os.path.exists(POSTED_TITLES_FILE):
        with open(POSTED_TITLES_FILE, "w", encoding="utf-8") as file:
            json.dump([], file)
    
    run_once()
