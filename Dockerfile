# Use Node.js to build the React frontend
FROM node:18 as frontend

WORKDIR /frontend

COPY frontend/ .

RUN npm install && npm run build

# ---- Backend stage ----
FROM python:3.11-slim

WORKDIR /app

# System dependencies for OpenCV
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ .

# Copy the frontend build from the previous stage
COPY --from=frontend /frontend/build ./frontend/build

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
