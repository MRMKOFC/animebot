import os
import json
import hashlib
import logging
import asyncio
from tenacity import retry, stop_after_attempt, wait_fixed
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import telegram

# Setup logging
logging.basicConfig(level=logging.INFO)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
DATA_FILE = "posted_news.json"
ANN_URL = "https://www.animenewsnetwork.com/news/"

if not BOT_TOKEN or not CHAT_ID:
    logging.error("BOT_TOKEN or CHAT_ID environment variables not set. Exiting.")
    exit(1)

bot = telegram.Bot(token=BOT_TOKEN)

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

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def fetch_news():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        # Set a realistic user-agent to avoid bot detection
        await page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        })
        try:
            # First attempt with domcontentloaded
            try:
                await page.goto(ANN_URL, wait_until="domcontentloaded", timeout=60000)
                logging.info(f"Successfully loaded ANN page (DOM): {ANN_URL}")
            except PlaywrightTimeoutError:
                logging.warning("DOM content load timed out, trying networkidle...")
                await page.goto(ANN_URL, wait_until="networkidle", timeout=90000)  # Increased to 90s
                logging.info(f"Successfully loaded ANN page (networkidle): {ANN_URL}")
            
            # Wait for a news article to load (updated selectors)
            await page.wait_for_selector(".wrap, .news-item, .herald.box.news, article, .mainfeed-day", state="visible", timeout=30000)
            content = await page.content()
            logging.debug(f"Page content: {content[:1000]}")  # Log more content for debugging
            # Extract the first article
            article = await page.query_selector(".wrap, .news-item, .herald.box.news, article, .mainfeed-day")
            if not article:
                logging.warning("No article found on the page using any selector.")
                return None
            # Extract title
            title_element = await article.query_selector("h3, h2, h1")
            title = await title_element.inner_text() if title_element else "No Title"
            title = title.strip()
            title_hash = hashlib.md5(title.encode()).hexdigest()
            posted_news = await load_posted_news()
            if title_hash in posted_news:
                logging.info(f"News already posted: {title}")
                return None
            # Extract summary (first meaningful paragraph)
            summary_elements = await article.query_selector_all("p")
            summary = "No summary available for this article. 📜"
            for element in summary_elements:
                text = await element.inner_text()
                text = text.strip()
                if text and "see the archives" not in text.lower() and len(text) > 20:
                    summary = text[:200] + "..."
                    break
            # Extract image URL
            img_element = await article.query_selector("img.thumbnail, img[src*=jpg], img[src*=png], img[src*=jpeg]")
            image_url = await img_element.get_attribute("src") if img_element else None
            if image_url and not image_url.startswith("http"):
                image_url = f"https://www.animenewsnetwork.com{image_url}"
            # Fallback image if none found
            if not image_url:
                image_url = "https://via.placeholder.com/300x200?text=Anime+News+🌟"
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
    else:  # caption
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
            
            # Send image with caption if available
            if image_url:
                image_caption = add_emojis("Powered by: @TheAnimeTimes_acn", is_summary=False)
                await bot.send_photo(chat_id=CHAT_ID, photo=image_url, caption=image_caption)
                logging.info(f"Sent image to Telegram with caption: {image_url}")
            
            # Send text message with caption for all posts
            message = f"{emoji_title}\n\n{emoji_summary}"
            text_caption = add_emojis("Powered by: @TheAnimeTimes_acn", is_summary=False)
            logging.info(f"Attempting to send message to Telegram: {message}")
            logging.info(f"Message length: {len(message)}, Bytes: {len(message.encode('utf-8'))}")
            response = await bot.send_message(chat_id=CHAT_ID, text=message, caption=text_caption)
            logging.info(f"News posted successfully. Response: {response}")
            # Save the posted news to avoid duplicates
            await save_posted_news(title_hash)
        except Exception as e:
            logging.error(f"Error posting to Telegram: {e}")
            logging.error(f"Message that failed: {message}")
    else:
        logging.warning("No new news to post")

if __name__ == "__main__":
    asyncio.run(post_to_telegram())
