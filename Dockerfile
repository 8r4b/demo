# Use a slim Python 3.12 image
FROM python:3.12-slim-bookworm

# Install essential system dependencies for OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    libgl1-mesa-glx \
    libjpeg-dev \
    libpng-dev \
    libtiff-dev \
    libavcodec-dev \
    libavformat-dev \
    libswscale-dev \
    libv4l-dev \
    git \
&& rm -rf /var/lib/apt/lists/* \
&& apt-get clean

# Set the working directory inside the container
WORKDIR /app

# Copy requirements.txt and upgrade pip
COPY requirements.txt .
RUN pip install --upgrade pip

# Install all Python dependencies from requirements.txt
RUN pip install -r requirements.txt

# Copy the rest of your application code into the container
COPY . .

# Ensure necessary directories exist for data storage at runtime
RUN mkdir -p /app/data /app/known_faces /app/data/faces /app/data/temp_videos

# Expose the port where the FastAPI application will be listening
EXPOSE 8000

# Define the command to run the FastAPI application using Uvicorn
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
