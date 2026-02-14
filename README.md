# RSS Bridge ntfy

[![Docker Hub](https://img.shields.io/docker/pulls/nurefexc/rss-bridge-ntfy.svg)](https://hub.docker.com/r/nurefexc/rss-bridge-ntfy)
[![Docker Image Size](https://img.shields.io/docker/image-size/nurefexc/rss-bridge-ntfy/latest)](https://hub.docker.com/r/nurefexc/rss-bridge-ntfy)
[![Docker Image Version](https://img.shields.io/docker/v/nurefexc/rss-bridge-ntfy/latest)](https://hub.docker.com/r/nurefexc/rss-bridge-ntfy)

A lightweight Python script that monitors RSS/Atom feeds and sends real-time notifications to your [ntfy](https://ntfy.sh) topics. It supports multiple configuration files, history tracking with SQLite, and advanced ntfy features like priority, tags, and attachments.

## Features

- **Multiple Feed Support:** Monitor multiple RSS/Atom feeds organized in JSON configuration files.
- **SQLite History:** Remembers sent entries to avoid duplicate notifications.
- **Smart Content Parsing:** Extracts descriptions and images from HTML content for rich notifications.
- **Actionable Notifications:** 
    - 🖼️ **Image Attachments:** Automatically attaches images found in the feed.
    - 🏷️ **Customizable Metadata:** Supports per-feed priority, custom icons, and tags.
- **Flood Protection:** Implements intelligent delays based on priority when sending multiple updates.
- **Docker Ready:** Optimized for containerized deployment with minimal footprint.

## Prerequisites

1. **ntfy Topic:** Create one or more topics on [ntfy.sh](https://ntfy.sh).
2. **RSS Feeds:** URLs of the RSS/Atom feeds you want to monitor.

## Setup & Installation

### Option 1: Using Docker (Recommended)

The easiest way to run the bridge is using the official Docker image:

1. Pull the image from Docker Hub:
   ```bash
   docker pull nurefexc/rss-bridge-ntfy:latest
   ```
2. Run the container:
   ```bash
   docker run -d \
     --name rss-ntfy-bridge \
     --restart always \
     -v $(pwd)/configs:/app/configs \
     -e NTFY_URL=https://ntfy.sh \
     -e NTFY_TOKEN=your_token \
     -e SYNC_INTERVAL=600 \
     nurefexc/rss-bridge-ntfy:latest
   ```

### Option 2: Build Locally
If you want to build the image yourself:
1. Clone this repository.
2. Build the image:
   ```bash
   docker build -t nurefexc/rss-bridge-ntfy:latest .
   ```
3. Run the container as shown in Option 1.

## CI/CD (Automation)

This repository includes a GitHub Action that automatically builds and pushes the Docker image to **Docker Hub** whenever you push to the `master` branch.

To enable this, add the following **Secrets** to your GitHub repository (`Settings > Secrets and variables > Actions`):
- `DOCKERHUB_USERNAME`: Your Docker Hub username.
- `DOCKERHUB_TOKEN`: Your Docker Hub Personal Access Token (PAT).

### Option 3: Manual Installation

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Set environment variables (see `env.sample`).
3. Create a `configs` directory and add your feed JSON files.
4. Run the script:
   ```bash
   python main.py
   ```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `CONFIG_DIR` | Directory where feed configuration JSON files are located | `configs` |
| `NTFY_URL` | Full ntfy server URL | `https://ntfy.sh` |
| `NTFY_TOKEN` | ntfy authentication token (optional) | - |
| `DB_PATH` | Path to the SQLite database file for history | `rss_history.db` |
| `SYNC_INTERVAL` | Synchronization interval in seconds | `600` |
| `USER_AGENT` | Custom User-Agent for HTTP requests | *Browser-like UA* |

### Feed Configuration (JSON)

Place `.json` files in the `configs` directory. The filename (without extension) will be used as the **ntfy topic**. Example `news.json`:

```json
[
  {
    "name": "Example News",
    "url": "https://example.com/rss",
    "priority": "3",
    "icon": "https://example.com/icon.png"
  }
]
```

## How it works

The script scans the `CONFIG_DIR` for `.json` files. Each file represents an ntfy topic. For each feed in the JSON, it parses the latest entries, checks against the SQLite database to see if they are new, and sends a notification to the corresponding topic. It then waits for the `SYNC_INTERVAL` before checking again.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
