import os
import re
import json
import logging
import threading
from datetime import datetime
from collections import defaultdict

import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
from flask import Flask

# Load environment variables (for local testing)
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID"))
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Expenses")

# --- Google Sheets Setup ---
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds_path = "/etc/secrets/google-credentials.json"
if os.path.exists(creds_path):
    logging.info("Loading credentials from Render Secret File...")
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
elif os.getenv("GOOGLE_CREDENTIALS"):
    logging.info("Loading credentials from Environment Variable...")
    creds_dict = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
else:
    logging.info("Loading credentials from local credentials.json file...")
    creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)

client = gspread.authorize(creds)
sheet = client.open(SHEET_NAME).sheet1

# Ensure headers exist
try:
    if not sheet.row_values(1):
        sheet.append_row(["Date", "User ID", "Amount", "Description", "Category"])
except Exception as e:
    logging.error(f"Sheet header error: {e}")

# --- Expanded Category Parser ---
CATEGORY_MAP = {
    # Food
    "food": "Food", "cafe": "Food", "coffee": "Food", "lunch": "Food", "dinner": "Food", 
    "groceries": "Food", "restaurant": "Food", "snack": "Food", "bakery": "Food",
    # Transport
    "transport": "Transport", "bus": "Transport", "taxi": "Transport", "uber": "Transport", 
    "train": "Transport", "fuel": "Transport", "gas": "Transport", "metro": "Transport",
    # Entertainment
    "entertainment": "Entertainment", "movie": "Entertainment", "game": "Entertainment", 
    "netflix": "Entertainment", "spotify": "Entertainment", "concert": "Entertainment", "drink": "Entertainment"
}

def get_category(description: str) -> str:
    desc_lower = description.lower()
    
    # Check for Income keywords first
    if "income" in desc_lower or "salary" in desc_lower or "paycheck" in desc_lower:
        return "Income"
        
    # Check the map
    for keyword, category in CATEGORY_MAP.items():
        if keyword in desc_lower:
            return category
            
    return "Other"

# --- Telegram Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Welcome to your Expense Tracker!**\n\n"
        "📝 **How to log a transaction:**\n"
        "Just type the `amount` followed by a `description`.\n\n"
        "**Examples:**\n"
        "• `2000 income` (Logs as Income)\n"
        "• `300 cafe` (Logs as Food)\n"
        "• `2400 bus` (Logs as Transport)\n"
        "• `100 impresion` (Logs as Other)\n\n"
        "🏷️ **Available Categories:**\n"
        "• **Food** (cafe, lunch, groceries)\n"
        "• **Transport** (bus, taxi, fuel)\n"
        "• **Entertainment** (movie, game, netflix)\n"
        "• **Other** (anything else)\n"
        "• **Income** (salary, income)\n\n"
        "🛠 **Commands:**\n"
        "/month - Show this month's summary\n"
        "/today - Show today's summary\n"
        "/undo - Delete the last entry",
        parse_mode="Markdown"
    )

async def log_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        
        # Reply differently based on whether it's Income or Expense
        if category == "Income":
            await update.message.reply_text(f"💰 Logged Income: **{description}** for **{amount:.2f}**", parse_mode="Markdown")
        else:
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
        await update.message.reply_text(f"📭 No transactions logged for {current_month} yet.")
        return

    # Separate income from expenses
    income = totals.pop("Income", 0.0)
    total_expenses = sum(totals.values())

    report = f"📊 **Monthly Report ({current_month})**\n\n"
    report += f"🟢 Income: **{income:.2f}**\n"
    report += f"🔴 Expenses: **{total_expenses:.2f}**\n"
    
    if totals:
        report += "\n**Expense Breakdown:**\n"
        for cat, total in sorted(totals.items(), key=lambda x: -x[1]):
            report += f"• {cat}: {total:.2f}\n"
            
    report += f"\n---\n💵 **Net: {income - total_expenses:.2f}**"
    await update.message.reply_text(report, parse_mode="Markdown")

async def today_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return

    today_date = datetime.now().strftime("%Y-%m-%d")
    records = sheet.get_all_records()
    
    total_expense = 0.0
    total_income = 0.0

    for row in records:
        if str(row.get("User ID")) == str(ALLOWED_USER_ID) and str(row.get("Date")) == today_date:
            amount = float(row.get("Amount", 0))
            if row.get("Category") == "Income":
                total_income += amount
            else:
                total_expense += amount

    if total_expense == 0 and total_income == 0:
        await update.message.reply_text("No transactions today yet.")
        return

    report = f"📅 **Today's Summary ({today_date})**\n"
    report += f"🟢 Income: **{total_income:.2f}**\n"
    report += f"🔴 Expenses: **{total_expense:.2f}**\n"
    report += f"---\n**Net: {total_income - total_expense:.2f}**"
    
    await update.message.reply_text(report, parse_mode="Markdown")

async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return

    try:
        rows = len(sheet.get_all_values())
        if rows <= 1:
            await update.message.reply_text("Nothing to undo! Your sheet is empty.")
            return
        
        sheet.delete_rows(rows)
        await update.message.reply_text("↩️ Last entry has been deleted.")
    except Exception as e:
        logging.error(f"Failed to undo: {e}")
        await update.message.reply_text("⚠️ Failed to delete the last row.")


# --- Web Server & Bot Threading (Render Trick) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_bot():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("month", month_report))
    application.add_handler(CommandHandler("today", today_report))
    application.add_handler(CommandHandler("undo", undo))
    # Renamed to log_transaction to reflect that it handles income too
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_transaction))
    
    logging.info("Bot is polling...")
    application.run_polling()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
