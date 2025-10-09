    # Example Dockerfile to install ffmpeg on an Ubuntu base image
    FROM ubuntu:latest
    RUN apt-get update && apt-get install -y ffmpeg
    # Add your application code and dependencies here
    COPY . /app
    WORKDIR /app

    CMD ["python3 bot.py"] 
