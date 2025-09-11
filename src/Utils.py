import datetime
import json
import logging
import os
import shutil
import smtplib
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

def write_text_file(text: str, path):
	with open(path, 'w', encoding='utf-8') as f:
		f.write(text)
		f.close()


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
