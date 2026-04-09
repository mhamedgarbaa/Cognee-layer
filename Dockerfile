# ============================
# 1) Builder Image
# ============================
FROM python:3.11-slim AS builder

# Prevent Python from writing .pyc
ENV PYTHONDONTWRITEBYTECODE=1
# Make logs unbuffered
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install compilation dependencies for Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
 && rm -rf /var/lib/apt/lists/*

# Copy requirement file
COPY requirements.txt .

# Install dependencies into a temporary folder
RUN pip install --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ============================
# 2) Final Runtime Image
# ============================
FROM python:3.11-slim

WORKDIR /app

# Create a non-root user (security best practice)
RUN useradd -m appuser

# Copy dependencies from builder image
COPY --from=builder /install /usr/local

# Copy project
COPY . .

# Change owner to non-root
RUN chown -R appuser:appuser /app
USER appuser

# Expose API port
EXPOSE 8000

# Default command (production)
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
