import requests
from bs4 import BeautifulSoup
import os
import json
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Configuration (Use hardcoded values if env variables are missing)
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID", "-1002051610513")
POSTED_TITLES_FILE = "posted_titles.json"
BASE_URL = "https://www.animenewsnetwork.com"
TIMEZONE_OFFSET = -7  # Adjust based on ANN's timezone

session = requests.Session()

# Load posted titles to prevent duplicates
def load_posted_titles():
    try:
        with open(POSTED_TITLES_FILE, "r", encoding="utf-8") as file:
            return set(json.load(file))
    except FileNotFoundError:
        return set()

# Save newly posted title
def save_posted_title(title):
    titles = load_posted_titles()
    titles.add(title)
    with open(POSTED_TITLES_FILE, "w", encoding="utf-8") as file:
        json.dump(list(titles), file)

# Scrape ANN news
def fetch_ann_news():
    response = session.get(BASE_URL)
    soup = BeautifulSoup(response.text, 'html.parser')

    news_list = []
    for article in soup.select(".herald.box.news"):  # Fix class selector
        title_tag = article.select_one("h3 a")
        image_tag = article.select_one("img")

        if title_tag:
            title = title_tag.text.strip()
            link = title_tag["href"]
            if not link.startswith("http"):
                link = BASE_URL + link
            image = image_tag["src"] if image_tag else None

            news_list.append({"title": title, "link": link, "image": image})

    return news_list

# Send message to Telegram
def send_to_telegram(news):
    posted_titles = load_posted_titles()
    new_posts = 0

    for article in news:
        if article["title"] in posted_titles:
            continue  # Skip duplicate news

        text = f"📰 *{article['title']}*\n\n🔗 [Read More]({article['link']})"
        image_url = article.get("image")

        if image_url and image_url.startswith("http"):
            send_photo_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            data = {"chat_id": CHAT_ID, "photo": image_url, "caption": text, "parse_mode": "Markdown"}
        else:
            send_photo_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}

        response = requests.post(send_photo_url, data=data)
        if response.status_code == 200:
            posted_titles.add(article["title"])
            new_posts += 1
        else:
            logging.error(f"Telegram post failed: {response.json()}")

    save_posted_title(posted_titles)
    logging.info(f"✅ Posted {new_posts} new articles.")

# Main function
def main():
    logging.info("🚀 Fetching latest news...")
    news = fetch_ann_news()
    if news:
        send_to_telegram(news)
    else:
        logging.info("❌ No new news found.")

if __name__ == "__main__":
    main()
