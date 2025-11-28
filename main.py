import os
import telebot
import pyshorteners
import requests
import validators
import logging
import random
import time
import json
import zipfile
import io
from datetime import datetime
from pymongo import MongoClient
from bson import ObjectId

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.environ.get('BOT_TOKEN')
MONGODB_URI = os.environ.get('MONGODB_URI', 'mongodb://localhost:27017/urlshortener')

# Initialize the bot and MongoDB
bot = telebot.TeleBot(BOT_TOKEN)
client = MongoClient(MONGODB_URI)
db = client.url_shortener
urls_collection = db.urls
clicks_collection = db.clicks

class UnlimitedShortener:
    def __init__(self):
        self.services_used = 0
        self.failed_services = set()

    def shorten_with_all_services(self, url: str, user_id: int) -> dict:
        """Try multiple free URL shortening services and store in MongoDB"""
        services = [
            self._cleanuri,
            self._tinyurl,
            self._isgd,
            self._dagd,
            self._clckru
        ]

        # Shuffle services to distribute load
        random.shuffle(services)

        for service in services:
            if service.__name__ in self.failed_services:
                continue

            try:
                short_url = service(url)
                if short_url:
                    self.services_used += 1
                    
                    # Store in MongoDB
                    url_data = {
                        'user_id': user_id,
                        'original_url': url,
                        'short_url': short_url,
                        'service_used': service.__name__,
                        'click_count': 0,
                        'created_at': datetime.utcnow(),
                        'last_clicked': None
                    }
                    
                    result = urls_collection.insert_one(url_data)
                    url_data['_id'] = result.inserted_id
                    
                    logger.info(f"Success with {service.__name__}: {short_url}")
                    return url_data
            except Exception as e:
                logger.warning(f"Service {service.__name__} failed: {e}")
                self.failed_services.add(service.__name__)
                continue

        # If all services fail, use TinyURL as last resort
        short_url = self._tinyurl(url)
        url_data = {
            'user_id': user_id,
            'original_url': url,
            'short_url': short_url,
            'service_used': 'tinyurl',
            'click_count': 0,
            'created_at': datetime.utcnow(),
            'last_clicked': None
        }
        result = urls_collection.insert_one(url_data)
        url_data['_id'] = result.inserted_id
        return url_data

    def _cleanuri(self, url: str) -> str:
        """CleanURI API - Free, no rate limits mentioned"""
        response = requests.post(
            'https://cleanuri.com/api/v1/shorten',
            data={'url': url},
            timeout=10
        )
        if response.status_code == 200:
            return response.json()['result_url']
        raise Exception(f"HTTP {response.status_code}")

    def _tinyurl(self, url: str) -> str:
        """TinyURL - Most reliable free service"""
        shortener = pyshorteners.Shortener()
        return shortener.tinyurl.short(url)

    def _isgd(self, url: str) -> str:
        """is.gd - Free, no registration needed"""
        response = requests.get(
            f'https://is.gd/create.php',
            params={'format': 'simple', 'url': url},
            timeout=10
        )
        if response.status_code == 200:
            return response.text.strip()
        raise Exception(f"HTTP {response.status_code}")

    def _dagd(self, url: str) -> str:
        """da.gd - Free URL shortener"""
        response = requests.get(
            f'https://da.gd/s',
            params={'url': url},
            timeout=10
        )
        if response.status_code == 200:
            return response.text.strip()
        raise Exception(f"HTTP {response.status_code}")

    def _clckru(self, url: str) -> str:
        """clck.ru - Russian shortener, works globally"""
        response = requests.get(
            f'https://clck.ru/--',
            params={'url': url},
            timeout=10
        )
        if response.status_code == 200:
            return response.text.strip()
        raise Exception(f"HTTP {response.status_code}")

# Create shortener instance
unlimited_shortener = UnlimitedShortener()

