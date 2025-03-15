import os
import json
import hashlib
import logging
import asyncio
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import requests
import re
from telegram import Bot, InputFile
from telegram.error import TelegramError
from requests.exceptions import RequestException

# Setup logging
logging.basicConfig(level=logging.INFO)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
DATA_FILE = "posted_news.json"
ANN_URL = "https://www.animenewsnetwork.com/news/"
TEMP_IMAGE_PATH = "temp_image.jpg"

if not BOT_TOKEN or not CHAT_ID:
    logging.error("BOT_TOKEN or CHAT_ID environment variables not set. Exiting.")
    exit(1)

bot = Bot(token=BOT_TOKEN)

async def load_posted_news():
    """Load previously posted news titles from file."""
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        return {}
    except Exception as e:
        logging.error(f"Error loading posted news: {e}")
        return {}

async def save_posted_news(title_hash):
    """Save the current news title to file to avoid duplicates."""
    posted_news = await load_posted_news()
    posted_news[title_hash] = True
    with open(DATA_FILE, "w") as f:
        json.dump(posted_news, f)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(2),
    retry=retry_if_exception_type(RequestException),
    before_sleep=lambda retry_state: logging.info(f"Retrying image download (attempt {retry_state.attempt_number})...")
)
def download_image(image_url):
    """Download the original ANN image to a local file, return the path or None if fails."""
    temp_path = TEMP_IMAGE_PATH
    try:
        response = requests.get(image_url, timeout=5, allow_redirects=True)
        response.raise_for_status()  # Raise an exception for bad status codes
        content_type = response.headers.get("Content-Type", "").lower()
        if "image" not in content_type:
            logging.warning(f"Invalid image content type {image_url}: {content_type}")
            return None
        with open(temp_path, "wb") as f:
            f.write(response.content)
        return temp_path
    except Exception as e:
        logging.warning(f"Failed to download image {image_url}: {e}")
        return None

def validate_image_url(image_url):
    """Validate if the image URL is accessible and points to a valid image."""
    try:
        response = requests.get(image_url, timeout=5, allow_redirects=True, stream=True)
        if response.status_code != 200:
            logging.warning(f"Invalid image URL {image_url}: Status {response.status_code}")
            return False
        content_type = response.headers.get("Content-Type", "").lower()
        if "image" not in content_type:
            logging.warning(f"Invalid image URL {image_url}: Content-Type {content_type} is not an image")
            return False
        if not any(ext in image_url.lower() for ext in [".jpg", ".jpeg", ".png", ".gif"]):
            logging.warning(f"Invalid image URL {image_url}: No image file extension detected")
            return False
        response.raw.decode_content = True
        chunk = response.raw.read(1024)
        if not chunk or len(chunk) < 100:
            logging.warning(f"Invalid image URL {image_url}: Insufficient data")
            return False
        return True
    except Exception as e:
        logging.warning(f"Failed to validate image URL {image_url}: {e}")
        return False

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def fetch_news():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        })
        try:
            try:
                await page.goto(ANN_URL, wait_until="domcontentloaded", timeout=60000)
                logging.info(f"Successfully loaded ANN page (DOM): {ANN_URL}")
            except PlaywrightTimeoutError:
                logging.warning("DOM content load timed out, trying networkidle...")
                await page.goto(ANN_URL, wait_until="networkidle", timeout=90000)
                logging.info(f"Successfully loaded ANN page (networkidle): {ANN_URL}")
            
            await page.wait_for_selector(".wrap, .news-item, .herald.box.news, article, .mainfeed-day", state="visible", timeout=30000)
            content = await page.content()
            logging.debug(f"Page content: {content[:1000]}")
            article = await page.query_selector(".wrap, .news-item, .herald.box.news, article, .mainfeed-day")
            if not article:
                logging.warning("No article found on the page using any selector.")
                return None
            # Extract and clean title
            title_element = await article.query_selector("h3, h2, h1")
            title = await title_element.inner_text() if title_element else "No Title"
            title = re.sub(r'\s+', ' ', title.strip())
            title_hash = hashlib.md5(title.encode()).hexdigest()
            posted_news = await load_posted_news()
            if title_hash in posted_news:
                logging.info(f"News already posted: {title}")
                return None
            # Extract summary with improved selectors
            summary = "No summary available for this article. 📜"
            for selector in [".intro", "p", ".summary", ".content", "div[itemprop='articleBody']"]:
                summary_elements = await article.query_selector_all(selector)
                for element in summary_elements:
                    text = await element.inner_text()
                    text = text.strip()
                    if text and "see the archives" not in text.lower() and len(text) > 20:
                        summary = text[:200] + "..."
                        break
                if summary != "No summary available for this article. 📜":
                    break
            # Extract image URL
            img_element = await article.query_selector("img.thumbnail, img[src*=jpg], img[src*=png], img[src*=jpeg]")
            image_url = await img_element.get_attribute("src") if img_element else None
            if image_url and not image_url.startswith("http"):
                image_url = f"https://www.animenewsnetwork.com{image_url}"
            # Validate image URL, skip if invalid
            if not image_url or not validate_image_url(image_url):
                logging.warning(f"Invalid or no image URL found: {image_url}. Skipping image.")
                image_url = None
            logging.info(f"Fetched news: Title={title}, Summary={summary}, Image URL={image_url}")
            return {"title": title, "summary": summary, "image_url": image_url, "title_hash": title_hash}
        except PlaywrightTimeoutError as e:
            logging.error(f"Timeout waiting for page or article to load: {e}")
            return None
        except Exception as e:
            logging.error(f"Error fetching or parsing ANN page: {e}")
            return None
        finally:
            await browser.close()

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
    else:
        return f"🌟 {text} 🌟"

