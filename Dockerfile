# Use an Ubuntu LTS base
#FROM ghcr.io/linuxserver/baseimage-selkies:ubuntunoble
FROM ubuntu:noble

# 1. Install system dependencies for Calibre and Python
# libgl1 and libfontconfig are required for Calibre's headless tools to function
RUN  apt-get update && \
  apt-get install -y --no-install-recommends \
    dbus \
    fcitx-rime \
    fonts-wqy-microhei \
    libxcb-cursor0 \
    libnss3 \
    libopengl0 \
    libqpdf29t64 \
    poppler-utils \
    python3 \
    python3-pip \
    python3-xdg \
    ttf-wqy-zenhei \
    wget \
    xz-utils \
    ca-certificates \
    libasound2t64

# 2. Run the installer
# We use /opt as the base; the installer creates /opt/calibre/
RUN wget -nv -O- https://download.calibre-ebook.com/linux-installer.sh | \
    sh /dev/stdin install_dir=/opt isolated=y

# 3. Fix the PATH
# The binaries are located inside the 'calibre' subfolder created by the installer
ENV PATH="/opt/calibre:${PATH}"
ENV LC_ALL=C.UTF-8
ENV QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox"

# 4. Verify (Using the absolute path just to be safe for the first run)
RUN ebook-convert --version

# Set the working directory in the container
WORKDIR /app
COPY . .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Make port 5000 available to the world outside this container
EXPOSE 8008

# Run the application
#CMD ["sh", "-c", "/app/wrapper-run.sh"]

#CMD ["python3"]