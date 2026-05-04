import io
import logging
import os
import random
import re
import sys
from threading import Thread
from time import sleep
from urllib.parse import unquote, urlsplit, urlparse

import chinese_converter
import requests
from PIL import Image, ImageDraw, ImageFont
from bs4 import BeautifulSoup
from ebooklib import epub
from requests import HTTPError

from Utils import UA, ConfigIO, read_binary_file, write_text_file

logger = logging.getLogger(__name__)

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# make a list of possible chap format, start from the strict regex.
chap_regex_list = [
	r"(?m)^[\s\r\n\.☆、—-]*第[0123456789一二三四五六七八九十零〇百千两]+[章回部节集卷][\s\r\n\.☆、—-].{0,30}",  # 第一章 飞雪连天
	r"(?m)^.{0,10}第[0123456789一二三四五六七八九十零〇百千两]+[章回部节集卷].{0,30}",  # 1. 第一章 飞雪连天
	r"(?m)^[\s\r\n\.☆、—-]*\d+[\s\r\n\.☆、—-].{0,30}",  # 1. 飞雪连天
	r"(?m)^.{0,10}\d+.{0,30}",  # 正文 1. 飞雪连天
	r"(?m)^[\s\r\n\.☆、—-]*[第章集卷][0123456789一二三四五六七八九十零〇百千两]+[\s\r\n\.☆、—-].{0,30}",  # ☆ 卷一 飞雪连天
	r"(?m)^.{0,10}[第章集卷][0123456789一二三四五六七八九十零〇百千两]+[\s\r\n\.☆、—-].{0,30}",  # ☆一。 卷一 飞雪连天
	r"(?m)^[\s\r\n\.]*[☆、—-].{0,30}"]  # ☆ 飞雪连天

codepoint_to_chr = chr


def get_meta_data(intro, data, filename=""):
	content = intro if intro else data
	title = filename
	author, tags = "", ""
	seg = re.search(r"(?s)[.]?([^\n《》「」『』【】/]*).?\n?作者[：:]([^\n》」』】)]*)", content)
	if seg:
		title = title if title else seg.group(1)
		author = seg.group(2)
	if not title:
		for line in content.split():
			if line and not line.strip().startswith("http"):
				title = line
				break
	if not tags:
		seg = re.search('内容标签[:：](.*)', content)
	if seg:
		tags = seg.group(1)
	return title.strip(), author.strip(), tags, intro


def clean_txt(txt):
	"""
	Run transformations on the text to put it into
	consistent state.
	"""
	if not txt:
		return ''

	if isinstance(txt, bytes):
		# Only handle GBK chars for now. Download Cadet to detect other encodings.
		try:
			txt = txt.decode('utf-8')
		except UnicodeDecodeError:
			txt = txt.decode('GBK', 'ignore')
	# Strip whitespace from the beginning and end of the line. Also replace
	# all line breaks with \n.
	txt = '\n'.join([line.strip() for line in txt.splitlines()])

	# Replace whitespace at the beginning of the line with &nbsp;
	# txt = re.sub(r'(?m)(?<=^)([ ]{2,}|\t+)(?=.)', '&nbsp;' * 4, txt)

	# Condense redundant spaces
	txt = re.sub(r'[ ]{2,}', ' ', txt)

	# Remove blank space from the beginning and end of the document.
	txt = re.sub(r'^\s+(?=.)', '', txt)
	txt = re.sub(r'(?<=.)\s+$', '', txt)
	# Remove excessive line breaks.
	txt = re.sub(r'\n{3,}', '\n\n', txt)
	# remove ASCII invalid chars : 0 to 8 and 11-14 to 24
	txt = clean_ascii_chars(txt)

	return chinese_converter.to_simplified(txt)


def ascii_pat(for_binary=False):
	attr = 'binary' if for_binary else 'text'
	ans = getattr(ascii_pat, attr, None)
	if ans is None:
		chars = set(range(32)) - {9, 10, 13}
		chars.add(127)
		pat = '|'.join(map(codepoint_to_chr, chars))
		if for_binary:
			pat = pat.encode('ascii')
		ans = re.compile(pat)
		setattr(ascii_pat, attr, ans)
	return ans


