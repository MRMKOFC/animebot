import requests
from bs4 import BeautifulSoup
import time
import re
import os
import json
import logging
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

if not BOT_TOKEN or not CHAT_ID:
    logging.error("BOT_TOKEN or CHAT_ID is missing. Check environment variables.")
    exit(1)

# Create a session for HTTP requests
session = requests.Session()

def escape_markdown(text):
    """Escapes Telegram Markdown special characters."""
    if not text or not isinstance(text, str):
        return ""
    return re.sub(r"([_*[\]()~`>#\+\-=|{}.!\\])", r"\\\1", text)

def load_posted_titles():
    """Loads posted titles from file and logs them."""
    try:
        if os.path.exists(POSTED_TITLES_FILE):
            with open(POSTED_TITLES_FILE, "r", encoding="utf-8") as file:
                titles = set(json.load(file))
                logging.info(f"Loaded {len(titles)} posted titles.")
                return titles
        return set()
    except Exception as e:
        logging.error(f"Error loading posted titles: {e}")
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

def normalize_date(date_text):
    """Normalizes date text from ANN."""
    try:
        date_obj = datetime.strptime(date_text, "%b %d, %H:%M")
        date_obj = date_obj.replace(year=datetime.now().year)

        # Ensure no future dates
        if date_obj > datetime.now():
            date_obj = date_obj.replace(year=date_obj.year - 1)

        return date_obj
    except ValueError:
        logging.error(f"Unknown date format: {date_text}")
        return None

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_anime_news():
    """Fetches latest anime news from ANN."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    try:
        response = session.get(BASE_URL, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        news_list = []
        all_articles = soup.find_all("div", class_="herald box news t-news")
        logging.info(f"Total articles found: {len(all_articles)}")

        for article in all_articles:
            title_tag = article.find("h3")
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            date_tag = article.find("time")
            date_text = date_tag.get_text(strip=True) if date_tag else ""

            normalized_date = normalize_date(date_text)
            if not normalized_date:
                logging.warning(f"Skipping article due to date issue: {title}")
                continue

            if not DEBUG_MODE and normalized_date.date() != today.date():
                logging.info(f"Skipping (not today’s news): {title}")
                continue

            link = title_tag.find("a")
            article_url = f"{BASE_URL}{link['href']}" if link else None
            logging.info(f"Detected article: {title} (URL: {article_url})")

            news_list.append({"title": title, "article_url": article_url, "article": article})

        logging.info(f"Filtered today’s articles: {len(news_list)}")
        return news_list

    except requests.RequestException as e:
        logging.error(f"Fetch error: {e}")
        return []

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_article_details(article_url, article):
    """Fetches article image and summary."""
    image_url = None
    summary = "No summary available."

    # Extract thumbnail
    thumbnail = article.find("div", class_="thumbnail lazyload")
    if thumbnail and thumbnail.get("data-src"):
        img_url = thumbnail["data-src"]
        image_url = f"{BASE_URL}{img_url}" if not img_url.startswith("http") else img_url

    # Fetch article content
    if article_url:
        try:
            article_response = session.get(article_url, timeout=5)
            article_response.raise_for_status()
            article_soup = BeautifulSoup(article_response.text, "html.parser")
            content_div = article_soup.find("div", class_="meat") or article_soup.find("div", class_="content")
            if content_div:
                first_paragraph = content_div.find("p")
                if first_paragraph:
                    summary = first_paragraph.get_text(strip=True)[:200] + "..." if len(first_paragraph.text) > 200 else first_paragraph.text
        except requests.RequestException as e:
            logging.error(f"Error fetching article content: {e}")

    return {"image": image_url, "summary": summary}

def fetch_selected_articles(news_list):
    """Fetches article details concurrently."""
    posted_titles = load_posted_titles()
    articles_to_fetch = [news for news in news_list if news["title"] not in posted_titles]

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(fetch_article_details, news["article_url"], news["article"]): news for news in articles_to_fetch}
        
        for future in futures:
            try:
                result = future.result(timeout=10)
                news = futures[future]
                news["image"] = result["image"]
                news["summary"] = result["summary"]
            except Exception as e:
                logging.error(f"Error processing article: {e}")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def send_to_telegram(title, image_url, summary):
    """Posts news to Telegram."""
    safe_title = escape_markdown(title)
    safe_summary = escape_markdown(summary) if summary else "No summary available"
    
    caption = f"✨ *{safe_title}* ✨\n\n📖 {safe_summary}\n\n🌟 [Powered By: `@TheAnimeTimes_acn`] 🌟"

    params = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "MarkdownV2"}
    try:
        if image_url:
            response = session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data={"photo": image_url, **params}, timeout=5)
        else:
            response = session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"text": caption, **params}, timeout=5)
        
        response.raise_for_status()
        save_posted_title(title)
        logging.info(f"Posted: {title}")
        return True
    except requests.RequestException as e:
        logging.error(f"Telegram post failed: {e}")
        return False

def run_once():
    """Runs the bot once to fetch and post today’s news."""
    logging.info("Fetching latest anime news...")
    news_list = fetch_anime_news()
    if not news_list:
        logging.info("No new articles to post.")
        return

    fetch_selected_articles(news_list)
    
    for news in news_list:
        if news["title"] not in load_posted_titles():
            send_to_telegram(news["title"], news["image"], news["summary"])
            time.sleep(1)  # Avoid spam

if __name__ == "__main__":
    run_once()
