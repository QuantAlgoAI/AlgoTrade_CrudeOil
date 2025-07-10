# ---------- Base image ----------
FROM python:3.11-slim

# Prevent interactive prompts during apt
ENV DEBIAN_FRONTEND=noninteractive

# ---------- OS-level dependencies ----------
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    build-essential gcc libffi-dev libssl-dev curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ---------- Python dependencies ----------
WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# ---------- Application layer ----------
COPY . /app

# ---------- Runtime configuration ----------
ENV PYTHONUNBUFFERED=1
EXPOSE 5000

CMD ["python", "mcx.py"]
    FROM python:3.11-slim AS base

    ENV DEBIAN_FRONTEND=noninteractive
    
    # ---------- Install OS-level dependencies ----------
    RUN apt-get update -qq && apt-get install -y --no-install-recommends \
        build-essential gcc libpam0g libffi-dev libssl-dev curl \
# ---------- Install Python packages ----------
WORKDIR /app
COPY requirements.txt .
    
RUN pip install --upgrade pip && \
    pip install -r requirements.txt
    
# ---------- Copy app ----------
COPY . /app
    
# ---------- Env ----------
ENV PYTHONUNBUFFERED=1
EXPOSE 5000
    ENV PYTHONUNBUFFERED=1
    EXPOSE 5000
    
    # ---------- Run ----------
    CMD ["python", "mcx.py"]
    