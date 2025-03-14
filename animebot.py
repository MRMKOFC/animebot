import os
import requests
from bs4 import BeautifulSoup
import telegram
import logging
import asyncio
from tenacity import retry, stop_after_attempt, wait_fixed
import re
import json
import hashlib

# Setup logging
logging.basicConfig(level=logging.INFO)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
DATA_FILE = "posted_news.json"

if not BOT_TOKEN or not CHAT_ID:
    logging.error("BOT_TOKEN or CHAT_ID environment variables not set. Exiting.")
    exit(1)

bot = telegram.Bot(token=BOT_TOKEN)

def load_posted_news():
    """Load previously posted news titles from file."""
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        return {}
    except Exception as e:
        logging.error(f"Error loading posted news: {e}")
        return {}

def save_posted_news(title_hash):
    """Save the current news title to file to avoid duplicates."""
    posted_news = load_posted_news()
    posted_news[title_hash] = True
    with open(DATA_FILE, "w") as f:
        json.dump(posted_news, f)

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_news():
    url = "https://www.animenewsnetwork.com/news/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        response = requests.get(url, timeout=15, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        # Extract the first news article
        article = soup.find("div", class_="herald box news")
        if not article:
            logging.warning("No article found on the page.")
            return None
        # Extract title
        title_tag = article.find("h3")
        title = title_tag.text.strip() if title_tag else "No Title"
        title_hash = hashlib.md5(title.encode()).hexdigest()
        posted_news = load_posted_news()
        if title_hash in posted_news:
            logging.info(f"News already posted: {title}")
            return None
        # Extract summary (look for the first meaningful paragraph)
        summary_tag = None
        for p in article.find_all("p"):
            text = p.text.strip()
            if text and "see the archives" not in text.lower() and len(text) > 20:
                summary_tag = p
                break
        summary = summary_tag.text.strip()[:200] + "..." if summary_tag else "No summary available for this article. 📜"
        # Extract first image URL, handle relative URLs
        img_tag = article.find("img", class_="thumbnail") or article.find("img", {"src": re.compile(r'.*\.jpg$|\.png$|\.jpeg$')})
        image_url = img_tag['src'] if img_tag and 'src' in img_tag.attrs else None
        if image_url and not image_url.startswith("http"):
            image_url = f"https://www.animenewsnetwork.com{image_url}"
        # Fallback image if none found
        if not image_url:
            image_url = "https://via.placeholder.com/300x200?text=Anime+News+🌟"
        logging.info(f"Fetched news: Title={title}, Summary={summary}, Image URL={image_url}")
        return {"title": title, "summary": summary, "image_url": image_url, "title_hash": title_hash}
    except Exception as e:
        logging.error(f"Error fetching news: {e}")
        return None

def sanitize_text(text):
    """Remove or replace problematic characters for Telegram."""
    text = text.encode('utf-8', 'ignore').decode('utf-8')
    return text

def add_emojis(text, is_title=True, is_summary=False):
    """Add emojis to text based on context."""
    if is_title:
        return f"✨ {text} ✨"
    elif is_summary:
        return f"📖 {text} 📖"
    else:  # caption
        return f"🌟 {text} 🌟"

async def post_to_telegram():
    logging.info(f"Starting post_to_telegram with CHAT_ID: {CHAT_ID}")
    news = fetch_news()
    if news:
        try:
            title = sanitize_text(news['title'])
            summary = sanitize_text(news['summary'])
            image_url = news['image_url']
            title_hash = news['title_hash']
            
            # Add emojis to title and summary
            emoji_title = add_emojis(title, is_title=True)
            emoji_summary = add_emojis(summary, is_summary=True)
            
            # Send image with caption if available
            if image_url:
                caption = add_emojis("Powered by: @TheAnimeTimes_acn", is_summary=False)
                await bot.send_photo(chat_id=CHAT_ID, photo=image_url, caption=caption)
                logging.info(f"Sent image to Telegram with caption: {image_url}")
            
            # Send text message with caption for all posts
            message = f"{emoji_title}\n\n{emoji_summary}"
            caption = add_emojis("Powered by: @TheAnimeTimes_acn", is_summary=False)
            logging.info(f"Attempting to send message to Telegram: {message}")
            logging.info(f"Message length: {len(message)}, Bytes: {len(message.encode('utf-8'))}")
            response = await bot.send_message(chat_id=CHAT_ID, text=message, caption=caption)
            logging.info(f"News posted successfully. Response: {response}")
            # Save the posted news to avoid duplicates
            save_posted_news(title_hash)
        except Exception as e:
            logging.error(f"Error posting to Telegram: {e}")
            logging.error(f"Message that failed: {message}")
    else:
        logging.warning("No new news to post")

if __name__ == "__main__":
    asyncio.run(post_to_telegram())
