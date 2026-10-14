import datetime
import json
import logging
import os
import re
import shutil
import smtplib
import sys
import threading
from email.message import EmailMessage
from pathlib import Path

from fake_useragent import UserAgent

log_path = "logs"
log_file = "debug.log"
config_path = "configs"
config_file = "config.json"
cache_path = "cache"
symlink_path = os.path.join(cache_path, "symlinks")
image_path = os.path.join(cache_path, "image")
book_csv_file = "books.csv"
book_csv_columns = ['title', 'author', 'path', 'format', 'size', 'updated', 'created']


logger = logging.getLogger(__name__)


class ThreadWithTrace(threading.Thread):
	def __init__(self, target=None, name="", daemon=True, *args, **keywords):
		threading.Thread.__init__(self, target=target, name=name, daemon=daemon, *args, **keywords)
		self.killed = False

	def start(self):
		self.__run_backup = self.run
		self.run = self.__run
		threading.Thread.start(self)

	def __run(self):
		sys.settrace(self.globaltrace)
		self.__run_backup()
		self.run = self.__run_backup

	def globaltrace(self, frame, event, arg):
		if event == 'call':
			return self.localtrace
		else:
			return None

	def localtrace(self, frame, event, arg):
		if self.killed:
			if event == 'line':
				raise SystemExit()
		return self.localtrace

	def kill(self):
		self.killed = True


def getDate():
	return datetime.date.today().__str__()


def getTime():
	return datetime.datetime.now().__str__()


class UserAgentGen(UserAgent):
	def __init__(self):
		super().__init__()
		self.ua = self.random
		logger.debug(f"User Agent: {self.ua}")

	def get(self):
		return self.ua

	def renew(self):
		self.ua = self.random
		logger.debug(f"User Agent: {self.ua}")


UA = UserAgentGen()


class JsonIO:
	def __init__(self, path=config_path, json_file=config_file):
		if not os.path.isdir(path):
			os.mkdir(path)
		self.json_path = os.path.join(path, json_file)
		if not os.path.isfile(self.json_path):
			with open(self.json_path, "w") as f:
				self.dict = {
					"video_dir": "/video",
					"audio_dir": "/audio",
					"ebook_dir": "/book",
					"email": {
						"from": "sender email address",
						"to": "kindle email address",
						"host": "host",
						"secret": "password",
						"port": 587
					},
					"web_parsers": [
						{
							"base": "www.52shu.vip",
							"content_tag": {
								"id": "text"
							}
						}]
				}
				json.dump(self.dict, f, ensure_ascii=False, indent=4, )
				f.close()
		else:
			with open(self.json_path, 'r') as f:
				self.dict = json.load(f)
				f.close()

	def get(self, key, subkey=None):
		if not subkey:
			return self.dict.get(key, None)
		if self.dict.get(key, None):
			return self.dict.get(key, None).get(subkey, None)
		return None

	def set(self, key, value, subkey=None):
		if not subkey:
			self.dict[key] = value
		else:
			if self.dict.get(key, None):
				self.dict[key][subkey] = value
		with open(self.json_path, 'w', encoding='utf-8') as f:
			json.dump(self.dict, f, ensure_ascii=False, indent=4, )
			f.close()


ConfigIO = JsonIO()


class CacheIO:
	def __init__(self, path=cache_path):
		self.path = path
		if not os.path.isdir(path):
			os.makedirs(path)
		self.clear()

	def getPath(self):
		return self.path

	def clear(self):
		for filename in os.listdir(self.path):
			file_path = os.path.join(self.path, filename)
			try:
				if os.path.isfile(file_path) or os.path.islink(file_path):
					os.unlink(file_path)
				elif os.path.isdir(file_path):
					shutil.rmtree(file_path)
			except Exception as e:
				logger.warning('Failed to delete %s. Reason: %s' % (file_path, e))


SymlinkIO = CacheIO(path=symlink_path)
ImageIO = CacheIO(path=image_path)


def getInitialFolder(dir_type):
	folder = ConfigIO.get(dir_type)
	if not folder or not os.path.isdir(folder):
		folder = Path.home().__str__()
		ConfigIO.set(dir_type, folder)
	return folder


