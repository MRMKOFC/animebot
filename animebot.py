import os
import requests
from bs4 import BeautifulSoup
import time
import re
import json
from datetime import datetime
from collections import defaultdict
import logging
from concurrent.futures import ThreadPoolExecutor
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

# Get Bot Token & Chat ID from Environment Variables (GitHub Secrets)
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    logging.error("❌ BOT_TOKEN or CHAT_ID is missing! Check environment variables.")
    exit(1)

POSTED_TITLES_FILE = "posted_titles.json"
BASE_URL = "https://www.animenewsnetwork.com"

# Create a session for HTTP requests to reuse connections
session = requests.Session()

# Escape Markdown characters
def escape_markdown(text):
    if not text or not isinstance(text, str):
        return ""
    return re.sub(r'([_*[\]()~`>#\+\-=|{}.!\\])', r'\\\1', text)

# Load posted titles
def load_posted_titles():
    try:
        if os.path.exists(POSTED_TITLES_FILE):
            with open(POSTED_TITLES_FILE, "r", encoding="utf-8") as file:
                return set(json.load(file))
    except Exception as e:
        logging.error(f"Error loading posted titles: {e}")
    return set()

# Save posted title
def save_posted_title(title):
    try:
        titles = load_posted_titles()
        titles.add(title)
        with open(POSTED_TITLES_FILE, "w", encoding="utf-8") as file:
            json.dump(list(titles), file)
    except Exception as e:
        logging.error(f"Error saving posted title: {e}")

# Fetch article details
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_article_details(article_url):
    image_url, summary = None, "No summary available."

    if article_url:
        try:
            response = session.get(article_url, timeout=5)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract image
            img_tag = soup.find('meta', property='og:image')
            if img_tag and img_tag.get('content'):
                image_url = img_tag['content']
            
            # Extract summary
            content_div = soup.find('div', class_='meat') or soup.find('div', class_='content')
            if content_div:
                first_paragraph = content_div.find('p')
                if first_paragraph:
                    summary = first_paragraph.get_text(strip=True)

        except requests.RequestException as e:
            logging.error(f"Error fetching article content: {e}")

    return {'image': image_url, 'summary': summary}

# Fetch today's anime news
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_anime_news():
    today = datetime.now().strftime("%b %d")  # Example: "Mar 16"
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
            date_tag = article.find('time')
            date_text = date_tag.get_text(strip=True) if date_tag else ""

            if today not in date_text:
                continue  # Skip non-today articles

            link = title_tag.find('a')
            article_url = f"{BASE_URL}{link['href']}" if link else None

            news_list.append({'title': title, 'article_url': article_url})

        logging.info(f"✅ Found {len(news_list)} articles for today ({today})")
        return news_list

    except requests.RequestException as e:
        logging.error(f"Fetch error: {e}")
        raise

# Fetch details for each news article
def fetch_selected_articles(news_list):
    posted_titles = load_posted_titles()
    articles_to_fetch = [news for news in news_list if news['title'] not in posted_titles]

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(fetch_article_details, news['article_url']): news for news in articles_to_fetch}

        for future in futures:
            try:
                result = future.result(timeout=10)
                news = futures[future]
                news['image'] = result['image']
                news['summary'] = result['summary']
            except Exception as e:
                logging.error(f"Error processing article: {e}")

    return articles_to_fetch

# Send message to Telegram
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def send_to_telegram(title, image_url, summary):
    safe_title = escape_markdown(title)
    safe_summary = escape_markdown(summary) if summary else "No summary available"
    
    caption = f"✨ *{safe_title}* ✨\n\n📖 {safe_summary}\n\n🌟 Powered by: @TheAnimeTimes_acn"

    params = {'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'Markdown'}

    try:
        if image_url:
            # Validate if image URL is working
            img_response = requests.head(image_url)
            if img_response.status_code >= 400:
                logging.warning(f"⚠️ Image URL is not accessible: {image_url}")
                image_url = None

        if image_url:
            response = session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", 
                                    data={'photo': image_url, **params}, timeout=5)
        else:
            response = session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", 
                                    data={'text': caption, **params}, timeout=5)

        response.raise_for_status()
        save_posted_title(title)
        logging.info(f"✅ Successfully posted: {title}")
        return True

    except requests.RequestException as e:
        logging.error(f"❌ Telegram post failed: {e}")
        return False

# Run bot once per day
def run_once():
    logging.info("🚀 Fetching today's anime news from ANN...")
    
    news_list = fetch_anime_news()
    if not news_list:
        logging.info("❌ No news found for today.")
        return

    news_list = fetch_selected_articles(news_list)
    
    new_posts = 0
    for news in news_list:
        if send_to_telegram(news['title'], news['image'], news['summary']):
            new_posts += 1
        time.sleep(1)  # Delay between posts

    logging.info(f"📢 Posted {new_posts} new articles.")

if __name__ == "__main__":
    run_once()
