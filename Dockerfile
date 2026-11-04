# Repro service: API, run page, job workers and the GitHub bot in one container.
# docker build -t repro . && docker run -p 8000:8000 --env-file .env -v repro-data:/app/runs repro
FROM mcr.microsoft.com/playwright:v1.56.1-noble

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip ffmpeg git curl ca-certificates gh \
    && rm -rf /var/lib/apt/lists/* \
    && npx -y playwright@1.56.1 install chrome

# Muse Code CLI (Meta's coding agent), installed without touching shell rc files.
RUN MUSE_NO_MODIFY_PATH=1 bash -c "curl -fsSL https://dev.meta.ai/install.sh | sh"
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /venv && /venv/bin/pip install --no-cache-dir -r requirements.txt
ENV PATH="/venv/bin:${PATH}"

COPY backend/browser/package*.json backend/browser/
RUN cd backend/browser && npm ci

COPY . .
ENV REPRO_WORKERS=2 PYTHONUNBUFFERED=1
VOLUME ["/app/runs", "/root/.cache/repro"]
EXPOSE 8000
HEALTHCHECK CMD curl -fs http://localhost:8000/api/budget || exit 1
CMD ["uvicorn", "backend.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
