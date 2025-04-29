# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables to prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory in the container
WORKDIR /app

# Install system dependencies that might be needed by some Python packages (like matplotlib)
#RUN apt-get update && apt-get install -y --no-install-recommends \
#    # Add any needed system packages here, e.g., fonts for matplotlib if needed \
#    && rm -rf /var/lib/apt/lists/*

# Install pip dependencies
# Install system dependencies and update certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY ./app /app/app
# We don't copy .env here; it will be provided via docker-compose or environment variables

# Create the data directory (though it will be mounted from the host via docker-compose)
# RUN mkdir -p /app/data && chown -R nobody:nogroup /app/data # Create as non-root if needed

# Specify the command to run on container startup
CMD ["python", "-m", "app.bot"]

# Optional: Run as a non-root user for better security
# RUN useradd -m myuser
# USER myuser
# If using a non-root user, ensure file permissions are correct (e.g., for the data volume)
