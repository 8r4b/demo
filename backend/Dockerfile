# Stage 1: Install dependencies
FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    libgl1 \
    git \
 && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependencies
COPY requirements.txt .

# Install Python dependencies
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy app source
COPY backend ./backend

# Add face_recognition_models manually if you downloaded it
COPY face_recognition_models/face_recognition_models /usr/local/lib/python3.12/site-packages/face_recognition_models

# Default command
CMD ["python", "backend/main.py"]
