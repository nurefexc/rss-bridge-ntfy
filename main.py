import feedparser
import requests
import sqlite3
import hashlib
import re
import json
import os
import logging
import signal
import sys
from bs4 import BeautifulSoup
import time
from datetime import datetime
import zoneinfo

# --- Configuration ---
# Read environment variables with default values
CONFIG_DIR = os.getenv("CONFIG_DIR", "configs")
BASE_URL = os.getenv("NTFY_URL", "https://ntfy.sh")
NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "rss_history.db")
TZ_NAME = os.getenv("TZ", "UTC")
DEFAULT_PRIORITY = "3"
USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Logging setup
def get_now():
    """Returns the current time in the configured timezone."""
    return datetime.now(zoneinfo.ZoneInfo(TZ_NAME))

class TZFormatter(logging.Formatter):
    """Custom logging formatter that respects the configured timezone."""
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, zoneinfo.ZoneInfo(TZ_NAME))
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(sep=' ', timespec='seconds')

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler("rss_bridge.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
for handler in logging.root.handlers:
    handler.setFormatter(TZFormatter('%(asctime)s - [%(levelname)s] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))

db_conn = None


def signal_handler(sig, frame):
    """Handles termination signals for safe database closure."""
    global db_conn
    logging.info("Termination signal received. Closing database...")
    if db_conn:
        try:
            db_conn.close()
        except Exception:
            pass
    sys.exit(0)


# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def init_db():
    """Initializes the SQLite database for storing seen entries."""
    db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    if not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS seen_entries (hash TEXT PRIMARY KEY, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    return conn


def format_local_date(entry):
    """Formats the feed entry date to a readable local time based on TZ."""
    try:
        parsed_time = entry.get("published_parsed", entry.get("updated_parsed"))
        if parsed_time:
            # Convert feed time (usually UTC) to local timezone
            dt_utc = datetime.fromtimestamp(time.mktime(parsed_time), zoneinfo.ZoneInfo("UTC"))
            local_dt = dt_utc.astimezone(zoneinfo.ZoneInfo(TZ_NAME))
            return local_dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        pass
    return entry.get("published", entry.get("updated", "Unknown date"))


def clean_html_content(html_content, entry):
    """Cleans HTML content, extracts description and potential image."""
    if not html_content:
        return "", None
    soup = BeautifulSoup(html_content, "html.parser")

    # Image extraction based on media_content, enclosures or img tag
    img_url = None
    if 'media_content' in entry and len(entry.media_content) > 0:
        img_url = entry.media_content[0]['url']
    elif 'enclosures' in entry and len(entry.enclosures) > 0:
        img_url = entry.enclosures[0]['href']
    else:
        img_tag = soup.find("img")
        if img_tag and img_tag.has_attr("src"):
            img_url = img_tag["src"]

    text = soup.get_text(separator=" ")
    text = re.sub(r'\s+', ' ', text).strip()
    short_desc = (text[:250] + '...') if len(text) > 250 else text
    return short_desc, img_url


def send_ntfy(session, entry, source_name, custom_icon, topic, priority, delay_str):
    """Sends a notification to the ntfy server."""
    title = entry.get("title", "No Title")
    link = entry.get("link", "#")
    content = entry.get("summary", "")
    if entry.get("content"):
        content = entry.content[0].value

    short_desc, image_url = clean_html_content(content, entry)
    local_date_str = format_local_date(entry)

    headers = {
        "Authorization": f"Bearer {NTFY_TOKEN}",
        "User-Agent": USER_AGENT,
        "Title": title.encode('utf-8'),
        "Click": link,
        "Markdown": "yes",
        "Tags": "newspaper",
        "Priority": str(priority),
        "X-Publish-Date": local_date_str
    }

    if delay_str:
        headers["Delay"] = delay_str
    if custom_icon:
        headers["Icon"] = custom_icon
    if image_url:
        headers["Attach"] = image_url

    message = f"**Source:** {source_name}\n**Local Time:** {local_date_str}\n\n{short_desc}\n\n[Read on Website]({link})"

    try:
        r = session.post(f"{BASE_URL}/{topic}", data=message.encode('utf-8'), headers=headers, timeout=20)
        r.raise_for_status()
        logging.info(f"Notification sent: [{source_name}] - {title} [P:{priority}]")
    except Exception as e:
        logging.error(f"Error during ntfy dispatch: {e}")


def process_config_file(session, file_path, cursor, conn):
    """Processes a configuration JSON file containing feeds."""
    topic = os.path.splitext(os.path.basename(file_path))[0]
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            feeds = json.load(f)
    except Exception as e:
        logging.error(f"Error loading configuration ({file_path}): {e}")
        return

    for f_conf in feeds:
        try:
            feed = feedparser.parse(f_conf['url'])
            prio = f_conf.get('priority', DEFAULT_PRIORITY)
            sent_in_batch = 0

            for entry in feed.entries:
                # Send at most 3 new entries per feed per run
                if sent_in_batch >= 3:
                    break

                # Robust entry identification
                entry_id = entry.get('id', entry.get('link', 'unknown_id'))
                entry_hash = hashlib.sha256(f"{topic}_{entry_id}".encode()).hexdigest()

                cursor.execute("SELECT 1 FROM seen_entries WHERE hash=?", (entry_hash,))
                if not cursor.fetchone():
                    # Calculate delay based on priority (flood protection)
                    p = int(prio)
                    delay = None
                    if p < 5:
                        if p == 4:
                            delay = f"{sent_in_batch * 30 + 10}s"
                        elif p == 3:
                            delay = f"{sent_in_batch * 5 + 5}m"
                        else:
                            delay = f"{sent_in_batch * 10 + 15}m"

                    send_ntfy(session, entry, f_conf['name'], f_conf.get('icon'), topic, prio, delay)

                    cursor.execute("INSERT INTO seen_entries (hash) VALUES (?)", (entry_hash,))
                    conn.commit()
                    sent_in_batch += 1
        except Exception as e:
            logging.error(f"Error processing feed ({f_conf.get('name', 'Unknown')}): {e}")


def main():
    """Main execution cycle that iterates over configuration files."""
    global db_conn
    if not os.path.exists(CONFIG_DIR):
        logging.error(f"Configuration directory '{CONFIG_DIR}' not found.")
        return
    db_conn = init_db()
    cursor = db_conn.cursor()
    with requests.Session() as session:
        config_files = sorted([f for f in os.listdir(CONFIG_DIR) if f.endswith('.json')])
        for filename in config_files:
            process_config_file(session, os.path.join(CONFIG_DIR, filename), cursor, db_conn)
    db_conn.close()


if __name__ == "__main__":
    sync_interval = int(os.getenv("SYNC_INTERVAL", "600"))
    logging.info(f"Service started. Interval: {sync_interval}s")
    while True:
        try:
            main()
        except Exception as e:
            logging.error(f"Main loop crashed but restarting: {e}")
        time.sleep(sync_interval)