def getInitialSubfolders(cur_dir):
	res = [cur_dir]
	parent_dir = cur_dir
	if cur_dir and os.path.exists(cur_dir):
		parent_dir = Path(cur_dir).parent
	for f in (os.sep, parent_dir.__str__(), Path.home().__str__()):
		if f not in res:
			res.append(f)
	return res


def getSubfolders(cur_dir):
	res = getInitialSubfolders(cur_dir)
	temp = []
	if cur_dir and os.path.isdir(cur_dir):
		for f in os.scandir(cur_dir):
			if f.is_dir() and not f.name.startswith('.'):
				temp.append(f.path)
	temp = sorted(temp)
	for f in temp:
		if f not in res:
			res.append(f)
	return res


def read_binary_file(filepath=None):
	output = ""
	if filepath is not None:
		with open(filepath, 'rb') as f:
			output = f.read()
			f.close()
	return output

def write_text_file(text: str, path: str, filename: str):
	clean_path = os.path.join(path, sanitize_linux_filename(filename))
	try:
		with open(clean_path, 'w', encoding='utf-8') as f:
			f.write(text)
		logger.info(f"Saved extracted content file file: {clean_path}")
	except Exception:
		logger.error(f"Can't save extracted content to file: {clean_path}")
	return clean_path

def sanitize_linux_filename(filename, replacement="_"):
	# 1. Reject or clean the path separator '/' and the null byte '\x00'
	# Also cleans hidden control characters that break terminal scripts
	forbidden_chars = r"[\/\x00-\x1f]"
	clean_name = re.sub(forbidden_chars, replacement, filename)

	# 2. Linux quirk: Avoid letting a filename start with a dash '-'
	# This prevents terminal commands (like 'rm' or 'cat') from thinking it's an option flag
	if clean_name.startswith("-"):
		clean_name = replacement + clean_name[1:]

	# 3. Linux Maximum Length Constraint (255 bytes max for most file systems)
	# We encode to bytes to accurately measure length, then decode back
	encoded_name = clean_name.encode("utf-8")
	if len(encoded_name) > 255:
		# Separate extension to preserve it if possible
		root, ext = os.path.splitext(clean_name)
		ext_bytes_len = len(ext.encode("utf-8"))

		# Truncate the root string bytes to fit within the 255-byte limit
		max_root_bytes = 255 - ext_bytes_len
		truncated_root = root.encode("utf-8")[:max_root_bytes].decode(
			"utf-8", errors="ignore"
		)
		clean_name = truncated_root + ext
	return clean_name


# --- Example Usage ---
bad_linux_name = "-my/invalid\x00file*name.txt"
good_linux_name = sanitize_linux_filename(bad_linux_name)

print(f"Original:  {repr(bad_linux_name)}")
print(f"Sanitized: {good_linux_name}")

# Safely write the file
try:
	with open(good_linux_name, "w", encoding="utf-8") as file:
		file.write("Linux file content.")
	print("File saved successfully!")
except OSError as e:
	print(f"OS Error: {e}")


class SendEmail:
	def __init__(self, filepath):
		self.filepath = filepath
		self.message = ""

	def send(self):
		if not os.path.isfile(self.filepath):
			logger.error(f"Invalid file path: {self.filepath}")
			self.message = f"Invalid file path: {self.filepath}"
			return False

		email = ConfigIO.get("email")
		if not email or None in [email.get(key, None) for key in ["from", "to", "host", "port", "secret"]]:
			logger.error(f"Invalid email config in config.json: please check keys: from, to, host, port, secret")
			self.message = f"Invalid email config in config.json: please check keys: from, to, host, port, secret"
			return False
		msg = EmailMessage()
		filename = Path(self.filepath).name
		msg['Subject'] = filename
		msg['From'] = email.get("from")
		msg['To'] = email.get("to")
		with open(self.filepath, 'rb') as fp:
			data = fp.read()
		msg.add_attachment(data, maintype='application', subtype='octet-stream', filename=filename)
		# Add error and failure check
		with smtplib.SMTP(email.get("host"), email.get("port")) as smtp:
			smtp.starttls()
			smtp.login(email.get("from"), email.get("secret"))
			smtp.send_message(msg=msg)
			smtp.quit()
		logger.info(f"Emailed {filename} to {email.get('to')}")
		self.message = f"Emailed {filename} to {email.get('to')}"
		return True
