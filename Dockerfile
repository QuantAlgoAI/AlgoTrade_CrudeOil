# ---------- Base image ----------
FROM python:3.11-slim as base

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install system build deps only if necessary for pip wheel builds
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

# ---------- Python deps ----------
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ---------- App layer ----------
COPY . /app

# Expose Flask default port
ENV PYTHONUNBUFFERED=1 
EXPOSE 5000

# Default command runs the dashboard; worker is run in a separate container service
CMD ["python", "mcx.py"]
