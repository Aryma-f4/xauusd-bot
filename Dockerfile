FROM python:3.12-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir pybit python-telegram-bot pandas numpy ta

# Copy bot code
COPY bot.py /app/bot.py

# Run
CMD ["python3", "/app/bot.py"]
