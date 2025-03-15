import os
import json
import logging
import time
import re
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import requests
from bs4 import BeautifulSoup
from telegram import Bot, InputFile
from telegram.error import TelegramError
from requests.exceptions import RequestException
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

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
POSTED_TITLES_FILE = "posted_titles.json"
TEMP_DIR = "temp_media"
BASE_URL = "https://www.animenewsnetwork.com"
DEBUG_MODE = False  # Set to True to disable date filter for testing

# Validate environment variables
if not BOT_TOKEN or not CHAT_ID:
    logging.error("BOT_TOKEN or CHAT_ID environment variables not set. Exiting.")
    exit(1)

if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

# Create a session for HTTP requests to reuse connections
session = requests.Session()

# Enhanced Markdown escaping
def escape_markdown(text):
    if not text or not isinstance(text, str):
        return ""
    return re.sub(r'([_*[\]()~`>#\+\-=|{}.!\\])', r'\\\1', text)

# Load and save posted titles using JSON for better reliability
def load_posted_titles():
    try:
        if os.path.exists(POSTED_TITLES_FILE):
            with open(POSTED_TITLES_FILE, "r", encoding="utf-8") as file:
                return set(json.load(file))
        return set()
    except Exception as e:
        logging.error(f"Error loading posted titles: {e}")
        return set()

def save_posted_title(title):
    try:
        titles = load_posted_titles()
        titles.add(title)
        with open(POSTED_TITLES_FILE, "w", encoding="utf-8") as file:
            json.dump(list(titles), file)
    except Exception as e:
        logging.error(f"Error saving posted title: {e}")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def download_image(image_url):
    """Download the original ANN image to a temporary file, return the path or None if fails."""
    temp_path = os.path.join(TEMP_DIR, "image.jpg")
    try:
        response = session.get(image_url, timeout=5, allow_redirects=True)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if "image" not in content_type:
            logging.warning(f"Invalid image content type {image_url}: {content_type}")
            return None
        with open(temp_path, "wb") as f:
            f.write(response.content)
        return temp_path
    except RequestException as e:
        logging.warning(f"Failed to download image {image_url}: {e}")
        return None

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_article_details(article_url, article):
    image_url = None
    summary = "No summary available."

    # Extract thumbnail
    thumbnail = article.find('div', class_='thumbnail lazyload')
    if thumbnail and thumbnail.get('data-src'):
        img_url = thumbnail['data-src']
        image_url = f"{BASE_URL}{img_url}" if not img_url.startswith('http') else img_url

    # Fetch article content
    if article_url:
        try:
            article_response = session.get(article_url, timeout=5)
            article_response.raise_for_status()
            article_soup = BeautifulSoup(article_response.text, 'html.parser')
            
            content_div = article_soup.find('div', class_='meat') or article_soup.find('div', class_='content')
            if content_div:
                first_paragraph = content_div.find('p')
                if first_paragraph:
                    summary = first_paragraph.get_text(strip=True)[:200] + "..." if len(first_paragraph.text) > 200 else first_paragraph.text
        except requests.RequestException as e:
            logging.error(f"Error fetching article content: {e}")

    return {'image': image_url, 'summary': summary}

