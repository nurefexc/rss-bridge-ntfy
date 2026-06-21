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
import time
from datetime import datetime
import zoneinfo
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urlunparse

# --- Configuration ---
CONFIG_DIR = os.getenv("CONFIG_DIR", "configs")
BASE_URL = os.getenv("NTFY_URL", "https://ntfy.sh")
NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "rss_history.db")
TZ_NAME = os.getenv("TZ", "UTC")
DEFAULT_PRIORITY = "3"
USER_AGENT = os.getenv("USER_AGENT",
                       "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


# Logging Setup
class TZFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, zoneinfo.ZoneInfo(TZ_NAME))
        return dt.strftime(datefmt) if datefmt else dt.isoformat(sep=' ', timespec='seconds')


logging.basicConfig(level=logging.INFO,
                    handlers=[logging.FileHandler("rss_bridge.log", encoding='utf-8'), logging.StreamHandler()])
for h in logging.root.handlers:
    h.setFormatter(TZFormatter('%(asctime)s - [%(levelname)s] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))


class FeedEngine:
    def __init__(self):
        self.db_conn = self._init_db()
        self.session = requests.Session()

    def _init_db(self):
        os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")  # Senior practice for better concurrency
        conn.execute(
            "CREATE TABLE IF NOT EXISTS seen_entries (hash TEXT PRIMARY KEY, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
        conn.commit()
        return conn

    def clean_url(self, url):
        """Strips tracking parameters (UTM) to ensure stable hashing."""
        u = urlparse(url)
        return urlunparse((u.scheme, u.netloc, u.path, '', '', ''))

    def get_now(self):
        return datetime.now(zoneinfo.ZoneInfo(TZ_NAME))

    def get_dynamic_priority(self, f_conf):
        """Calculates priority based on time-of-day (Quiet Hours)."""
        prio = f_conf.get('priority', DEFAULT_PRIORITY)
        schedule = f_conf.get('schedule', {})
        if 'quiet_hours' in schedule:
            now_hour = self.get_now().hour
            try:
                start, end = map(int, re.findall(r'\d+', schedule['quiet_hours']))
                is_quiet = now_hour >= start or now_hour < end if start > end else start <= now_hour < end
                if is_quiet:
                    return schedule.get('quiet_priority', 1)
            except ValueError:
                logging.error(f"Invalid quiet_hours format for {f_conf.get('name')}")
        return prio

    def should_filter(self, entry, f_conf):
        """Advanced content filtering using Regex."""
        filters = f_conf.get('filters', {})
        text_to_scan = f"{entry.get('title', '')} {entry.get('summary', '')}".lower()

        if 'exclude_regex' in filters and re.search(filters['exclude_regex'], text_to_scan, re.IGNORECASE):
            return True  # Filter out
        if 'include_regex' in filters and not re.search(filters['include_regex'], text_to_scan, re.IGNORECASE):
            return True  # Filter out because it doesn't match inclusion
        return False

    def clean_html_content(self, html_content, entry):
        if not html_content: return "", None
        soup = BeautifulSoup(html_content, "html.parser")

        # Image extraction logic
        img_url = None
        if 'media_content' in entry and entry.media_content:
            img_url = entry.media_content[0]['url']
        elif 'enclosures' in entry and entry.enclosures:
            img_url = entry.enclosures[0]['href']
        else:
            img_tag = soup.find("img")
            if img_tag and img_tag.get("src"): img_url = img_tag["src"]

        text = re.sub(r'\s+', ' ', soup.get_text(separator=" ")).strip()
        return (text[:250] + '...') if len(text) > 250 else text, img_url

    def send_ntfy(self, entry, f_conf, topic, priority, delay_str):
        title = entry.get("title", "No Title")
        link = entry.get("link", "#")
        content_list = entry.get("content")
        if content_list and isinstance(content_list, list) and len(content_list) > 0:
            content = content_list[0].get("value", "")
        else:
            content = entry.get("summary", "")

        short_desc, image_url = self.clean_html_content(content, entry)

        headers = {
            "Authorization": f"Bearer {NTFY_TOKEN}",
            "User-Agent": USER_AGENT,
            "Title": title.encode('utf-8'),
            "Click": link,
            "Markdown": "yes",
            "Tags": "newspaper",
            "Priority": str(priority)
        }
        if delay_str: headers["Delay"] = delay_str
        if f_conf.get('icon'): headers["Icon"] = f_conf['icon']
        if image_url: headers["Attach"] = image_url

        message = f"**Source:** {f_conf['name']}\n\n{short_desc}\n\n[Read on Website]({link})"

        try:
            r = self.session.post(f"{BASE_URL}/{topic}", data=message.encode('utf-8'), headers=headers, timeout=20)
            r.raise_for_status()
            logging.info(f"Sent: [{f_conf['name']}] {title} (P:{priority})")
        except Exception as e:
            logging.error(f"Ntfy error: {e}")

    def sync(self):
        logging.info("Sync cycle started...")
        if not os.path.exists(CONFIG_DIR):
            logging.error("Config dir missing.")
            return

        cursor = self.db_conn.cursor()
        config_files = sorted([f for f in os.listdir(CONFIG_DIR) if f.endswith('.json')])

        for filename in config_files:
            topic = os.path.splitext(filename)[0]
            try:
                with open(os.path.join(CONFIG_DIR, filename), 'r', encoding='utf-8') as f:
                    feeds = json.load(f)

                for f_conf in feeds:
                    source_name = f_conf.get('name', 'Unknown')
                    feed = feedparser.parse(f_conf['url'])
                    sent_count = 0

                    priority = self.get_dynamic_priority(f_conf)

                    for entry in feed.entries:
                        if sent_count >= 3: break  # Batch limit

                        if self.should_filter(entry, f_conf):
                            continue

                        clean_link = self.clean_url(entry.get('link', ''))
                        entry_id = entry.get('id', clean_link)
                        entry_hash = hashlib.sha256(f"{topic}_{entry_id}".encode()).hexdigest()

                        cursor.execute("SELECT 1 FROM seen_entries WHERE hash=?", (entry_hash,))
                        if not cursor.fetchone():
                            # Flood protection delay
                            delay = f"{sent_count * 5 + 5}m" if int(priority) < 4 else None

                            self.send_ntfy(entry, f_conf, topic, priority, delay)
                            cursor.execute("INSERT INTO seen_entries (hash) VALUES (?)", (entry_hash,))
                            self.db_conn.commit()
                            sent_count += 1

                    if sent_count > 0:
                        logging.info(f"Processed {source_name}: {sent_count} new items.")

            except Exception as e:
                logging.error(f"Failed config {filename}: {e}")

    def close(self):
        self.db_conn.close()


# --- Global Control ---
engine = FeedEngine()


def signal_handler(sig, frame):
    logging.info("Shutdown signal received.")
    engine.close()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

if __name__ == "__main__":
    interval = int(os.getenv("SYNC_INTERVAL", "600"))
    logging.info(f"Service running (Interval: {interval}s, TZ: {TZ_NAME})")
    while True:
        try:
            engine.sync()
        except Exception as e:
            logging.error(f"Loop error: {e}")
        time.sleep(interval)
