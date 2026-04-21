FROM python:3.12-slim

# LibreOffice for XLSX → PDF conversion
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libreoffice-calc \
        fonts-liberation \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create necessary directories
RUN mkdir -p data/uploads data/declarations uploads

# Expose port
EXPOSE 8000

# Environment
ENV HOST=0.0.0.0
ENV PORT=8000
ENV NO_BROWSER=1

# Run via uvicorn directly (not run.py which tries to open browser)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