async def post_to_telegram():
    logging.info(f"Starting post_to_telegram with CHAT_ID: {CHAT_ID}")
    news = await fetch_news()
    if news:
        try:
            title = sanitize_text(news['title'])
            summary = sanitize_text(news['summary'])
            image_url = news['image_url']
            title_hash = news['title_hash']
            
            # Add emojis to title and summary
            emoji_title = add_emojis(title, is_title=True)
            emoji_summary = add_emojis(summary, is_summary=True)
            message = f"{emoji_title}\n\n{emoji_summary}\n\n🌟 Powered by: @TheAnimeTimes_acn 🌟"
            
            # Try to send image with caption only if image_url is valid
            image_posted = False
            if image_url:
                try:
                    image_path = download_image(image_url)
                    if image_path and os.path.exists(image_path):
                        with open(image_path, "rb") as f:
                            image_caption = add_emojis("Powered by: @TheAnimeTimes_acn", is_summary=False)
                            await bot.send_photo(
                                chat_id=CHAT_ID,
                                photo=InputFile(f, filename="image.jpg"),
                                caption=image_caption
                            )
                        logging.info(f"Sent image to Telegram with caption from {image_url}")
                        image_posted = True
                    else:
                        logging.warning(f"Image file not found at {image_path}. Skipping image posting.")
                except TelegramError as e:
                    logging.warning(f"Failed to send image to Telegram: {e}. Skipping image posting.")
                    logging.debug(f"Telegram error details: {str(e)}")

            # Send text message (always posted)
            logging.info(f"Attempting to send message to Telegram: {message}")
            logging.info(f"Message length: {len(message)}, Bytes: {len(message.encode('utf-8'))}")
            await bot.send_message(
                chat_id=CHAT_ID,
                text=message,
            )
            logging.info("News posted successfully.")
            # Clean up temporary file if it exists
            if os.path.exists(TEMP_IMAGE_PATH):
                os.remove(TEMP_IMAGE_PATH)
            # Save the posted news to avoid duplicates
            await save_posted_news(title_hash)
        except Exception as e:
            logging.error(f"Error posting to Telegram: {e}")
            # Clean up temporary file if it exists
            if os.path.exists(TEMP_IMAGE_PATH):
                os.remove(TEMP_IMAGE_PATH)
            raise
    else:
        logging.warning("No new news to post")

if __name__ == "__main__":
    asyncio.run(post_to_telegram())
