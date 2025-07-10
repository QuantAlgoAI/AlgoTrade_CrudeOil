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
    


    