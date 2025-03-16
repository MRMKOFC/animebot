import os
import requests
import json
from datetime import datetime
from bs4 import BeautifulSoup

# Load secrets from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

NEWS_URL = "https://www.animenewsnetwork.com/"
TELEGRAM_SEND_PHOTO_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"

# Fetch today's news
def fetch_news():
    response = requests.get(NEWS_URL)
    if response.status_code != 200:
        print("❌ Failed to fetch news")
        return []
    
    soup = BeautifulSoup(response.text, "html.parser")
    articles = soup.find_all("div", class_="herald box news")
    
    today = datetime.now().strftime("%b %d")  # Example: "Mar 16"
    news_list = []

    for article in articles:
        date_element = article.find("time")
        if date_element:
            article_date = date_element.text.strip()
            if today not in article_date:  # Skip articles not from today
                continue

        title_element = article.find("h3")
        summary_element = article.find("div", class_="herald box summary")
        image_element = article.find("img")

        if title_element and summary_element and image_element:
            title = title_element.text.strip()
            summary = summary_element.text.strip()
            image_url = image_element["src"]
            
            news_list.append({
                "title": title,
                "summary": summary,
                "image_url": image_url
            })

    return news_list

# Send news to Telegram
def send_news_to_telegram(news_list):
    for item in news_list:
        caption = f"📰 **{item['title']}**\n\n📌 {item['summary']}\n\n⚡ Powered by: @TheAnimeTimes_acn"
        
        payload = {
            "chat_id": CHAT_ID,
            "photo": item["image_url"],
            "caption": caption,
            "parse_mode": "Markdown"
        }

        response = requests.post(TELEGRAM_SEND_PHOTO_URL, data=payload)
        if response.status_code != 200:
            print(f"❌ Telegram post failed: {response.text}")

# Main function
if __name__ == "__main__":
    news_today = fetch_news()
    if news_today:
        send_news_to_telegram(news_today)
        print(f"✅ Posted {len(news_today)} articles.")
    else:
        print("⚠️ No new articles found for today.")