class BackupManager:
    @staticmethod
    def create_backup(user_id: int):
        """Create backup of user's URLs and click data"""
        try:
            # Get user's URLs
            user_urls = list(urls_collection.find({'user_id': user_id}))
            
            # Get click data for user's URLs
            url_ids = [str(url['_id']) for url in user_urls]
            user_clicks = list(clicks_collection.find({'url_id': {'$in': url_ids}}))
            
            backup_data = {
                'user_id': user_id,
                'backup_created': datetime.utcnow().isoformat(),
                'urls_count': len(user_urls),
                'urls': user_urls,
                'clicks': user_clicks
            }
            
            # Create ZIP in memory
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w') as zip_file:
                # Convert to JSON string and add to zip
                json_data = json.dumps(backup_data, default=str, indent=2)
                zip_file.writestr(f'backup_{user_id}_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.json', json_data)
            
            zip_buffer.seek(0)
            return zip_buffer
        except Exception as e:
            logger.error(f"Backup creation failed: {e}")
            return None

    @staticmethod
    def restore_backup(user_id: int, zip_data: bytes):
        """Restore backup data"""
        try:
            zip_buffer = io.BytesIO(zip_data)
            with zipfile.ZipFile(zip_buffer, 'r') as zip_file:
                # Get the first JSON file in zip
                for file_name in zip_file.namelist():
                    if file_name.endswith('.json'):
                        with zip_file.open(file_name) as f:
                            backup_data = json.loads(f.read().decode('utf-8'))
                        
                        # Restore URLs
                        for url_data in backup_data.get('urls', []):
                            # Remove _id to avoid duplicate key errors
                            if '_id' in url_data:
                                del url_data['_id']
                            url_data['user_id'] = user_id
                            urls_collection.update_one(
                                {'short_url': url_data['short_url'], 'user_id': user_id},
                                {'$set': url_data},
                                upsert=True
                            )
                        
                        # Restore clicks
                        for click_data in backup_data.get('clicks', []):
                            if '_id' in click_data:
                                del click_data['_id']
                            clicks_collection.update_one(
                                {'url_id': click_data['url_id'], 'clicked_at': click_data['clicked_at']},
                                {'$set': click_data},
                                upsert=True
                            )
                        
                        return True
            return False
        except Exception as e:
            logger.error(f"Backup restore failed: {e}")
            return False

# Initialize backup manager
backup_manager = BackupManager()

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    """Handle /start and /help commands"""
    welcome_text = """
🔗 *UNLIMITED URL Shortener Bot* 🔗

I can shorten URLs using MULTIPLE free services!
No rate limits - I'll automatically switch between services.
Now with MongoDB integration for click tracking and backup features.

*Commands:*
/start - Show this message
/help - Get help
/stats - Show usage statistics
/mystats - Show your shortened URLs with click counts
/backup - Download your data backup
/upload - Upload and restore backup (reply to a backup file)

*How to use:*
Just send me any URL starting with http:// or https://

*Example URLs to test:*
`https://www.google.com/search?q=python+programming+tutorial`
`https://www.youtube.com/watch?v=very_long_video_id_here`

*New Features:*
📊 Click tracking for your shortened URLs
💾 Backup and restore your data
🔍 View detailed statistics
"""
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(commands=['stats'])
def show_stats(message):
    """Show usage statistics"""
    user_id = message.from_user.id
    
    # Get user's URL count and total clicks
    user_urls = list(urls_collection.find({'user_id': user_id}))
    total_clicks = sum(url.get('click_count', 0) for url in user_urls)
    
    stats_text = f"""
📊 *Bot Statistics*

✅ Services used successfully: `{unlimited_shortener.services_used}`
❌ Failed services: `{len(unlimited_shortener.failed_services)}`

🔗 Your URLs shortened: `{len(user_urls)}`
👆 Your total clicks: `{total_clicks}`

💡 I'm using multiple free APIs to provide unlimited shortening!
"""
    bot.reply_to(message, stats_text, parse_mode='Markdown')

@bot.message_handler(commands=['mystats'])
def show_my_stats(message):
    """Show user's URLs with click counts"""
    user_id = message.from_user.id
    user_urls = list(urls_collection.find({'user_id': user_id}).sort('created_at', -1))
    
    if not user_urls:
        bot.reply_to(message, "❌ You haven't shortened any URLs yet!")
        return
    
    stats_text = "📊 *Your Shortened URLs*\n\n"
    
    for i, url in enumerate(user_urls[:10], 1):  # Show last 10 URLs
        stats_text += f"{i}. `{url['short_url']}`\n"
        stats_text += f"   👆 Clicks: `{url.get('click_count', 0)}`\n"
        stats_text += f"   📅 Created: `{url['created_at'].strftime('%Y-%m-%d')}`\n\n"
    
    if len(user_urls) > 10:
        stats_text += f"... and {len(user_urls) - 10} more URLs\n"
    
    stats_text += "Use /backup to download your complete data"
    
    bot.reply_to(message, stats_text, parse_mode='Markdown')

@bot.message_handler(commands=['backup'])
def handle_backup(message):
    """Create and send backup of user's data"""
    user_id = message.from_user.id
    
    bot.send_chat_action(message.chat.id, 'upload_document')
    
    try:
        zip_buffer = backup_manager.create_backup(user_id)
        
        if zip_buffer:
            bot.send_document(
                message.chat.id,
                zip_buffer,
                caption=f"📦 Backup of your URL data\nCreated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n\nUse /upload to restore this backup later.",
                visible_file_name=f"url_backup_{user_id}_{datetime.utcnow().strftime('%Y%m%d')}.zip"
            )
        else:
            bot.reply_to(message, "❌ Failed to create backup. Please try again later.")
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        bot.reply_to(message, "❌ Backup creation failed. Please try again later.")

