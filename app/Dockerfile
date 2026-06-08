FROM python:3.11-slim

# ffmpeg is used to convert the recorded video (mkv/mp4) into mp3 audio.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh

EXPOSE 8080

# entrypoint.sh serves HTTPS if a cert is mounted at /certs, else plain HTTP.
CMD ["./entrypoint.sh"]
