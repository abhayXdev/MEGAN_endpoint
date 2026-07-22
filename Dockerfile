# Use a lightweight Linux with Python pre-installed
FROM python:3.11-slim

# Install FFmpeg and NodeJS (for yt-dlp JavaScript extraction bypass)
RUN apt-get update && \
    apt-get install -y ffmpeg nodejs && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy the requirements and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your python script into the container
COPY app.py .

# Expose port 7860
EXPOSE 7860

# Start the MCP server
CMD ["python", "app.py"]
