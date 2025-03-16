import requests
from bs4 import BeautifulSoup
import re
import os
import json
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from tenacity import retry, stop_after_attempt, wait_exponential

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
POSTED_TITLES_FILE = "posted_titles.json"
TEMP_DIR = "temp_media"
BASE_URL = "https://www.animenewsnetwork.com"
TIMEZONE_OFFSET = -7  # Adjust based on ANN's timezone

# Ensure temp directory exists
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

session = requests.Session()

# Load posted titles
def load_posted_titles():
    try:
        if os.path.exists(POSTED_TITLES_FILE):
            with open(POSTED_TITLES_FILE, "r", encoding="utf-8") as file:
                return set(json.load(file))
    except Exception as e:
        logging.error(f"Error loading posted titles: {e}")
    return set()

# Save new posted title
def save_posted_title(title):
    try:
        titles = load_posted_titles()
        titles.add(title)
        with open(POSTED_TITLES_FILE, "w", encoding="utf-8") as file:
            json.dump(list(titles), file)
    except Exception as e:
        logging.error(f"Error saving posted title: {e}")

# Normalize ANN's date format
def normalize_date(date_text):
    try:
        tz = timezone(timedelta(hours=TIMEZONE_OFFSET))
        date_obj = datetime.strptime(date_text, "%b %d, %H:%M")
        date_obj = date_obj.replace(year=datetime.now(tz).year, tzinfo=tz)
        return date_obj
    except Exception as e:
        logging.error(f"Error normalizing date: {e}")
        return None

# Fetch news from ANN
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_anime_news():
    now = datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET)))
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        response = session.get(BASE_URL, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        news_by_date = defaultdict(list)
        articles = soup.find_all('div', class_='herald box news t-news')

        for article in articles:
            title_tag = article.find('h3')
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            date_tag = article.find('time')
            date_text = date_tag.get_text(strip=True) if date_tag else ""
            normalized_date = normalize_date(date_text)

            if not normalized_date or normalized_date < today_start:
                continue  # Ignore old articles

            link = title_tag.find('a')
            article_url = f"{BASE_URL}{link['href']}" if link else None

            # Extract image
            thumbnail = article.find('div', class_='thumbnail lazyload')
            image_url = None
            if thumbnail and thumbnail.get('data-src'):
                img_url = thumbnail['data-src']
                image_url = f"{BASE_URL}{img_url}" if not img_url.startswith('http') else img_url

            news_by_date[normalized_date].append({
                'title': title,
                'image': image_url,
                'summary': "Fetching summary...",
                'article_url': article_url
            })

        return news_by_date

    except requests.RequestException as e:
        logging.error(f"Fetch error: {e}")
        raise

# Fetch article summaries
def fetch_article_summaries(news_by_date):
    def fetch_details(article_url):
        try:
            response = session.get(article_url, timeout=5)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            content_div = soup.find('div', class_='meat') or soup.find('div', class_='content')
            summary = "No summary available."
            if content_div:
                first_paragraph = content_div.find('p')
                if first_paragraph:
                    summary = first_paragraph.get_text(strip=True)[:200] + "..."
            return summary
        except requests.RequestException as e:
            logging.error(f"Error fetching article content: {e}")
            return "No summary available."

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(fetch_details, news['article_url']): news for date, news_list in news_by_date.items() for news in news_list}
        for future in futures:
            try:
                summary = future.result(timeout=10)
                futures[future]['summary'] = summary
            except Exception as e:
                logging.error(f"Error processing article: {e}")

# Send post to Telegram (with full image)
def send_to_telegram(title, image_url, summary):
    caption = f"📰 *{title}*\n\n📖 {summary}\n\n🌟 Powered By: `@TheAnimeTimes_acn`"
    params = {
        'chat_id': CHAT_ID,
        'caption': caption,
        'parse_mode': 'MarkdownV2'
    }

    try:
        if image_url:
            image_path = os.path.join(TEMP_DIR, "temp.jpg")
            response = session.get(image_url, stream=True)
            if response.status_code == 200:
                with open(image_path, 'wb') as img_file:
                    img_file.write(response.content)

                with open(image_path, 'rb') as img_file:
                    response = requests.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                        data=params,
                        files={'photo': img_file},
                        timeout=10
                    )
                    response.raise_for_status()
                    save_posted_title(title)
                    logging.info(f"Successfully posted: {title}")
                    return True

        # If no image, send text-only message
        response = session.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={'text': caption, **params},
            timeout=5
        )
        response.raise_for_status()
        save_posted_title(title)
        logging.info(f"Successfully posted: {title}")
        return True

    except requests.RequestException as e:
        logging.error(f"Telegram post failed: {e}")
        return False

# Main function
def run_once():
    logging.info("Starting Anime News Bot...")
    news_by_date = fetch_anime_news()
    fetch_article_summaries(news_by_date)

    new_posts = 0
    for date, news_list in news_by_date.items():
        for news in news_list:
            if news['title'] not in load_posted_titles():
                if send_to_telegram(news['title'], news['image'], news['summary']):
                    new_posts += 1

    logging.info(f"Posted {new_posts} new articles")

if __name__ == "__main__":
    run_once()