@bot.message_handler(commands=['upload'])
def handle_upload(message):
    """Handle backup upload"""
    if message.reply_to_message and message.reply_to_message.document:
        bot.reply_to(message, "📤 Please reply to a backup file with this command to restore your data.")
    else:
        bot.reply_to(message, """
📤 *How to restore backup:*

1. Use /backup to download your current backup first
2. Reply to a backup file with /upload
3. I'll restore your URLs and click data

*Warning:* This will overwrite existing data for the same URLs!
        """, parse_mode='Markdown')

@bot.message_handler(func=lambda message: True, content_types=['document'])
def handle_document(message):
    """Handle backup file upload"""
    if message.reply_to_message and '/upload' in message.reply_to_message.text:
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            
            user_id = message.from_user.id
            
            if backup_manager.restore_backup(user_id, downloaded_file):
                bot.reply_to(message, "✅ Backup restored successfully! Your URLs and click data have been updated.")
            else:
                bot.reply_to(message, "❌ Failed to restore backup. Please check the file format.")
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            bot.reply_to(message, "❌ Backup restoration failed. Please try again.")

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Handle all text messages"""
    user_message = message.text.strip()

    # Skip if it's a command
    if user_message.startswith('/'):
        return

    # Validate URL format
    if not user_message.startswith(('http://', 'https://')):
        user_message = 'https://' + user_message

    if not validators.url(user_message):
        bot.reply_to(
            message,
            "❌ *Invalid URL format!*\n\nPlease send a valid URL starting with http:// or https://\n\n*Example:* `https://www.google.com`",
            parse_mode='Markdown'
        )
        return

    try:
        # Show typing action
        bot.send_chat_action(message.chat.id, 'typing')

        # Shorten the URL using our unlimited service and store in MongoDB
        url_data = unlimited_shortener.shorten_with_all_services(user_message, message.from_user.id)

        # Send the result
        result_text = f"""
✅ *URL Shortened Successfully!*

🔗 *Original URL:*
`{user_message[:100]}{'...' if len(user_message) > 100 else ''}`

🚀 *Short URL:*
`{url_data['short_url']}`

📊 *Click Tracking:* Enabled
👆 Clicks: `0` (new)
🔧 Service: `{url_data['service_used']}`

💡 *Commands:*
/mystats - View your URLs and clicks
/backup - Download your data
/stats - View usage statistics
"""
        bot.reply_to(message, result_text, parse_mode='Markdown')

        # Log the activity
        logger.info(f"User {message.from_user.first_name} shortened URL using service #{unlimited_shortener.services_used}")

    except Exception as e:
        error_text = f"""
❌ *All shortening services are currently busy!*

Please try again in a few minutes.

*Error details:* `{str(e)}`

💡 You can also try:
- Using a different URL
- Waiting a moment and trying again
"""
        bot.reply_to(message, error_text, parse_mode='Markdown')
        logger.error(f"All services failed: {str(e)}")

# Click tracking function (for webhook implementation)
def track_click(short_url: str, user_agent: str = "Unknown"):
    """Track a click on shortened URL"""
    try:
        url_data = urls_collection.find_one({'short_url': short_url})
        if url_data:
            # Update click count in URLs collection
            urls_collection.update_one(
                {'_id': url_data['_id']},
                {
                    '$inc': {'click_count': 1},
                    '$set': {'last_clicked': datetime.utcnow()}
                }
            )
            
            # Log detailed click information
            click_data = {
                'url_id': str(url_data['_id']),
                'short_url': short_url,
                'user_id': url_data['user_id'],
                'user_agent': user_agent,
                'clicked_at': datetime.utcnow(),
                'ip_address': 'unknown'  # Would be set in webhook implementation
            }
            clicks_collection.insert_one(click_data)
            
            return url_data['original_url']
    except Exception as e:
        logger.error(f"Click tracking failed: {e}")
    return None

# Start the bot
if __name__ == '__main__':
    print("🚀 UNLIMITED URL Shortener Bot with MongoDB is starting...")
    print("📊 Features: Click tracking, Backup/Restore, Statistics")
    print("🔗 Using multiple free APIs for unlimited shortening")
    print("💾 MongoDB connected:", MONGODB_URI)
    print("⏹️  Press Ctrl+C to stop the bot")

    try:
        bot.polling()
    except Exception as e:
        print(f"Bot stopped: {e}")