def clean_ascii_chars(txt, charlist=None):
	r"""
	Remove ASCII control chars.
	This is all control chars except \t, \n and \r
	"""
	is_binary = isinstance(txt, bytes)
	empty = b'' if is_binary else ''
	if not txt:
		return empty

	if charlist is None:
		pat = ascii_pat(is_binary)
	else:
		pat = '|'.join(map(codepoint_to_chr, charlist))
		if is_binary:
			pat = pat.encode('utf-8')
	return pat.sub(empty, txt)


def read_image(filename):
	image = None
	if filename and os.path.isfile(filename):
		image = read_binary_file(filename)
	return image


def extractHtml(url: str, netloc="", cookies=None):
	if not (url.startswith("http://") or url.startswith("https://")):
		if netloc and netloc not in url:
			url = "http://" + netloc + url
		else:
			url = "http://" + url

	try:
		urlparse(url)
	except AttributeError:
		logger.warning(f"Error: {url} is not a valid url!")
		return None
	retry = 7
	while retry > 0:
		try:
			r = requests.get(url, headers={'User-Agent': UA.get()}, cookies=cookies)
			r.raise_for_status()
			return r
		except HTTPError as ex:
			logger.warning(f"Error: Downloading {url} with {ex}")
			if ex.response.status_code == 429:
				sleep(10 - retry)
			retry -= 1
		except Exception as ex:
			logger.warning(f"Error: Downloading {url} with {ex}")
			UA.renew()
			retry -= 1
	return None


def extractHtmlImage(url: str):
	image = None
	response = extractHtml(url)
	if response and response.status_code == 200:
		image = response.content
	return image


def extractHtmlText(url: str, netloc="", cookies=None):
	text = ""
	response = extractHtml(url, netloc, cookies)
	if response and response.status_code == 200:
		try:
			text = str(response.content, 'utf-8')
		except UnicodeDecodeError:
			text = str(response.content, 'GBK', errors="ignore")
		except (LookupError, TypeError):
			text = str(response.content, errors="replace")
		text = unquote(text, response.encoding)
	return text


def extractHtmlSoup(url: str, netloc="", cookies=None):
	soup = None
	text = extractHtmlText(url, netloc, cookies)
	if text:
		soup = BeautifulSoup(text, 'html5lib')
	return soup


def getKindleGenBin():
	my_platform = sys.platform
	executable = os.path.join(ROOT_DIR, 'bin', 'kindlegen')
	if my_platform.startswith("linux"):
		executable += '-linux'
	elif my_platform.startswith("darwin"):
		executable += '-mac'
	return executable

def getCalibreCli():
	return "ebook-convert"

def randomRGB():
	return random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)


def generate_cover(title_raw, author):
	path = os.path.join(ROOT_DIR, "images")
	if not os.path.exists(path):
		logger.error(f"Image folder not found {path}")
		return None
	files = os.listdir(path)
	if len(files) == 0:
		logger.error(f"Image files not found {path}")
		return None
	try:
		img = Image.open(os.path.join(path, random.choice(files)))
	except Exception as e:
		logger.error(e)
		return None

	d = ImageDraw.Draw(img)
	x, y = 60, 40
	if len(title_raw) > 7:
		x = 20
	title = ""
	while len(title_raw) > 7:
		title = "\n\t" + title_raw[-7:] + title
		title_raw = title_raw[0:-7]
	title = title_raw + title
	font_path = os.path.join(ROOT_DIR, "static", "msz.ttf")
	if not os.path.exists(font_path):
		logger.error(f"Font file not found {font_path}")
	fnt = ImageFont.truetype(font_path, 40)
	outer = randomRGB()
	inner = randomRGB()
	d.text((x, y), title, font=fnt, fill=outer, direction="ttb", stroke_width=1)
	d.text((x, y), title, font=fnt, fill=inner, direction="ttb", stroke_width=0.4)

	while len(author) < 10:
		author = "\t" + author
	fnt = ImageFont.truetype(font_path, 30)
	d.text((160, 50), author, font=fnt, fill=outer, direction="ttb", stroke_width=1)
	d.text((160, 50), author, font=fnt, fill=inner, direction="ttb", stroke_width=0.4)

	buffer = io.BytesIO()
	img.save(buffer, format="JPEG")
	return buffer.getvalue()


