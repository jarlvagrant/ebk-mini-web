# Use an official Python runtime as the base image
FROM python:3-slim

RUN apt-get update && apt-get -y install git curl xz-utils

RUN curl -L -o ffmpeg.tar.xz https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz

RUN tar -xf ffmpeg.tar.xz -C /opt

RUN mv /opt/ffmpeg-master-latest-linux64-gpl /opt/ffmpeg

ENV PATH="$PATH:/opt/ffmpeg/bin"

RUN rm ffmpeg.tar.xz

# Set the working directory in the container
WORKDIR /app
COPY . .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Make port 5000 available to the world outside this container
EXPOSE 8008

# Run the application
CMD ["sh", "-c", "/app/wrapper-run.sh"]

