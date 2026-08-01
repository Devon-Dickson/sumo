FROM python:3.12-slim

# ffmpeg is not optional: NHK serves video and audio as separate HLS renditions,
# so yt-dlp has to merge them.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV SUMO_HOST=0.0.0.0 \
    SUMO_PORT=8787 \
    SUMO_INCOMPLETE_DIR=/downloads/incomplete \
    SUMO_COMPLETE_DIR=/downloads/complete \
    SUMO_STATE_FILE=/config/state.json \
    PYTHONUNBUFFERED=1

EXPOSE 8787
VOLUME ["/config", "/downloads"]

HEALTHCHECK --interval=60s --timeout=10s --start-period=10s \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ['SUMO_PORT']}/health\").read()"

CMD ["sumobridge"]