class LocalBookStatus:
	def __init__(self):
		self.status = {}

	def add(self, key):
		if not self.status.get(key):
			self.status[key] = {"is_collapsed": "", "intro": "", "image": "", "txt": "", "epub": "", "mobi": "", "pdf": "", "azw": "", "chapter": ""}

	def remove(self, key):
		return self.status.pop(key, None)

	def get_value(self, key, sub_key):
		item = self.status.get(key)
		if not item:
			self.add(key)
			return self.get_value(key, sub_key)
		else:
			return item.get(sub_key)

	def set_value(self, key, sub_key, value):
		item = self.status.get(key)
		if not item:
			self.add(key)
			self.set_value(key, sub_key, value)
		else:
			item[sub_key] = value

	def get_value_if_key(self, key, sub_key):
		item = self.status.get(key)
		if item:
			return item.get(sub_key)
		return None

	def set_value_if_key(self, key, sub_key, value):
		item = self.status.get(key)
		if item:
			item[sub_key] = value

	def append_value_if_key(self, key, sub_key, value):
		item = self.status.get(key)
		if item:
			item[sub_key] = value if item[sub_key] is None else item[sub_key] + "\n" + value


class UrlBookStatus(LocalBookStatus):
	def add(self, key):
		if not self.status.get(key):
			self.status[key] = {"intro": "", "is_collapsed": "", "content": "", "chapter": "", "thread": Thread(),
			                    "message": "",
			                    "image": "", "txt": "", "epub": ""}


