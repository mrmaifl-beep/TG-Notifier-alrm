import asyncio
import os
import json
import logging
import platform
import subprocess
from collections import deque
from datetime import datetime
from aiohttp import web
from telethon import TelegramClient, events

# Logging configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

CONFIG_FILE = 'config.json'
last_messages = deque(maxlen=20)
client = None
tg_connected = False

# Load configuration
def load_config():
    if not os.path.exists(CONFIG_FILE):
        logging.error(f"File {CONFIG_FILE} not found! Create it based on the example.")
        return {}
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_config(config_data):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, indent=4, ensure_ascii=False)

config = load_config()

# Cross-platform sound playback
def play_sound(filename):
    if not os.path.exists(filename):
        logging.warning(f"Sound file {filename} not found!")
        return
    
    system = platform.system()
    try:
        if system == "Windows":
            import winsound
            winsound.PlaySound(filename, winsound.SND_FILENAME | winsound.SND_ASYNC)
        elif system == "Linux":
            # Using aplay for Raspberry Pi / Linux
            subprocess.Popen(['aplay', '-q', filename])
        else:
            logging.warning(f"Automatic playback on OS {system} is not supported.")
    except Exception as e:
        logging.error(f"Error playing sound: {e}")

# Universal incoming message handler
async def telegram_message_handler(event):
    global last_messages, config, client
    try:
        chat = await event.get_chat()
        username = getattr(chat, 'username', None)
        
        if username:
            username_formatted = f"@{username}".lower()
            active_channels = [c.lower().strip() for c in config.get('channels', [])]
            
            if username_formatted in active_channels:
                text = event.message.text or "[Media or system message]"
                logging.info(f"New message in {username_formatted}: {text[:50]}")
                
                # Add to history for the web panel
                last_messages.appendleft({
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "channel": f"@{username}",
                    "text": text[:150]
                })
                
                # Play sound
                if config.get('sound_enabled', True):
                    play_sound(config.get('sound_file', 'notification.wav'))
                
                # Send Direct Messages (DM)
                dm_users = config.get('dm_users', [])
                dm_text = config.get('dm_message', '').strip()
                
                if dm_users and dm_text and client:
                    for target_user in dm_users:
                        try:
                            # Forward the custom message to the user
                            await client.send_message(target_user, f"{dm_text}\n\n[Trigger: {username_formatted}]")
                            logging.info(f"Sent DM to {target_user}")
                        except Exception as dm_err:
                            logging.error(f"Failed to send DM to {target_user}: {dm_err}")
                            
    except Exception as e:
        logging.error(f"Error processing Telegram message: {e}")

# Function to initialize and start the Telegram client
async def run_telegram_client():
    global client, tg_connected, config
    while True:
        try:
            logging.info("Starting Telegram client...")
            
            # Check for proxy
            if config.get('proxy_enabled') and config.get('proxy_ip'):
                from telethon.network.connection import ConnectionTcpMTProxyRandomizedIntermediate
                proxy_settings = (
                    config['proxy_ip'], 
                    int(config['proxy_port']), 
                    config['proxy_secret']
                )
                client = TelegramClient(
                    'pi_session', 
                    config['api_id'], 
                    config['api_hash'],
                    connection=ConnectionTcpMTProxyRandomizedIntermediate,
                    proxy=proxy_settings
                )
            else:
                client = TelegramClient('pi_session', config['api_id'], config['api_hash'])
            
            # Register event handler
            client.add_event_handler(telegram_message_handler, events.NewMessage())
            
            await client.start()
            tg_connected = True
            logging.info("Telegram client started successfully and is listening to channels.")
            
            # Wait for disconnection (blocking call)
            await client.run_until_disconnected()
        except Exception as e:
            tg_connected = False
            logging.error(f"Critical error in Telegram client: {e}. Restarting in 10 seconds...")
            await asyncio.sleep(10)
        finally:
            tg_connected = False

