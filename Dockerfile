# Use an official Python runtime as a parent image
FROM python:3.13-slim

# Set environment variables to prevent Python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1
# Set default port
ENV PORT=8000

# Create and set the working directory
WORKDIR /app

# Install system dependencies (if any were needed - none apparent here)
# RUN apt-get update && apt-get install -y --no-install-recommends some-package && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container
COPY requirements.txt .

# Install Python dependencies
# --no-cache-dir reduces image size
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code into the container
COPY . .
# We DON'T copy .env or credentials here - they will be handled by docker-compose

# Expose the port the app runs on
EXPOSE ${PORT}

# Define the command to run the application using uvicorn
# --host 0.0.0.0 makes the server accessible from outside the container
CMD ["uvicorn", "websocket:app", "--host", "0.0.0.0", "--port", "8000"]

# For development with hot-reloading (optional, comment out CMD above if using this)
# CMD ["uvicorn", "websocket:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]