class EpubConverter:
	def __init__(self, data, extra="", img_filename="", txt_filename="", path=""):
		self.data = data
		self.extra = extra
		self.path = ConfigIO.get("ebook_dir") if not path else path
		self.title, self.author, self.tags, self.des = get_meta_data(self.extra, self.data, txt_filename)
		self.info = ""
		self.image = read_image(img_filename)
		self.ebook = epub.EpubBook()

	def create_txt(self):
		if not self.title:
			logger.error(f"Can't save extracted content to file: failed to extract title")
			return ""
		txt_path = os.path.join(ConfigIO.get("ebook_dir"), self.title + ".txt")
		write_text_file(self.data, txt_path)
		logger.info(f"save extracted content to {txt_path}")
		return txt_path

	def get_chaps(self, indices):
		chaps = ()
		start = 0
		for count, index in enumerate(indices):
			if start == index:
				continue
			temp = self.data[start: index].lstrip()
			start = index

			if "\n" in temp:
				header = temp.split("\n", 1)[0]
				content = temp.split("\n", 1)[1].replace("\n", '</p><p>')
			elif len(temp) > 10:
				header = temp[:10]
				content = temp
			else:
				header = temp
				content = ""
			info = f"Chapter {count}: title={header}, size={len(temp)}"
			logger.info(info)
			self.info += info + "\n"
			chap = epub.EpubHtml(title=header, file_name="%05d.xhtml" % count, lang="zh")
			chap.content = ("<h3>%s</h3><p>%s</p>" % (header, content))
			chaps = chaps + (chap,)
			self.ebook.toc.append(epub.Link("%05d.xhtml" % count, header, "%05d" % count))
			self.ebook.add_item(chap)
		return chaps

	def split_txt(self):
		"""
		Split text by the most common chapter regex
		"""
		start = 0
		end = len(self.data)
		indices = []
		while end - start > 8000:
			diff = -1
			for regex in chap_regex_list:
				found = re.search(regex, self.data[start: start + 8000])
				if found:
					diff = found.start()
					indices.append(start + diff)
					start += found.end()
					break
			if diff == -1:  # no chap with known regex found smaller than 8000 words
				diff = self.data[start:start + 8000].rfind("\n")  # find the last occurrence of line breaker
				if diff == -1:
					start += 8000
				else:
					start += diff
				indices.append(start)
		indices.append(end)
		return indices

	def convert(self):
		if not self.data:
			logger.error("Error: the resource txt file has no content")
			return ""
		if not self.title:
			logger.error("Error: can not convert txt file with no title")
			return ""
		logger.info(
			f"initiating txt to epub converting of {self.title}, author: {self.author}, tags={self.tags}, des={self.des}, length {len(self.data)}")

		title = clean_txt(self.title)
		pos = title.find("(", 1)  # remove pattern like (www.xxx.org)
		title = title[:pos] if pos != -1 else title  # don't change the file name, only the metadata title

		# set metadata
		self.ebook.set_identifier(title + self.author)
		self.ebook.set_title(title)
		self.ebook.set_language("zh")
		self.ebook.add_author(self.author)
		if not self.image:
			self.image = generate_cover(title, self.author)
		self.ebook.set_cover(file_name="cover.jpg", content=self.image)
		if self.tags:
			for t in re.split(r"[ ,，.。;；\\、｜|]", self.tags):
				self.ebook.add_metadata('DC', 'subject', t)
		if self.des:
			self.ebook.add_metadata('DC', 'description', self.des)

		# create add intro page
		intro = epub.EpubHtml(title="简介", file_name="intro.xhtml", lang="zh")
		intro.content = ("<h2>%s</h2><h3>作者：%s</h3><h3>内容标签：%s</h3><p>%s</p>" %
		                 (title, self.author, self.tags, self.des.replace("\n", "</p><p>")))
		self.ebook.toc.append(epub.Link("intro.xhtml", "简介", "intro"))
		self.ebook.add_item(intro)

		# create and add chapters
		indices = self.split_txt()
		chaps = self.get_chaps(indices)

		# add default NCX and Nav file
		self.ebook.add_item(epub.EpubNcx())
		self.ebook.add_item(epub.EpubNav())

		# define CSS style
		style = '''p {text-indent: 0.5em;}'''
		default_css = epub.EpubItem(
			uid="style_default",
			file_name="style/default.css",
			media_type="text/css",
			content=style)
		self.ebook.add_item(default_css)

		# basic spine
		self.ebook.spine = ["nav", intro, *chaps]

		# write to the file
		file_path = os.path.join(self.path, self.title + ".epub")
		epub.write_epub(file_path, self.ebook, {})
		logger.info(f"Success: {self.title} converting done. number of chapters: {len(indices)}")
		return file_path


def getArticleBody(soup: BeautifulSoup):
	longest_text = ""
	max_length = 0
	for div in soup.find_all(['div', 'article']):
		for t in ['a', 'article', 'button', 'div', 'img', 'nav',' script', 'span']: [s.extract() for s in div.find_all(t)]
		text = "\n".join(div.stripped_strings)
		if len(text) > max_length:
			longest_text = text
			max_length = len(text)
	return longest_text

def getArticleBodyDiverse(soup: BeautifulSoup, attr):
	if not soup:
		return ""
	if not attr:
		return getArticleBody(soup)
	element = soup.find(attrs=attr)
	if element:
		return "\n".join(element.stripped_strings)