# WEB SERVER API
async def handle_index(request):
    try:
        with open('index.html', 'r', encoding='utf-8') as f:
            return web.Response(text=f.read(), content_type='text/html')
    except Exception as e:
        return web.Response(text=f"Error loading index.html: {e}", status=500)

async def handle_status(request):
    global config, last_messages, tg_connected
    # Scan available .wav files in the directory
    sounds = [f for f in os.listdir('.') if f.endswith('.wav')]
    if not sounds:
        sounds = ['notification.wav'] # Default fallback if folder is empty
        
    status_data = {
        "tg_connected": tg_connected,
        "config": {
            "sound_enabled": config.get('sound_enabled', True),
            "sound_file": config.get('sound_file', 'notification.wav'),
            "channels": config.get('channels', []),
            "dm_users": config.get('dm_users', []),
            "dm_message": config.get('dm_message', ''),
            "proxy_enabled": config.get('proxy_enabled', False),
            "proxy_ip": config.get('proxy_ip', ''),
            "proxy_port": config.get('proxy_port', 443),
            "proxy_secret": config.get('proxy_secret', '')
        },
        "sounds": sounds,
        "messages": list(last_messages)
    }
    return web.json_response(status_data)

async def handle_save_settings(request):
    global config, client
    try:
        new_settings = await request.json()
        
        proxy_changed = (
            new_settings.get('proxy_enabled') != config.get('proxy_enabled') or
            new_settings.get('proxy_ip') != config.get('proxy_ip') or
            new_settings.get('proxy_port') != config.get('proxy_port') or
            new_settings.get('proxy_secret') != config.get('proxy_secret')
        )
        
        config['sound_enabled'] = new_settings.get('sound_enabled', True)
        config['sound_file'] = new_settings.get('sound_file', 'notification.wav')
        config['channels'] = new_settings.get('channels', [])[:5]
        config['dm_users'] = new_settings.get('dm_users', [])[:5] # Max 5 users
        config['dm_message'] = new_settings.get('dm_message', '')
        config['proxy_enabled'] = new_settings.get('proxy_enabled', False)
        config['proxy_ip'] = new_settings.get('proxy_ip', '')
        config['proxy_port'] = new_settings.get('proxy_port', 443)
        config['proxy_secret'] = new_settings.get('proxy_secret', '')
        
        save_config(config)
        logging.info("Configuration updated successfully via web panel.")
        
        if proxy_changed and client:
            logging.info("Proxy settings changed. Restarting Telegram client...")
            await client.disconnect()
            
        return web.json_response({"status": "success"})
    except Exception as e:
        logging.error(f"Error saving settings: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=400)

async def handle_upload(request):
    """Handles .wav file uploads from the web panel"""
    try:
        reader = await request.multipart()
        field = await reader.next()
        
        if field.name == 'file':
            filename = field.filename
            if not filename.lower().endswith('.wav'):
                return web.json_response({"status": "error", "message": "Only .wav files are allowed."})
            
            # Save the file to the current directory
            with open(filename, 'wb') as f:
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    f.write(chunk)
                    
            logging.info(f"New sound file uploaded: {filename}")
            return web.json_response({"status": "success", "filename": filename})
            
        return web.json_response({"status": "error", "message": "No file found in request."})
    except Exception as e:
        logging.error(f"Upload error: {e}")
        return web.json_response({"status": "error", "message": str(e)})

async def main():
    # Setup and start web server on port 8080
    app = web.Application()
    
    # Increase max payload size for file uploads (e.g. 50MB)
    client_max_size = 1024 ** 2 * 50 
    
    app.router.add_get('/', handle_index)
    app.router.add_get('/api/status', handle_status)
    app.router.add_post('/api/settings', handle_save_settings)
    app.router.add_post('/api/upload', handle_upload) # New upload route
    
    runner = web.AppRunner(app, client_max_size=client_max_size)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logging.info("Web control panel running at http://localhost:8080")
    
    # Start background task for Telegram
    await run_telegram_client()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Application stopped by user.")
