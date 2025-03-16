import requests
from bs4 import BeautifulSoup
import os
import json
import logging
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("anime_news_bot.log"),
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
    logging.error("❌ Missing BOT_TOKEN or CHAT_ID in environment variables!")
    exit(1)

# Load posted titles
def load_posted_titles():
    try:
        if os.path.exists(POSTED_TITLES_FILE):
            with open(POSTED_TITLES_FILE, "r", encoding="utf-8") as file:
                return set(json.load(file))
        return set()
    except Exception as e:
        logging.error(f"❌ Error loading posted titles: {e}")
        return set()

# Save newly posted title
def save_posted_title(title):
    try:
        titles = load_posted_titles()
        titles.add(title)
        with open(POSTED_TITLES_FILE, "w", encoding="utf-8") as file:
            json.dump(list(titles), file)
    except Exception as e:
        logging.error(f"❌ Error saving posted title: {e}")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_anime_news():
    try:
        response = requests.get(BASE_URL, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        news_list = []
        today_str = datetime.now().strftime("%b %d")  # Example: "Mar 16"

        for article in soup.find_all('div', class_='herald box news t-news'):
            title_tag = article.find('h3')
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            date_tag = article.find('time')
            date_text = date_tag.get_text(strip=True) if date_tag else ""

            # Check if article is from today
            if today_str not in date_text:
                continue

            link = title_tag.find('a')
            article_url = f"{BASE_URL}{link['href']}" if link else None

            # Fetch summary and image
            image_url, summary = fetch_article_details(article_url)

            news_list.append({
                'title': title,
                'image': image_url,
                'summary': summary
            })

        return news_list

    except requests.RequestException as e:
        logging.error(f"❌ Error fetching ANN news: {e}")
        return []

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_article_details(article_url):
    try:
        if not article_url:
            return None, "No summary available."

        response = requests.get(article_url, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # Extract image
        image_tag = soup.find("meta", property="og:image")
        image_url = image_tag["content"] if image_tag else None

        # Extract summary
        content_div = soup.find('div', class_='meat') or soup.find('div', class_='content')
        summary = "No summary available."
        if content_div:
            first_paragraph = content_div.find('p')
            if first_paragraph:
                summary = first_paragraph.get_text(strip=True)[:200] + "..." if len(first_paragraph.text) > 200 else first_paragraph.text

        return image_url, summary

    except requests.RequestException as e:
        logging.error(f"❌ Error fetching article details: {e}")
        return None, "No summary available."

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def send_to_telegram(title, image_url, summary):
    caption = f"✨ *{title}* ✨\n\n📖 {summary}\n\n🌟 Powered by: `@TheAnimeTimes_acn` 🌟"

    params = {
        'chat_id': CHAT_ID,
        'caption': caption,
        'parse_mode': 'MarkdownV2'
    }

    try:
        if image_url:
            response = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={'photo': image_url, **params},
                timeout=5
            )
        else:
            response = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={'text': caption, **params},
                timeout=5
            )

        response.raise_for_status()
        save_posted_title(title)
        logging.info(f"✅ Successfully posted: {title}")
    except requests.RequestException as e:
        logging.error(f"❌ Error sending message to Telegram: {e}")

def run_once():
    logging.info("🚀 Fetching today's anime news from ANN...")
    news_list = fetch_anime_news()

    if not news_list:
        logging.info("❌ No news found for today.")
        return

    posted_titles = load_posted_titles()
    new_posts = 0

    for news in news_list:
        if news['title'] not in posted_titles:
            send_to_telegram(news['title'], news['image'], news['summary'])
            new_posts += 1
            time.sleep(1)  # Delay between posts

    logging.info(f"✅ Posted {new_posts} new articles.")

if __name__ == "__main__":
    run_once()
