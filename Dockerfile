# syntax=docker/dockerfile:1
FROM python:3.12-slim

# System deps (lean) so wheels can install; no heavy compilers needed when we force binary wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Modern pip / build tools
RUN python -m pip install --upgrade pip==24.2 setuptools==69.5.1 wheel==0.43.0

# Copy only the requirement files first for better cache
COPY requirements.txt requirements.txt
COPY constraints.txt constraints.txt

# Install deps (binary wheels only)
RUN pip install --only-binary=:all: -c constraints.txt -r requirements.txt

# Copy the rest of your app
COPY . .

# Render provides $PORT. Ensure your Flask server binds to 0.0.0.0:$PORT
ENV PORT=10000
EXPOSE 10000

CMD ["python", "bot.py"]
