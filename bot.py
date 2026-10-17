import os
import re
import logging
from datetime import datetime
from collections import defaultdict

import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
from flask import Flask
import threading
# Load environment variables
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID"))
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Expenses")

# Google Sheets Setup
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
client = gspread.authorize(creds)
sheet = client.open(SHEET_NAME).sheet1

# Ensure headers exist
try:
    if not sheet.row_values(1):
        sheet.append_row(["Date", "User ID", "Amount", "Description", "Category"])
except Exception as e:
    logging.error(f"Sheet header error: {e}")

# Simple Category Parser
CATEGORY_MAP = {
    "lunch": "Food", "dinner": "Food", "coffee": "Food", "groceries": "Food", "restaurant": "Food",
    "taxi": "Transport", "uber": "Transport", "bus": "Transport", "train": "Transport", "fuel": "Transport",
    "rent": "Housing", "bill": "Utilities", "electricity": "Utilities", "internet": "Utilities",
    "movie": "Entertainment", "game": "Entertainment", "netflix": "Entertainment",
}

def get_category(description: str) -> str:
    for keyword, category in CATEGORY_MAP.items():
        if keyword in description.lower():
            return category
    return "Other"

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hello! Send me a message like `12.50 lunch` to log an expense.\n\n"
        "Commands:\n"
        "/month - Show this month's total\n"
        "/today - Show today's total"
    )

async def log_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    text = update.message.text.strip()
    
    # Regex expects: [amount] [description]
    match = re.match(r"^(?:(?:rs\.?|inr|₹|\$|€|£)\s*)?(\d+(?:\.\d{1,2})?)\s+(.{1,100})$", text, re.IGNORECASE)

    if not match:
        await update.message.reply_text("❓ Format not recognized. Please use: `[amount] [description]`\nExample: `12.50 lunch`")
        return

    amount_str, description = match.groups()
    amount = float(amount_str)
    category = get_category(description)
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        sheet.append_row([today, update.effective_user.id, amount, description, category])
        await update.message.reply_text(f"✅ Logged: **{description}** for **{amount:.2f}** ({category})", parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Failed to write to sheet: {e}")
        await update.message.reply_text("⚠️ Sorry, I couldn't save that. Please try again.")

async def month_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return

    current_month = datetime.now().strftime("%Y-%m")
    records = sheet.get_all_records()
    totals = defaultdict(float)

    for row in records:
        if str(row.get("User ID")) == str(ALLOWED_USER_ID) and str(row.get("Date", "")).startswith(current_month):
            totals[row.get("Category", "Other")] += float(row.get("Amount", 0))

    if not totals:
        await update.message.reply_text(f"📭 No expenses logged for {current_month} yet.")
        return

    report = f"📊 **Monthly Report ({current_month})**\n"
    for cat, total in sorted(totals.items(), key=lambda x: -x[1]):
        report += f"• {cat}: **{total:.2f}**\n"
    report += f"---\n**Total: {sum(totals.values()):.2f}**"
    await update.message.reply_text(report, parse_mode="Markdown")

# Main Application
# Add this right above def main()
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_bot():
    # This is your existing main() logic
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("month", month_report))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_expense))
    
    logging.info("Bot is polling...")
    application.run_polling()

# Replace your old if __name__ == "__main__": block with this
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Start the Telegram bot in a background thread
    threading.Thread(target=run_bot, daemon=True).start()
    # Start the web server to keep Render happy
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
    main()
