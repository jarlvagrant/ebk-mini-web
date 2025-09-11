#!/bin/sh

# git clone https://github.com/jarlvagrant/yt-dlp-mini-web .

git pull
pip install --upgrade pip
pip install --no-cache-dir -r requirements.txt

python src/WebTools.py