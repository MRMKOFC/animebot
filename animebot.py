import os
import requests
from bs4 import BeautifulSoup
import telegram
import logging
import asyncio
from tenacity import retry, stop_after_attempt, wait_fixed

# Setup logging
logging.basicConfig(level=logging.INFO)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    logging.error("BOT_TOKEN or CHAT_ID environment variables not set. Exiting.")
    exit(1)

bot = telegram.Bot(token=BOT_TOKEN)

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
        # Simplified parsing (adjust to your full logic)
        title = soup.find("h3").text.strip() if soup.find("h3") else "No Title"
        summary = (soup.find("p").text.strip()[:200] + "...") if soup.find("p") else "No Summary"
        logging.info(f"Fetched news: {title}")
        return {"title": title, "summary": summary}
    except Exception as e:
        logging.error(f"Error fetching news: {e}")
        return None

async def post_to_telegram():
    logging.info(f"Starting post_to_telegram with CHAT_ID: {CHAT_ID}")
    news = fetch_news()
    if news:
        try:
            message = f"✨ *{news['title']}* ✨\n\n📖 {news['summary']}\n\n🌟 \"Powered by:@TheAnimeTimes_acn\" 🌟"
            logging.info("Attempting to send message to Telegram")
            await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="Markdown")
            logging.info("News posted successfully")
        except Exception as e:
            logging.error(f"Error posting to Telegram: {e}")
    else:
        logging.warning("No news to post")

if __name__ == "__main__":
    asyncio.run(post_to_telegram())
