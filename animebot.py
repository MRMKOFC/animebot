import requests
from bs4 import BeautifulSoup
import time
import re
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import logging
from concurrent.futures import ThreadPoolExecutor
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

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Fetch from environment variables
CHAT_ID = os.getenv("CHAT_ID")      # Fetch from environment variables
POSTED_TITLES_FILE = "posted_titles.json"
TEMP_DIR = "temp_media"
BASE_URL = "https://www.animenewsnetwork.com"
DEBUG_MODE = False  # Set to True to disable date filter for testing
TIMEZONE_OFFSET = -7  # Default to PDT (UTC-7), adjust for ANN's time zone (e.g., -5 for EST)

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
        # Parse the date (e.g., "Mar 16, 06:11") and make it offset-aware
        tz = timezone(timedelta(hours=TIMEZONE_OFFSET))
        # Parse the date without a year first
        date_obj = datetime.strptime(date_text, "%b %d, %H:%M")
        # Get the current year from an offset-aware datetime
        current_year = datetime.now(tz).year
        # Combine the parsed date with the current year and apply timezone
        date_obj = datetime(
            year=current_year,
            month=date_obj.month,
            day=date_obj.day,
            hour=date_obj.hour,
            minute=date_obj.minute,
            tzinfo=tz
        )
        
        # If the date is in the future (e.g., due to time zone differences), adjust the year
        now = datetime.now(tz)
        if date_obj > now:
            date_obj = date_obj.replace(year=current_year - 1)
        
        logging.info(f"Parsed date: {date_obj} (from input: {date_text})")
        return date_obj
    except Exception as e:
        logging.error(f"Error normalizing date: {e}")
        return None

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_anime_news():
    now = datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET)))
    one_day_ago = now - timedelta(days=1)  # Look back 24 hours
    logging.info(f"Server time: {now}, Looking for articles from: {one_day_ago} to {now}")
    
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
                
            # Check if the article is within the last 24 hours
            if not DEBUG_MODE:
                if normalized_date < one_day_ago:
                    logging.info(f"Skipping article older than 24 hours: {date_text} - {title}")
                    continue
                article_date = normalized_date.replace(hour=0, minute=0, second=0, microsecond=0)
                today = now.replace(hour=0, minute=0, second=0, microsecond=0)
                logging.info(f"Article date: {article_date}, Today's date: {today}, Matches today: {article_date == today}")
            
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
        logging.info(f"Articles after filtering: {len(article_list)}")
        logging.info(f"Current time: {now}")
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
    safe_title = escape_markdown(title)
    safe_summary = escape_markdown(summary) if summary else "No summary available"
    
    # Build the caption with line gaps
    caption_parts = [f"✨ *{safe_title}* ✨"]
    
    # Add summary if available, with a line gap
    if safe_summary != "No summary available":
        caption_parts.append(f"\n\n📖 {safe_summary}")
    
    # Add caption in quote format, with a line gap
    caption_parts.append(f'\n\n🌟Powered By:`@TheAnimeTimes_acn`')
    caption = "".join(caption_parts)

    params = {
        'chat_id': CHAT_ID,
        'caption': caption,
        'parse_mode': 'MarkdownV2'
    }

    try:
        if image_url:
            response = session.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={'photo': image_url, **params},
                timeout=5
            )
        else:
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

def run_once():
    logging.info("Starting Anime News Bot in run-once mode...")
    try:
        logging.info("Fetching news from ANN...")
        news_by_date, article_list = fetch_anime_news()
        logging.info(f"Found {len(article_list)} articles after filtering")
        if not news_by_date:
            logging.info("No articles from the last 24 hours found")
            return

        fetch_selected_articles(news_by_date)
        new_posts = 0

        for date, news_list in sorted(news_by_date.items(), key=lambda x: x[0]):
            for news in news_list:
                if news['title'] not in load_posted_titles():
                    if send_to_telegram(news['title'], news['image'], news['summary']):
                        new_posts += 1
                    time.sleep(1)  # Reduced from 2 to 1 second to post faster

        logging.info(f"Posted {new_posts} new articles")

    except Exception as e:
        logging.error(f"Main loop error: {e}")

if __name__ == "__main__":
    run_once()  # Run the bot once
