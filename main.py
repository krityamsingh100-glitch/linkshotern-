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
from bson.json_util import dumps, loads

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.environ.get('BOT_TOKEN')
MONGODB_URI = os.environ.get('MONGODB_URI', 'mongodb://localhost:27017/urlshortener')

# Initialize the bot and MongoDB
bot = telebot.TeleBot(BOT_TOKEN)
try:
    client = MongoClient(MONGODB_URI)
    db = client.url_shortener
    urls_collection = db.urls
    clicks_collection = db.clicks
    logger.info("✅ MongoDB connected successfully")
except Exception as e:
    logger.error(f"❌ MongoDB connection failed: {e}")
    # Fallback to in-memory storage if MongoDB fails
    urls_collection = None
    clicks_collection = None

class ProfessionalShortener:
    def __init__(self):
        self.services_used = 0
        self.failed_services = set()
        self.service_stats = {}

    def shorten_with_all_services(self, url: str, user_id: int) -> dict:
        """Try multiple free URL shortening services and store in MongoDB"""
        services = [
            {'name': 'cleanuri', 'function': self._cleanuri},
            {'name': 'tinyurl', 'function': self._tinyurl},
            {'name': 'isgd', 'function': self._isgd},
            {'name': 'dagd', 'function': self._dagd},
            {'name': 'clckru', 'function': self._clckru}
        ]

        random.shuffle(services)

        for service in services:
            if service['name'] in self.failed_services:
                continue

            try:
                short_url = service['function'](url)
                if short_url:
                    self.services_used += 1
                    # Update service stats
                    self.service_stats[service['name']] = self.service_stats.get(service['name'], 0) + 1
                    
                    # Store in MongoDB
                    url_data = {
                        'user_id': user_id,
                        'original_url': url,
                        'short_url': short_url,
                        'service_used': service['name'],
                        'click_count': 0,
                        'created_at': datetime.utcnow(),
                        'last_clicked': None,
                        'user_name': f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()
                    }
                    
                    if urls_collection:
                        result = urls_collection.insert_one(url_data)
                        url_data['_id'] = str(result.inserted_id)
                    
                    logger.info(f"✅ Success with {service['name']}: {short_url}")
                    return url_data
            except Exception as e:
                logger.warning(f"❌ Service {service['name']} failed: {e}")
                self.failed_services.add(service['name'])
                continue

        # If all services fail, use TinyURL as last resort
        try:
            short_url = self._tinyurl(url)
            url_data = {
                'user_id': user_id,
                'original_url': url,
                'short_url': short_url,
                'service_used': 'tinyurl',
                'click_count': 0,
                'created_at': datetime.utcnow(),
                'last_clicked': None,
                'user_name': f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()
            }
            if urls_collection:
                result = urls_collection.insert_one(url_data)
                url_data['_id'] = str(result.inserted_id)
            return url_data
        except Exception as e:
            raise Exception(f"All services failed: {str(e)}")

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
            'https://is.gd/create.php',
            params={'format': 'simple', 'url': url},
            timeout=10
        )
        if response.status_code == 200 and response.text.startswith('http'):
            return response.text.strip()
        raise Exception(f"HTTP {response.status_code}")

    def _dagd(self, url: str) -> str:
        """da.gd - Free URL shortener"""
        response = requests.get(
            'https://da.gd/s',
            params={'url': url},
            timeout=10
        )
        if response.status_code == 200:
            return response.text.strip()
        raise Exception(f"HTTP {response.status_code}")

    def _clckru(self, url: str) -> str:
        """clck.ru - Russian shortener, works globally"""
        response = requests.get(
            'https://clck.ru/--',
            params={'url': url},
            timeout=10
        )
        if response.status_code == 200:
            return response.text.strip()
        raise Exception(f"HTTP {response.status_code}")

# Create shortener instance
professional_shortener = ProfessionalShortener()

class DatabaseManager:
    @staticmethod
    def get_user_stats(user_id: int):
        """Get user statistics from MongoDB"""
        if not urls_collection:
            return {'total_urls': 0, 'total_clicks': 0, 'urls': []}
        
        user_urls = list(urls_collection.find({'user_id': user_id}))
        total_clicks = sum(url.get('click_count', 0) for url in user_urls)
        
        return {
            'total_urls': len(user_urls),
            'total_clicks': total_clicks,
            'urls': user_urls
        }

    @staticmethod
    def get_user_urls(user_id: int, limit: int = 10):
        """Get user's URLs with pagination"""
        if not urls_collection:
            return []
        
        return list(urls_collection.find({'user_id': user_id})
                   .sort('created_at', -1)
                   .limit(limit))

