import requests
from bs4 import BeautifulSoup
import time
import re
import os
import json
import logging
import pytz
from datetime import datetime, timedelta
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
DEBUG_MODE = False  # Set to False for production mode
DATE_RANGE_DAYS = 3  # Process articles within a 3-day range (past and future)

if not BOT_TOKEN or not CHAT_ID:
    logging.error("BOT_TOKEN or CHAT_ID is missing. Check environment variables.")
    exit(1)

# Time Zone Handling
utc_tz = pytz.utc
local_tz = pytz.timezone("Asia/Kolkata")  # Change if needed
today_local = datetime.now(local_tz).date()
date_threshold_past = today_local - timedelta(days=DATE_RANGE_DAYS)  # 3 days before today
date_threshold_future = today_local + timedelta(days=DATE_RANGE_DAYS)  # 3 days after today

session = requests.Session()

def escape_markdown(text):
    """Escapes Telegram Markdown special characters for MarkdownV2."""
    if not text or not isinstance(text, str):
        return ""
    # Escaping characters for MarkdownV2 as per Telegram's requirements
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
            date_str = date_tag["datetime"]  # Extract ISO 8601 date
            news_date = datetime.fromisoformat(date_str).astimezone(local_tz).date()  # Convert to local date

            if DEBUG_MODE or (date_threshold_past <= news_date <= date_threshold_future):
                link = title_tag.find("a")
                article_url = f"{BASE_URL}{link['href']}" if link else None
                news_list.append({"title": title, "article_url": article_url, "article": article})
                logging.info(f"✅ Found recent news (date: {news_date}): {title}")
            else:
                logging.info(f"⏩ Skipping (outside {DATE_RANGE_DAYS}-day range, date: {news_date}): {title}")

        logging.info(f"Filtered recent articles: {len(news_list)}")
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
        # Validate the image URL by making a HEAD request
        try:
            response = session.head(image_url, timeout=5, allow_redirects=True)
            if response.status_code != 200 or 'image' not in response.headers.get('Content-Type', '').lower():
                logging.warning(f"Invalid image URL: {image_url} (Status: {response.status_code})")
                image_url = None
            else:
                logging.info(f"Valid image URL: {image_url}")
        except requests.RequestException as e:
            logging.warning(f"Failed to validate image URL: {image_url} - {e}")
            image_url = None

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
    """Posts news to Telegram with updated formatting."""
    # Escape the title and summary for MarkdownV2
    safe_title = escape_markdown(title)
    safe_summary = escape_markdown(summary) if summary else "No summary available"

    # Format the title as {**Title**} ⚡ (bold title inside curly braces)
    formatted_title = f"\\{{**{safe_title}**\\}} ⚡"

    # Format the summary with quotation marks at the end
    formatted_summary = f"{safe_summary}\""

    # Get the current time in the local timezone for the timestamp
    current_time = datetime.now(local_tz).strftime("%I:%M %p")  # e.g., 11:22 PM

    # Format the caption as per the image
    caption = f"{formatted_title}\n\n{formatted_summary}\n\n🍁 \\| @TheAnimeTimes_acn {current_time}"

    # Log the caption for debugging
    logging.info(f"Attempting to send caption: {caption}")

    params = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "MarkdownV2"}

    try:
        if image_url:
            response = session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data={"photo": image_url, **params}, timeout=5)
        else:
            response = session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"text": caption, **params}, timeout=5)
        
        response.raise_for_status()
        save_posted_title(title)
        logging.info(f"✅ Posted: {title}")
    except requests.RequestException as e:
        logging.error(f"Telegram post failed with MarkdownV2: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logging.error(f"Response content: {e.response.text}")

        # Fallback: Try sending without MarkdownV2
        logging.info("Falling back to plain text message...")
        plain_caption = f"{{ {title} }} ⚡\n\n{summary}\"\n\n🍁 | @TheAnimeTimes_acn {current_time}"
        params = {"chat_id": CHAT_ID, "caption": plain_caption}

        try:
            if image_url:
                response = session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data={"photo": image_url, **params}, timeout=5)
            else:
                response = session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"text": plain_caption, **params}, timeout=5)
            
            response.raise_for_status()
            save_posted_title(title)
            logging.info(f"✅ Posted (plain text fallback): {title}")
        except requests.RequestException as e:
            logging.error(f"Telegram post failed (plain text fallback): {e}")
            if hasattr(e, 'response') and e.response is not None:
                logging.error(f"Response content: {e.response.text}")

def run_once():
    """Runs the bot once to fetch and post recent news."""
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
