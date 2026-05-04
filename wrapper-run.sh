#!/bin/sh

# git clone https://github.com/jarlvagrant/calibre-mini-web .

git pull
pip install --upgrade pip
pip install --no-cache-dir -r requirements.txt

python src/WebTools.py