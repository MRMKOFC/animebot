import requests
from bs4 import BeautifulSoup
from datetime import datetime
import json
import os

# Telegram Bot Token & Chat ID
BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"

# ANN Website URL
ANN_URL = "https://www.animenewsnetwork.com/"

# JSON file to track posted titles
POSTED_TITLES_FILE = "posted_titles.json"

# Ensure JSON file exists
if not os.path.exists(POSTED_TITLES_FILE):
    with open(POSTED_TITLES_FILE, "w") as f:
        json.dump([], f)

# Load previously posted titles
with open(POSTED_TITLES_FILE, "r") as f:
    posted_titles = json.load(f)

# Get today's date in ANN format
today = datetime.now().strftime("%B %d")  # Example: "March 16"

# Fetch the webpage
response = requests.get(ANN_URL)
soup = BeautifulSoup(response.text, "html.parser")

# Find articles
articles = soup.find_all("div", class_="herald box news")  # Update selector if needed

new_articles = []

for article in articles:
    # Extract title
    title_element = article.find("h3")
    title = title_element.text.strip() if title_element else "No Title"

    # Extract summary
    summary_element = article.find("div", class_="preview")
    summary = summary_element.text.strip() if summary_element else "No summary available."

    # Extract image (if available)
    img_element = article.find("img")
    img_url = img_element["src"] if img_element else None

    # Extract date
    date_element = article.find("time")
    article_date = date_element.text.strip() if date_element else "Unknown Date"

    # Debug: Print extracted date
    print(f"Extracted Date: {article_date} | Today's Date: {today}")

    # Check if the article is from today and not posted before
    if today in article_date and title not in posted_titles:
        new_articles.append(title)
        
        # Format message with emojis
        message = f"📰 *{title}*\n📅 {article_date}\n\n📖 {summary}\n🔗 [Read more]({ANN_URL})"

        # Send message to Telegram
        if img_url:
            telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            payload = {
                "chat_id": CHAT_ID,
                "photo": img_url,
                "caption": message,
                "parse_mode": "Markdown"
            }
        else:
            telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "Markdown"
            }

        response = requests.post(telegram_url, data=payload)

        # Debugging response
        print(f"Telegram Response: {response.json()}")

        # Add title to posted list
        posted_titles.append(title)

# Save updated posted titles
with open(POSTED_TITLES_FILE, "w") as f:
    json.dump(posted_titles, f)

# Final log message
if new_articles:
    print(f"✅ Posted {len(new_articles)} new articles.")
else:
    print("⚠️ No new articles found for today.")