class EbookWebExtractor:
	def __init__(self, url, args: dict, updater=None):
		self.url = url
		self.split_utl = urlsplit(url)
		self.args = args
		self.updater = updater
		self.info = {}
		self.setup()
		self.content = ""
		self.error = ""

	def getSoup(self, url):
		soup = extractHtmlSoup(url, netloc=self.split_utl.netloc)
		if not soup:
			logger.error(f"Failed to download page {url}\n")
			self.error += f"Failed to download page {url}\n"
		return soup

	def updatePageInfo(self, text, idx, total_pages):
		temp = text.replace("\n", "\t")
		temp = f"({idx}/{total_pages}) {temp[0:40] if len(temp) > 40 else temp}"
		logger.info(temp)
		self.content = temp + "\n" + self.content
		# self.updater.set_value_if_key(self.url, "content", self.content)
		if self.updater:
			self.updater["content"] = self.content

	def setup(self):
		# for p in ConfigIO.get("web_parsers"):  # known network location from config
		# 	if p.get("base", "") == self.split_utl.netloc:
		# 		for key, value in p.items():
		# 			self.info[key] = value
		for k, v in self.args.items():  # input info manually, override parsed info
			self.info[k] = v
		logger.info(f"Gathered info: {self.info.__str__()}")

	def extract(self):
		method = self.info.get("method", "index")
		if method == "index":
			logger.debug(f"Fetching page urls from index page: {self.url}")
			self.set_pages_index()
			text = self.extract_index()
		elif method == "next":
			logger.debug(f"Fetching page urls from first page: {self.url}")
			text = self.extract_traverse()
		else:
			logger.warning(f"Please input extracting method - index or next(page by page): {self.url}")
			text = ""
		text = clean_txt(text)
		text = re.sub(self.info.get('watermark', ""), '', text)
		if len(text) < 10000:
			logger.warning(f"Suspected download failure. Article is too short: {len(text)}")
		else:
			logger.info(f"Download Success. Word count: {len(text)}")
		return text

	def extract_index(self):
		if not self.info.get("pages"):
			logger.error(f"Failed to get pages from index page {self.url}")
			self.error += f"Failed to get pages from index page {self.url}"
			return ""
		logger.info(f"Extracting {len(self.info.get("pages"))} pages from {self.url}")
		text = self.info.get("intro") + "\n" if self.info.get("intro") else ""
		attr = self.info.get("content_tag") if self.info.get("content_tag") else {}
		for idx, page in enumerate(self.info.get("pages")):
			temp = getArticleBodyDiverse(self.getSoup(page), attr)
			if not temp:
				logger.error(f"Failed to extract page {page}, use content tag: {attr}?")
				self.error += f"Failed to extract page {page}, use content tag: {attr}?"
				break
			self.updatePageInfo(temp, idx, len(self.info.get("pages")))
			text += temp
			sleep(0.5)  # to avoid 429 Client Error: Too Many Requests
		return text

	def extract_traverse(self):
		text = self.info.get("intro") + "\n" if self.info.get("intro") else ""
		n = self.info.get("next_tag") if self.info.get("next_tag") else ["下一页", "下一頁", "下一章"]
		attr = self.info.get("content_tag") if self.info.get("content_tag") else {}
		url = self.url
		page = 0
		while True:
			soup = self.getSoup(url)
			href = ""
			for n_text in n:
				n_element = soup.find(string=re.compile(rf'{n_text}'))
				if n_element:
					href = n_element.parent.get("href")
					if href:
						break
			temp = getArticleBodyDiverse(soup, attr)
			if not temp:
				logger.error(f"Failed to extract page {url}, use content tag: {attr}?")
				self.error += f"Failed to extract page {url}, wrong content tag: {attr}"
				break
			self.updatePageInfo(temp, page, 'unknown')
			text += temp

			if not href:
				logger.info(f"Last page {url}\n")
				break
			url = href
			page = page + 1
			sleep(0.5)  # to avoid 429 Client Error: Too Many Requests
		return text

	def set_pages_index(self):
		soup = extractHtmlSoup(self.url)
		if not soup:
			return
		hrefs = []
		pages = set()
		attr = self.info.get("index_tag")
		if attr:  # input contains index tag
			element = soup.find(attrs=attr)
			if element:
				pages = element.find_all("a")
		else:  # there are many unordered list in html, I bet the longest list is the page list
			uls = soup.find_all("ul")
			for ul in uls:
				a = ul.find_all("a")
				if a and len(a) > len(pages):
					pages = a
		for p in pages:
			h = p.get("href")
			if h:
				hrefs.append(h)
		self.info["pages"] = hrefs
