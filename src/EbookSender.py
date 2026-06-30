import errno
import logging
import os
import re
import subprocess
import threading
import time
from logging import Logger

import pinyin
from pathlib import Path
from urllib.parse import urlsplit

from flask import typing as ft, render_template, Response, request, jsonify, send_from_directory
from flask.views import View

from EbookUtils import clean_txt, EbookWebExtractor, EpubConverter, getKindleGenBin, LocalBookStatus, UrlBookStatus, \
	extractHtmlImage, getCalibreCli
from Utils import ConfigIO, SendEmail, getInitialFolder, getSubfolders, log_path, read_binary_file, SymlinkIO, ImageIO
from src.Utils import ThreadWithTrace

logger = logging.getLogger(__name__)

local_book_dict = LocalBookStatus()

url_book_dict = UrlBookStatus()


class EBook(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		folder = getInitialFolder('ebook_dir')
		subfolders = getSubfolders(folder)
		log = os.path.join(log_path, "debug.log")
		return render_template("ebk.html", ebook_dir=folder, folders=subfolders,
		                       recipient=ConfigIO.get("email", "to"), log=log)


class EbookSyncInput(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		key = request.form.get("key")
		sub_key = request.form.get("sub_key")
		value = request.form.get("value")
		local_book_dict.set_value_if_key(key, sub_key, value)
		url_book_dict.set_value_if_key(key, sub_key, value)
		logger.debug(f"Synced {key}: {sub_key} -> {value}")
		return jsonify(code=200)


class EbookSyncOutput(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		key = request.form.get("key")
		content = url_book_dict.get_value_if_key(key, 'content')
		chapter = url_book_dict.get_value_if_key(key, 'chapter')
		e_pub = url_book_dict.get_value_if_key(key, 'epub')
		txt = url_book_dict.get_value_if_key(key, 'txt')
		stop = True
		for thread in threading.enumerate():
			if thread.name == key and thread.is_alive():
				stop = False
				break
		message = url_book_dict.get_value_if_key(key, 'message')
		return jsonify(code=200, content=content, chapter=chapter, txt=txt, epub=e_pub, stop=stop, message=message)


file_types = ['epub', 'txt', 'pdf', 'mobi', 'azw3']


class EbookListfiles(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		return render_template('ebk_listfiles.html')


class EbookListfilesMain(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		sort_by = request.form.get("sort_by")
		filter_by = request.form.get("filter_by")
		reverse_int = request.form.get("reverse", type=int)
		reverse = True if reverse_int == 1 else False
		path = request.form.get("path", ConfigIO.get("ebook_dir"))
		files = {}
		folders = {}
		if not os.path.isdir(path):
			logger.error(f"Invalid upload path: {path}")
			return Response(f'Invalid upload path: {path}', status=404)
		for file in os.listdir(path):
			file_path = os.path.join(path, file)
			if os.path.islink(file_path):
				continue
			if os.path.isdir(file_path):
				folders[file] = file_path
				continue

			stem, ext = os.path.splitext(file)
			ext = ext.lstrip(".")
			if ext in file_types:
				if stem not in files.keys():
					files[stem] = {'parent': path + os.path.sep}
				files[stem].update({ext: {'path': file_path, 'date': os.path.getmtime(file_path), 'size': os.path.getsize(file_path)}})
		if sort_by:
			files = sort_files_by(sort_by, files, reverse=reverse)
		logger.debug(files.__str__())
		return render_template('ebk_listfiles_main.html', files=files, folders=folders,
		                       indent=getIndent(path, ConfigIO.get("ebook_dir")))


def getIndent(path, parent):
	if path and parent and path == parent or path == os.sep:
		return 0
	indent = 0
	current = path
	while current != parent:
		indent += 20
		current = os.path.dirname(current)
		if current == os.sep and current != parent:
			return 0
	return f"{indent}px"


def sort_files_by(key, files, reverse=False):
	logger.debug(f"Sorting by {key} reverse {reverse}")
	match key:
		case "name":
			sorted_files = dict(
				sorted(files.items(), key=lambda item: pinyin.get(item[0], format="strip"), reverse=reverse))
		case "size":
			if reverse:
				sorted_files= dict(sorted(files.items(), key=lambda item: max(val['size'] for key, val in item[1].items() if key != 'parent'), reverse=reverse))
			else:
				sorted_files= dict(sorted(files.items(), key=lambda item: min(val['size'] for key, val in item[1].items() if key != 'parent'), reverse=reverse))
		case "date":
			if reverse:
				sorted_files= dict(sorted(files.items(), key=lambda item: max(val['date'] for key, val in item[1].items() if key != 'parent'), reverse=reverse))
			else:
				sorted_files= dict(sorted(files.items(), key=lambda item: min(val['date'] for key, val in item[1].items() if key != 'parent'), reverse=reverse))
		case _:
			sorted_files = files
	return sorted_files


class EbookUploads(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		if request.method == "POST":
			path = ConfigIO.get("ebook_dir")
			if not os.path.isdir(path):
				logger.error(f"Invalid upload path: {path}")
				return Response(f'Invalid upload path: {path}', status=404)
			else:
				files = request.files.getlist("docs")
				for f in files:
					file_name = clean_txt(f.filename)
					file_path = os.path.join(path, file_name)
					_, file_extension = os.path.splitext(file_path)
					f.save(file_path)
					local_book_dict.set_value(file_name, file_extension.lstrip('.'), file_path)
					logger.debug(f"Uploaded file: {file_path}")
				return Response(status=200)
		return render_template("ebk_uploads.html", txt_files=local_book_dict.status)


class EbookDownload(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		file_path = request.args.get('file_path')
		logger.debug(f"Downloading request {file_path}")
		if not os.path.isfile(file_path):
			logger.warning(f"Invalid downloading path: {file_path}")
			return Response(f"Invalid downloading path: {file_path}", status=404)

		path = Path(file_path).parent
		file = Path(file_path).name
		file_name, file_extension = os.path.splitext(file)
		link = link_mobi(path, file_name, file_extension)
		if link:
			path = Path(link).parent
			file = Path(link).name
		logger.info(f"Downloaded directory={path} file={file}")
		return send_from_directory(path, path=file)


def link_mobi(path, file_name, file_extension):
	if file_extension != ".mobi" or pinyin.get(file_name, format="strip") == file_name:
		return ""
	mobi_pinyin = pinyin.get(file_name, format="strip")
	mobi_pinyin_path = os.path.join(SymlinkIO.getPath(), mobi_pinyin + file_extension)
	mobi_path = os.path.join(path, file_name + file_extension)
	logger.debug(f"Create symlink {mobi_pinyin_path}->{mobi_path}")
	try:
		os.symlink(mobi_path, mobi_pinyin_path)
	except OSError as e:
		if e.errno == errno.EEXIST and os.path.islink(mobi_pinyin_path):
			os.unlink(mobi_pinyin_path)
			os.symlink(mobi_path, mobi_pinyin_path)
		else:
			logger.warning("Failed create symlink %s->%s: %s" % (mobi_pinyin_path, mobi_path, e))
	return mobi_pinyin_path


class EbookPreview(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		file_path = request.form.get('file_path')
		file_name, file_extension = os.path.splitext(file_path)
		data = b""
		if os.path.isfile(os.path.join(file_name + ".txt")):
			with open(os.path.join(file_name + ".txt"), "rb") as f:
				while len(data) < 4096:
					buffer = f.readline()
					if not buffer:
						break
					data += buffer
				f.close()
			logger.debug(f"preview file path: {os.path.join(file_name + ".txt")}")
			return jsonify(code=200, data=clean_txt(data))
		else:
			logger.error(f"Invalid preview file path: {os.path.join(file_name + ".txt")}")
			return jsonify(code=200, message=f"Invalid preview file path: {os.path.join(file_name + ".txt")}")


class EbookUrls(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		if request.method == "POST":
			new_url = request.form.get('new_url')
			if new_url:
				url_book_dict.add(new_url)
		return render_template("ebk_urls.html", book_urls=url_book_dict.status)


busy_hosts = {str: threading.Lock()}


class EbookExtractorTask(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		url = request.form.get("url")
		if not url:
			return jsonify(code=500, message="No url specified")
		for thread in threading.enumerate():
			if thread.name == url and thread.is_alive():
				return jsonify(code=500, message=f"Ongoing task extracting {url} ")

		path = ConfigIO.get("ebook_dir")
		if not os.path.isdir(path):
			return jsonify(code=501, message=f"Invalid upload path: {path}")

		intro = request.form.get("intro")
		method = request.form.get("method", "index")
		index_tag_k = request.form.get("index_tag_k")
		index_tag_v = request.form.get("index_tag_v")
		index_tag = {index_tag_k: index_tag_v} if index_tag_k and index_tag_v else {}
		next_tag_string = request.form.get("next_tag")
		next_tag = [n.strip() for n in re.split(r"[,， ]", next_tag_string)] if next_tag_string else []
		content_tag_k = request.form.get("content_tag_k")
		content_tag_v = request.form.get("content_tag_v")
		content_tag = {content_tag_k: content_tag_v} if content_tag_k and content_tag_v else {}
		to_email = request.form.get("to_email")
		args = {"intro": clean_txt(intro), "method": method, "index_tag": index_tag, "next_tag": next_tag,
		        "content_tag": content_tag}

		logger.debug(f"Request to extract from {url}: {args.__str__()}, send_epub_to_email: {to_email}")
		t = ThreadWithTrace(target=extractor_worker, name=url, daemon=True, args=(url, args, to_email))
		t.start()
		return jsonify(code=200, message="Extracting {url}")


def extractor_worker(url, args, to_email):
	url_netloc = urlsplit(url).netloc
	logger.debug(f"Busy hosts: {busy_hosts.keys().__str__()}")
	lock = busy_hosts.get(url_netloc)
	if not lock:
		lock = threading.Lock()
		busy_hosts[url_netloc] = lock
	logger.debug(f"Is host {url_netloc} busy? {lock and lock.locked()}")
	while lock and lock.locked():
		time.sleep(3)
	logger.debug(f"Host {url_netloc} is free")
	# block other thread
	lock.acquire_lock()
	extractor = EbookWebExtractor(url, args, url_book_dict.status.get(url))
	text = extractor.extract()
	content = text[0:2000] if len(text) > 2000 else text
	url_book_dict.set_value(url, "content", content)
	url_book_dict.set_value_if_key(url, "message", extractor.error)
	logger.debug(f"Host {url_netloc} freed")
	lock.release()

	# converter task doesn't need to be waiting for
	converter = EpubConverter(text, img_filename=url_book_dict.get_value(url, "image"))
	txt_path = converter.create_txt()
	if not txt_path:
		url_book_dict.append_value_if_key(url, "message",
		                                  f"Can't save extracted content to file:  failed to extract title")
		return
	url_book_dict.set_value(url, "txt", txt_path)
	output = converter.convert()
	if not output:
		logger.error(f"Can't convert extracted content to epub")
		url_book_dict.append_value_if_key(url, "message", f"Can't convert extracted content to epub")
		return
	url_book_dict.set_value(url, "epub", output)
	url_book_dict.set_value(url, "chapter", converter.info)

	if to_email == "true":
		logger.debug(f"Sending email with attachment {output}")
		sender = SendEmail(output)
		sender.send()
		url_book_dict.append_value_if_key(url, "message", sender.message)


class EbookExtractorTaskStopper(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		url = request.form.get("url")
		for thread in threading.enumerate():
			if thread.name == url and thread.is_alive():
				logger.debug(f"Request to kill {url}")
				try:
					thread.kill()
					busy_hosts[urlsplit(url).netloc].release()
				except Exception as e:
					logger.warning(f"Can't kill {url}: {e}")
		return jsonify(code=200)


class EbookEmail(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		paths = request.form.getlist("paths[]")
		code = 200
		message = ""
		for path in paths:
			logger.debug(f"Sending email with attachment {path}")
			sender = SendEmail(path)
			code = 400 if not sender.send() else code
			message += sender.message + "\n"
		return jsonify(code=code, message=message)


class EbookConverterTask(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		key = request.form.get('key', '')
		intro = clean_txt(request.form.get('intro', ''))
		to_email = request.form.get('to_email')
		txt_path = local_book_dict.get_value(key, "txt")
		if not os.path.isfile(txt_path):
			return jsonify(code=500, messages="Input text file not found")

		data = clean_txt(read_binary_file(txt_path))
		converter = EpubConverter(data, intro, local_book_dict.get_value(key, "image"), Path(txt_path).stem)
		output = converter.convert()
		local_book_dict.set_value(key, "epub", output)
		local_book_dict.set_value(key, "chapter", converter.info)

		message = f"Convert success: {output}" if output else f"Failed to convert {key} to epub\n"
		if to_email == "true":
			logger.debug(f"Sending email with attachment {output}")
			sender = SendEmail(output)
			sender.send()
			message = sender.message
		return jsonify(code=200, chapter=converter.info, epub=output, message=message)


class EbookToFormat(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		file_path = request.form.get('file_path', '')
		ext_from = request.form.get('from', '')
		ext_to = request.form.get('to', '')
		return ext_from_to(file_path, ext_from, ext_to)

calibre_args = {'txt': [],
                'epub': [],
                'mobi': [],
                'azw3': [],
				'pdf': ["--chapter", "//h:h1", "--chapter-mark", "pagebreak", "--page-breaks-before", "//h:h1"]}

def ext_from_to(file_path, ext_from, ext_to):
	input_path = file_path + "." + ext_from
	output_path = file_path + "." + ext_to
	if not (input_path and os.path.isfile(input_path)):
		msg = f"Missing input file->{input_path}"
		logger.error(msg)
		return jsonify(code=500, message=msg, output="")

	if ext_from == 'txt' and ext_to == 'epub':
		converter = EpubConverter(clean_txt(read_binary_file(input_path)), "", None,
	                          clean_txt(Path(input_path).stem), os.path.dirname(input_path))
		output_path = converter.convert()
		if os.path.isfile(output_path):
			return jsonify(code=200, message=f"Succeed converting {input_path} to {output_path}!", output=output_path)
	else:
		gen = getCalibreCli()
		try:
			command = [gen, input_path, output_path] + calibre_args[ext_to]
			subprocess.check_call(command)
		except subprocess.CalledProcessError as e:
			logger.error(f"Error converting {input_path} to {output_path}: {e}")
		if os.path.isfile(output_path):
			return jsonify(code=200, message=f"Succeed converting {input_path} to {output_path}!", output=output_path)
	return jsonify(code=501, message=f"Failed converting {input_path} to {output_path}", output="")


class EbookCover(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		key = request.form.get('title')
		file = request.files.get('image')
		if key and file:
			image_name = file.filename
			image_path = os.path.join(ImageIO.getPath(), image_name)
			image = file.read()
			logger.debug(f"Save image: {image_name} to {image_path}")
			if image:
				with open(image_path, "wb") as f:
					f.write(image)
				local_book_dict.set_value_if_key(key, "image", image_path)
				url_book_dict.set_value_if_key(key, "image", image_path)
				return Response(status=200)
		return Response(status=500)


class EbookCoverUrl(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		key = request.form.get('title')
		url = request.form.get('img_url')
		if key and url:
			image = extractHtmlImage(url)
			image_name = Path(url).stem + ".jpg"
			image_path = os.path.join(ImageIO.getPath(), image_name)
			logger.debug(f"Save image: {url} to {image_path}")
			if image:
				with open(image_path, "wb") as f:
					f.write(image)
				local_book_dict.set_value_if_key(key, "image", image_path)
				url_book_dict.set_value_if_key(key, "image", image_path)
				return jsonify(code=200)
		return jsonify(code=500)


class EbookRemoveItem(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		key = request.form.get('key')
		to_all = request.form.get('all')
		if to_all:
			for thread in threading.enumerate():
				if thread.name in url_book_dict.status.keys():
					return jsonify(code=501, message="Stop active task and retry.")
			for k, v in url_book_dict.status.items():
				remove_cached_files(v)
			for k, v in local_book_dict.status.items():
				remove_cached_files(v)
			url_book_dict.status.clear()
			local_book_dict.status.clear()
		else:
			for thread in threading.enumerate():
				if thread.name == key and thread.is_alive():
					logger.debug("thread is still running")
					return jsonify(code=501, message=f"Thread {thread.name} is still running!")
			item = url_book_dict.remove(key)
			remove_cached_files(item)
			item = local_book_dict.remove(key)
			remove_cached_files(item)
		return jsonify(code=200)


def remove_cached_files(item):
	if not item:
		return
	# txt = item.get("txt")
	# if txt and os.path.isfile(txt):
	# 	os.remove(txt)
	image = item.get("image")
	if image and os.path.isfile(image):
		os.remove(image)

class EbookRenameItem(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		cur_name = request.form.get('curName')
		new_name = request.form.get('newName')
		cur_path = request.form.get('path')
		new_path = cur_path.removesuffix(cur_name) + new_name
		code, msg = 200, ""

		for t in file_types:
			if os.path.isfile(cur_path + "." + t):
				os.rename(cur_path + "." + t, new_path + '.' + t)
				if not os.path.isfile(new_path + "." + t):
					code = 401
					msg += f"File {new_path}.{t} not found."
		logger.debug(f"Rename {cur_name} to {new_name}: {code} {msg}")
		return jsonify(code=code, message=msg, path=new_path)

class EbookDeleteItem(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		path = request.form.get('path')
		formats = request.form.getlist('formats[]')
		logger.debug(f"Delete {path}.{formats}")
		if not path or len(formats) == 0:
			return jsonify(code=401, message=f"Nothing to delete: path={path}, formats={formats}")
		for f in formats:
			Path(path + '.' + f).unlink(missing_ok=True)
		return jsonify(code=200)