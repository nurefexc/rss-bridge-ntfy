FROM python:3.11-alpine

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=UTC

LABEL description="RSS/Atom feed to ntfy.sh bridge with SQLite history"
LABEL maintainer="nurefexc"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV SYNC_INTERVAL=600

CMD ["python", "main.py"]
