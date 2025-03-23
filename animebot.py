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
    return re.sub(r"([_*[\]()~`>#\+\-=|{}.!\\])", r"\\\1", text)

def load_posted_titles():
    """Loads posted titles from file."""
    try:
        if os.path.exists(POSTED_TITLES_FILE):
            with open(POSTED_TITLES_FILE, "r", encoding="utf-8") as file:
                return set(json.load(file))
        return set()
    except json.JSONDecodeError:
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
            
            if not title_tag or not date_tag:
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

    thumbnail = article.find("div", class_="thumbnail lazyload")
    if thumbnail and thumbnail.get("data-src"):
        img_url = thumbnail["data-src"]
        image_url = f"{BASE_URL}{img_url}" if not img_url.startswith("http") else img_url
        logging.info(f"🔹 Extracted Image URL: {image_url}")

    if article_url:
        try:
            article_response = session.get(article_url, timeout=5)
            article_response.raise_for_status()
            article_soup = BeautifulSoup(article_response.text, "html.parser")
            content_div = article_soup.find("div", class
