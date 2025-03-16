import requests
from bs4 import BeautifulSoup
import time
import re
import os
from datetime import datetime
from collections import defaultdict
import logging
import json
from tenacity import retry, stop_after_attempt, wait_exponential

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('anime_news_bot.log'),
        logging.StreamHandler()
    ]
)

# Configuration (Using Environment Variables)
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
POSTED_TITLES_FILE = "posted_titles.json"
BASE_URL = "https://www.animenewsnetwork.com"

# Ensure environment variables are set
if not BOT_TOKEN or not CHAT_ID:
    logging.error("Missing BOT_TOKEN or CHAT_ID in environment variables!")
    exit(1)

# Create session
session = requests.Session()

# Load posted titles
def load_posted_titles():
    try:
        if os.path.exists(POSTED_TITLES_FILE):
            with open(POSTED_TITLES_FILE, "r", encoding="utf-8") as file:
                return set(json.load(file))
        return set()
    except Exception as e:
        logging.error(f"Error loading posted titles: {e}")
        return set()

# Save newly posted titles
def save_posted_title(title):
    try:
        titles = load_posted_titles()
        titles.add(title)
        with open(POSTED_TITLES_FILE, "w", encoding="utf-8") as file:
            json.dump(list(titles), file)
    except Exception as e:
        logging.error(f"Error saving posted title: {e}")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_anime_news():
    try:
        response = session.get(BASE_URL, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        news_list = []
        for article in soup.find_all('div', class_='herald box news t-news'):
            title_tag = article.find('h3')
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            link = title_tag.find('a')
            article_url = f"{BASE_URL}{link['href']}" if link else None

            if title and article_url:
                news_list.append({'title': title, 'article_url': article_url})

        return news_list

    except requests.RequestException as e:
        logging.error(f"Error fetching ANN news: {e}")
        return []

def send_to_telegram(title, article_url):
    caption = f"✨ *{title}* ✨\n\n🔗 [Read More]({article_url})\n\n🌟 Powered By: `@TheAnimeTimes_acn` 🌟"
    params = {
        'chat_id': CHAT_ID,
        'text': caption,
        'parse_mode': 'MarkdownV2'
    }

    try:
        response = session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data=params, timeout=5)
        response.raise_for_status()
        save_posted_title(title)
        logging.info(f"Successfully posted: {title}")
    except requests.RequestException as e:
        logging.error(f"Error sending message to Telegram: {e}")

def run_once():
    logging.info("🚀 Fetching latest news from ANN...")
    news_list = fetch_anime_news()

    if not news_list:
        logging.info("❌ No new news found.")
        return

    posted_titles = load_posted_titles()
    new_posts = 0

    for news in news_list:
        if news['title'] not in posted_titles:
            send_to_telegram(news['title'], news['article_url'])
            new_posts += 1
            time.sleep(1)  # Delay between posts

    logging.info(f"✅ Posted {new_posts} new articles.")

if __name__ == "__main__":
    run_once()