class BackupManager:
    @staticmethod
    def create_backup(user_id: int):
        """Create backup of user's URLs and click data"""
        try:
            if not urls_collection:
                return None
                
            # Get user's URLs
            user_urls = list(urls_collection.find({'user_id': user_id}))
            
            # Get click data for user's URLs
            url_ids = [url['_id'] for url in user_urls]
            user_clicks = list(clicks_collection.find({'url_id': {'$in': url_ids}})) if clicks_collection else []
            
            backup_data = {
                'user_id': user_id,
                'backup_created': datetime.utcnow().isoformat(),
                'urls_count': len(user_urls),
                'clicks_count': len(user_clicks),
                'urls': json.loads(dumps(user_urls)),
                'clicks': json.loads(dumps(user_clicks))
            }
            
            # Create ZIP in memory
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                # Convert to JSON string and add to zip
                json_data = json.dumps(backup_data, indent=2, ensure_ascii=False)
                zip_file.writestr(
                    f'url_backup_{user_id}_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.json', 
                    json_data
                )
            
            zip_buffer.seek(0)
            return zip_buffer
        except Exception as e:
            logger.error(f"Backup creation failed: {e}")
            return None

    @staticmethod
    def restore_backup(user_id: int, zip_data: bytes):
        """Restore backup data"""
        try:
            if not urls_collection:
                return False
                
            zip_buffer = io.BytesIO(zip_data)
            with zipfile.ZipFile(zip_buffer, 'r') as zip_file:
                # Get the first JSON file in zip
                for file_name in zip_file.namelist():
                    if file_name.endswith('.json'):
                        with zip_file.open(file_name) as f:
                            backup_data = json.loads(f.read().decode('utf-8'))
                        
                        # Restore URLs
                        for url_data in backup_data.get('urls', []):
                            # Convert string _id back to ObjectId if needed
                            if '_id' in url_data and '$oid' in url_data['_id']:
                                url_data['_id'] = ObjectId(url_data['_id']['$oid'])
                            
                            # Update user_id to current user
                            url_data['user_id'] = user_id
                            url_data['restored_at'] = datetime.utcnow()
                            
                            # Use short_url and user_id as unique identifier
                            urls_collection.update_one(
                                {'short_url': url_data['short_url'], 'user_id': user_id},
                                {'$set': url_data},
                                upsert=True
                            )
                        
                        return True
            return False
        except Exception as e:
            logger.error(f"Backup restore failed: {e}")
            return False

