import os
import requests
from bs4 import BeautifulSoup
import telegram
import logging
import asyncio

# Setup logging
logging.basicConfig(level=logging.INFO)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    logging.error("BOT_TOKEN or CHAT_ID environment variables not set. Exiting.")
    exit(1)

bot = telegram.Bot(token=BOT_TOKEN)

def fetch_news():
    url = "https://www.animenewsnetwork.com/news/"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        # Simplified parsing (adjust to your full logic)
        title = soup.find("h3").text.strip() if soup.find("h3") else "No Title"
        summary = (soup.find("p").text.strip()[:200] + "...") if soup.find("p") else "No Summary"
        return {"title": title, "summary": summary}
    except Exception as e:
        logging.error(f"Error fetching news: {e}")
        return None

async def post_to_telegram():
    news = fetch_news()
    if news:
        try:
            message = f"✨ *{news['title']}* ✨\n\n📖 {news['summary']}\n\n🌟 \"Powered by:@TheAnimeTimes_acn\" 🌟"
            await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="Markdown")
            logging.info("News posted successfully")
        except Exception as e:
            logging.error(f"Error posting to Telegram: {e}")
    else:
        logging.warning("No news to post")

if __name__ == "__main__":
    asyncio.run(post_to_telegram())