def normalize_date(date_text):
    try:
        current_year = datetime.now().year
        date_obj = datetime.strptime(f"{date_text}, {current_year}", "%b %d, %H:%M, %Y")
        if date_obj > datetime.now():
            date_obj = date_obj.replace(year=current_year - 1)
        return date_obj
    except Exception as e:
        logging.error(f"Error normalizing date: {e}")
        return None

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_anime_news():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        response = session.get(BASE_URL, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        news_by_date = defaultdict(list)
        article_list = []
        all_articles = 0
        
        for article in soup.find_all('div', class_='herald box news t-news'):
            all_articles += 1
            title_tag = article.find('h3')
            if not title_tag:
                continue
                
            title = title_tag.get_text(strip=True)
            date_tag = article.find('time')
            date_text = date_tag.get_text(strip=True) if date_tag else ""
            
            normalized_date = normalize_date(date_text)
            if not normalized_date:
                logging.info(f"Skipping article with invalid date: {title}")
                continue
                
            if not DEBUG_MODE:
                article_date = normalized_date.replace(hour=0, minute=0, second=0, microsecond=0)
                if article_date != today:
                    logging.info(f"Skipping non-today article: {date_text} - {title}")
                    continue
                
            link = title_tag.find('a')
            article_url = f"{BASE_URL}{link['href']}" if link else None
            
            article_list.append((title, article_url, date_text, article))
            news_by_date[normalized_date].append({
                'title': title,
                'image': None,
                'summary': "Fetching summary...",
                'article_url': article_url,
                'article': article
            })

        logging.info(f"Total articles on ANN homepage: {all_articles}")
        return news_by_date, article_list

    except requests.RequestException as e:
        logging.error(f"Fetch error: {e}")
        raise

def fetch_selected_articles(news_by_date):
    articles_to_fetch = []
    posted_titles = load_posted_titles()
    
    for date, news_list in news_by_date.items():
        for news in news_list:
            if news['title'] not in posted_titles:
                articles_to_fetch.append((news['article_url'], news['article']))

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(fetch_article_details, url, article) for url, article in articles_to_fetch]
        
        for idx, future in enumerate(futures):
            try:
                result = future.result(timeout=10)
                article_url = articles_to_fetch[idx][0]
                for news_list in news_by_date.values():
                    for news in news_list:
                        if news['article_url'] == article_url:
                            news['image'] = result['image']
                            news['summary'] = result['summary']
                            break
            except Exception as e:
                logging.error(f"Error processing article: {e}")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def send_to_telegram(title, image_url, summary):
    bot = Bot(token=BOT_TOKEN)
    safe_title = escape_markdown(title)
    safe_summary = escape_markdown(summary) if summary else "No summary available"
    
    # Build the caption with line gaps
    caption_parts = [f"✨ *{safe_title}* ✨"]
    
    if safe_summary != "No summary available":
        caption_parts.append(f"\n\n📖 {safe_summary}")
    
    caption_parts.append(f'\n\n🌟 [Powered By : `@TheAnimeTimes_acn`] 🌟')
    caption = "".join(caption_parts)

    logging.info(f"Attempting to send message to chat_id: {CHAT_ID}")
    logging.info(f"Using BOT_TOKEN: {BOT_TOKEN[:4]}... (partial for security)")  # Log partial token for verification

    try:
        if image_url:
            # Download the image locally
            image_path = download_image(image_url)
            if image_path and os.path.exists(image_path):
                with open(image_path, "rb") as image_file:
                    response = bot.send_photo(
                        chat_id=CHAT_ID,
                        photo=InputFile(image_file, filename="image.jpg"),
                        caption=caption,
                        parse_mode='MarkdownV2'
                    )
                logging.info(f"Successfully sent photo to chat_id {CHAT_ID}. Response: {response.message_id if response else 'No response'}")
            else:
                logging.warning(f"Failed to download image for {title}. Posting text only.")
                response = bot.send_message(
                    chat_id=CHAT_ID,
                    text=caption,
                    parse_mode='MarkdownV2'
                )
                logging.info(f"Successfully sent message to chat_id {CHAT_ID}. Response: {response.message_id if response else 'No response'}")
        else:
            logging.info(f"No image for {title}. Posting text only.")
            response = bot.send_message(
                chat_id=CHAT_ID,
                text=caption,
                parse_mode='MarkdownV2'
            )
            logging.info(f"Successfully sent message to chat_id {CHAT_ID}. Response: {response.message_id if response else 'No response'}")

        if not response:
            raise ValueError("Telegram API call returned no response")
        save_posted_title(title)
        return True
    except TelegramError as e:
        logging.error(f"Telegram post failed for chat_id {CHAT_ID}: {e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error posting to chat_id {CHAT_ID}: {e}")
        return False
    finally:
        # Clean up temporary file if it exists
        if os.path.exists(os.path.join(TEMP_DIR, "image.jpg")):
            os.remove(os.path.join(TEMP_DIR, "image.jpg"))

def run_once():
    """Run the bot once for GitHub Actions compatibility."""
    logging.info("Starting Anime News Bot (single run)...")
    try:
        logging.info("Fetching news from ANN...")
        news_by_date, article_list = fetch_anime_news()
        logging.info(f"Found {len(article_list)} articles after filtering")
        if not news_by_date:
            logging.info("No articles from today found")
            return

        fetch_selected_articles(news_by_date)
        new_posts = 0

        for date, news_list in sorted(news_by_date.items(), key=lambda x: x[0]):
            for news in news_list:
                if news['title'] not in load_posted_titles():
                    if send_to_telegram(news['title'], news['image'], news['summary']):
                        new_posts += 1
                    time.sleep(1)  # Post faster

        logging.info(f"Posted {new_posts} new articles")
    except Exception as e:
        logging.error(f"Main loop error: {e}")
        raise

if __name__ == "__main__":
    run_once()
