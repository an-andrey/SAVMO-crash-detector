FROM python:3.13
WORKDIR /app

# Install the specific Linux libraries that OpenCV needs to function
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Add requirements and copy website dir to the working dir
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY website/ .

# Run the command
CMD ["python", "run_manager.py"]