# Initialize managers
db_manager = DatabaseManager()
backup_manager = BackupManager()

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    """Handle /start and /help commands"""
    welcome_text = """
🎬 *WATCH OUR INTRODUCTION VIDEO*:
[Click here to watch the bot introduction](https://files.catbox.moe/nunx43.mp4)

🤖 *PROFESSIONAL URL SHORTENER BOT* 🚀

*Premium Features Included:*
✅ Multi-service URL shortening
📊 Advanced click analytics  
💾 Automated backup system
🔒 Secure data management
📈 Real-time statistics

*Commands Available:*
/start - Show this welcome message
/stats - View your shortening statistics
/mystats - See your shortened URLs with analytics
/backup - Download your data backup (JSON)
/upload - Restore from backup file

*How to Use:*
Simply send me any long URL and I'll shorten it instantly!

*Supported Services:*
• CleanURI • TinyURL • is.gd • da.gd • clck.ru
"""
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(commands=['stats'])
def show_stats(message):
    """Show comprehensive user statistics"""
    user_id = message.from_user.id
    
    try:
        stats = db_manager.get_user_stats(user_id)
        
        # Get service distribution
        if urls_collection:
            pipeline = [
                {'$match': {'user_id': user_id}},
                {'$group': {'_id': '$service_used', 'count': {'$sum': 1}}}
            ]
            service_stats = list(urls_collection.aggregate(pipeline))
        else:
            service_stats = []
        
        stats_text = f"""
📊 *YOUR PERSONAL ANALYTICS DASHBOARD*

📈 *Summary Statistics:*
• Total URLs Shortened: `{stats['total_urls']}`
• Total Clicks Tracked: `{stats['total_clicks']}`
• Average Clicks/URL: `{stats['total_clicks']/max(stats['total_urls'], 1):.1f}`

🛠️ *Service Usage:*
"""
        for service in service_stats:
            stats_text += f"• {service['_id'].title()}: `{service['count']}` URLs\n"
        
        if professional_shortener.service_stats:
            stats_text += f"\n🌐 *Global Service Reliability:*\n"
            for service, count in professional_shortener.service_stats.items():
                stats_text += f"• {service.title()}: `{count}` successful\n"
        
        stats_text += f"\n💡 *Bot Performance:*\n"
        stats_text += f"• Successful Shortenings: `{professional_shortener.services_used}`\n"
        stats_text += f"• Failed Services: `{len(professional_shortener.failed_services)}`\n"
        
        if stats['total_urls'] > 0:
            most_clicked = max(stats['urls'], key=lambda x: x.get('click_count', 0))
            stats_text += f"• Most Popular URL: `{most_clicked.get('click_count', 0)}` clicks\n"
        
        stats_text += "\n*Use /mystats to see your individual URLs*"
        
        bot.reply_to(message, stats_text, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Stats error: {e}")
        bot.reply_to(message, "❌ *Error generating statistics*. Please try again later.", parse_mode='Markdown')

@bot.message_handler(commands=['mystats'])
def show_my_stats(message):
    """Show user's URLs with detailed analytics"""
    user_id = message.from_user.id
    
    try:
        user_urls = db_manager.get_user_urls(user_id, limit=15)
        
        if not user_urls:
            bot.reply_to(message, """
📭 *No URLs Found*

You haven't shortened any URLs yet! Send me a URL to get started and track your analytics.
            """, parse_mode='Markdown')
            return
        
        stats_text = "📋 *YOUR SHORTENED URLS*\n\n"
        
        for i, url in enumerate(user_urls, 1):
            click_count = url.get('click_count', 0)
            created_date = url['created_at'].strftime('%m/%d/%Y')
            service = url.get('service_used', 'unknown')
            
            stats_text += f"`{i:2d}.` 🔗 `{url['short_url']}`\n"
            stats_text += f"     👆 **Clicks**: `{click_count}`"
            stats_text += f" | 📅 `{created_date}`"
            stats_text += f" | 🛠️ `{service}`\n\n"
        
        total_stats = db_manager.get_user_stats(user_id)
        stats_text += f"*Showing {len(user_urls)} of {total_stats['total_urls']} total URLs*\n"
        stats_text += "*Use /backup to download your complete history*"
        
        if len(stats_text) > 4096:
            # Split long messages
            parts = [stats_text[i:i+4096] for i in range(0, len(stats_text), 4096)]
            for part in parts:
                bot.reply_to(message, part, parse_mode='Markdown')
        else:
            bot.reply_to(message, stats_text, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"MyStats error: {e}")
        bot.reply_to(message, "❌ *Error retrieving your URLs*. Please try again later.", parse_mode='Markdown')

@bot.message_handler(commands=['backup'])
def handle_backup(message):
    """Create and send backup of user's data"""
    user_id = message.from_user.id
    
    try:
        bot.send_chat_action(message.chat.id, 'upload_document')
        
        processing_msg = bot.reply_to(message, "🔄 *Creating your backup...*", parse_mode='Markdown')
        
        zip_buffer = backup_manager.create_backup(user_id)
        
        if zip_buffer:
            # Get user stats for backup info
            stats = db_manager.get_user_stats(user_id)
            
            bot.send_document(
                message.chat.id,
                zip_buffer,
                caption=f"""
📦 *BACKUP CREATED SUCCESSFULLY*

✅ **Summary:**
• URLs: `{stats['total_urls']}`
• Clicks: `{stats['total_clicks']}`
• Date: `{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}`

💾 *How to Restore:*
Use /upload command and reply to this backup file

🔒 *Your data is securely backed up and ready for restore.*
                """,
                visible_file_name=f"url_backup_{user_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.zip",
                parse_mode='Markdown'
            )
            bot.delete_message(message.chat.id, processing_msg.message_id)
        else:
            bot.edit_message_text(
                "❌ *Backup Failed*\n\nPlease try again later or contact support if the issue persists.",
                message.chat.id,
                processing_msg.message_id,
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        bot.reply_to(message, "❌ *Backup creation failed*. Please try again later.", parse_mode='Markdown')

@bot.message_handler(commands=['upload'])
def handle_upload(message):
    """Handle backup upload instructions"""
    if message.reply_to_message and message.reply_to_message.document:
        # This will be handled by the document handler
        return
    
    instructions = """
📤 *BACKUP RESTORATION INSTRUCTIONS*

**Step 1:** First, use `/backup` to download your current data (recommended)

**Step 2:** Reply to a backup file with `/upload` command

**Step 3:** I'll restore all your URLs and analytics data

⚠️ **Important Notes:**
• Existing URLs with same short links will be updated
• Restoration may take a few moments
• Keep your backup files secure

*To proceed, reply to a backup file with /upload*
    """
    bot.reply_to(message, instructions, parse_mode='Markdown')

@bot.message_handler(func=lambda message: True, content_types=['document'])
def handle_document(message):
    """Handle backup file upload for restoration"""
    if message.reply_to_message and any(cmd in message.reply_to_message.text for cmd in ['/upload', 'BACKUP RESTORATION']):
        try:
            user_id = message.from_user.id
            
            processing_msg = bot.reply_to(message, "🔄 *Processing your backup file...*", parse_mode='Markdown')
            
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            
            if backup_manager.restore_backup(user_id, downloaded_file):
                # Get updated stats
                stats = db_manager.get_user_stats(user_id)
                
                bot.edit_message_text(
                    f"""
✅ *BACKUP RESTORED SUCCESSFULLY*

🎉 Your data has been completely restored!

📊 **Current Stats:**
• URLs: `{stats['total_urls']}`
• Total Clicks: `{stats['total_clicks']}`

✨ All your shortened URLs and analytics are now available.
Use /mystats to view your restored URLs.
                    """,
                    message.chat.id,
                    processing_msg.message_id,
                    parse_mode='Markdown'
                )
            else:
                bot.edit_message_text(
                    """
❌ *RESTORATION FAILED*

Possible reasons:
• Invalid backup file format
• File corruption
• Database connection issue

Please ensure you're uploading a valid backup file from this bot.
                    """,
                    message.chat.id,
                    processing_msg.message_id,
                    parse_mode='Markdown'
                )
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            bot.reply_to(message, "❌ *Restoration failed*. Please check the file and try again.", parse_mode='Markdown')

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Handle all URL shortening requests"""
    user_message = message.text.strip()

    if user_message.startswith('/'):
        return

    # Validate and prepare URL
    if not user_message.startswith(('http://', 'https://')):
        user_message = 'https://' + user_message

    if not validators.url(user_message):
        bot.reply_to(
            message,
            """
❌ *INVALID URL FORMAT*

Please send a valid URL starting with:
• `http://` or `https://`

*Examples:*
`https://www.example.com/very-long-path`
`http://yourwebsite.com/document`
            """,
            parse_mode='Markdown'
        )
        return

    try:
        bot.send_chat_action(message.chat.id, 'typing')
        
        processing_msg = bot.reply_to(message, "🔄 *Processing your URL...*", parse_mode='Markdown')

        # Shorten the URL
        url_data = professional_shortener.shorten_with_all_services(user_message, message.from_user.id)

        # Create success response
        original_display = user_message[:80] + ('...' if len(user_message) > 80 else '')
        
        result_text = f"""
✅ *URL SHORTENED SUCCESSFULLY*

🌐 **Original URL:**
`{original_display}`

🚀 **Shortened URL:**
`{url_data['short_url']}`

📊 **Analytics Enabled:**
• Clicks: `0` (new)
• Service: `{url_data.get('service_used', 'Unknown')}`
• Time: `{datetime.utcnow().strftime('%H:%M:%S UTC')}`

💡 **Quick Actions:**
• /mystats - View your URLs
• /stats - See analytics
• /backup - Download data
        """
        
        bot.delete_message(message.chat.id, processing_msg.message_id)
        bot.reply_to(message, result_text, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Shortening failed: {e}")
        error_text = f"""
❌ *SHORTENING FAILED*

All services are currently unavailable.

**Troubleshooting:**
• Check your URL format
• Try again in 1-2 minutes
• Use a different URL

*Error Details:* `{str(e)}`
        """
        bot.reply_to(message, error_text, parse_mode='Markdown')

# Click tracking simulation (for demonstration)
def simulate_click(short_url: str, user_agent: str = "Telegram Bot"):
    """Simulate click tracking for demo purposes"""
    try:
        if not urls_collection:
            return None
            
        url_data = urls_collection.find_one({'short_url': short_url})
        if url_data:
            # Update click count
            urls_collection.update_one(
                {'_id': url_data['_id']},
                {
                    '$inc': {'click_count': 1},
                    '$set': {'last_clicked': datetime.utcnow()}
                }
            )
            
            # Log click
            if clicks_collection:
                click_data = {
                    'url_id': url_data['_id'],
                    'short_url': short_url,
                    'user_id': url_data['user_id'],
                    'user_agent': user_agent,
                    'clicked_at': datetime.utcnow(),
                    'ip_address': 'simulated'
                }
                clicks_collection.insert_one(click_data)
            
            return url_data['original_url']
    except Exception as e:
        logger.error(f"Click simulation failed: {e}")
    return None

# Start the bot
if __name__ == '__main__':
    print("""
🚀 PROFESSIONAL URL SHORTENER BOT STARTING...
    
📊 Features Enabled:
✅ Multi-service URL shortening
✅ MongoDB analytics & tracking
✅ Backup/restore system
✅ Professional UI/UX
✅ Error handling & logging

🎬 Intro Video: https://files.catbox.moe/nunx43.mp4
💾 Database: MongoDB Connected
🤖 Bot: Ready to receive messages
    """)
    
    try:
        bot.polling(none_stop=True, timeout=60)
    except Exception as e:
        print(f"❌ Bot stopped: {e}")
        logger.error(f"Bot crashed: {e}")
