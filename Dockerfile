FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for Pillow (image processing for Pomodoro cards)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libjpeg62-turbo-dev \
    zlib1g-dev \
    libfreetype6-dev \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directory for the SQLite database
RUN mkdir -p /app/data

CMD ["python", "main.py"]
