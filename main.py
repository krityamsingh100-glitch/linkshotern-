import os
import pyshorteners
import validators
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Your bot's functionality
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Hello! Send me a URL and I will shorten it for you.')

async def shorten_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = update.message.text
    # Validate the URL
    if not validators.url(url):
        await update.message.reply_text("❌ Please send a valid URL (including http:// or https://).")
        return
    try:
        # Shorten the URL
        shortener = pyshorteners.Shortener()
        short_url = shortener.tinyurl.short(url)
        await update.message.reply_text(f"✅ Here is your shortened URL:\n{short_url}")
    except Exception as e:
        await update.message.reply_text(f"❌ An error occurred: {e}")

# Set up the bot
def main():
    token = os.environ.get('BOT_TOKEN')  # Get token from environment variable
    app = ApplicationBuilder().token(token).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, shorten_url))
    
    app.run_polling()

if __name__ == '__main__':
    main()
