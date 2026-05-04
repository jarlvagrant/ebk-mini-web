import errno
import logging
import os
import re
import subprocess
import threading

import pinyin
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

from flask import typing as ft, render_template, Response, request, jsonify, send_from_directory
from flask.views import View

from EbookUtils import clean_txt, EbookWebExtractor, EpubConverter, getKindleGenBin, LocalBookStatus, UrlBookStatus, \
	extractHtmlImage, getCalibreCli
from Utils import ConfigIO, SendEmail, getInitialFolder, getSubfolders, log_path, read_binary_file, SymlinkIO, ImageIO

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
		t = url_book_dict.get_value_if_key(key, 'thread')
		message = url_book_dict.get_value_if_key(key, 'message')
		stop = False if t and t.is_alive() else True
		return jsonify(code=200, content=content, chapter=chapter, txt=txt, epub=e_pub, stop=stop, message=message)


file_types = ['epub', 'txt', 'pdf', 'mobi', 'azw']


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
				files[stem].update({ext: {'path': file_path, 'date': os.path.getctime(file_path)}})
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
		indent += 1
		current = os.path.dirname(current)
		if current == os.sep and current != parent:
			return 0
	return indent


def sort_files_by(key, files, reverse=False):
	logger.debug(f"Sorting by {key} reverse {reverse}")
	match key:
		case "name":
			sorted_files = dict(
				sorted(files.items(), key=lambda item: pinyin.get(item[0], format="strip"), reverse=reverse))
		case "folder":
			sorted_files = dict(
				sorted(files.items(), key=lambda item: pinyin.get(item[1]['dp'], format="strip"), reverse=reverse))
		case "date":
			sorted_files = dict(sorted(files.items(), key=lambda item: item[1]['date'], reverse=reverse))
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


busy_hosts = {str: threading.Event()}


class EbookExtractorTask(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		url = request.form.get("url")
		if not url:
			return jsonify(code=500, message="No url specified")

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
		t = Thread(target=extractor_worker, args=(url, args, to_email))
		url_book_dict.set_value(url, "thread", t)
		t.start()
		return jsonify(code=200, message="Extracting {url}")


def extractor_worker(url, args, to_email):
	url_netloc = urlsplit(url).netloc
	logger.debug(f"Busy hosts: {busy_hosts.keys().__str__()}")
	if busy_hosts.get(url_netloc):
		logger.debug(f"Waiting for {url_netloc} to be free...")
		busy_hosts.get(url_netloc).wait()
	else:
		busy_hosts[url_netloc] = threading.Event()
	busy_hosts[url_netloc].clear()
	extractor = EbookWebExtractor(url, args, url_book_dict.status.get(url))
	text = extractor.extract()
	content = text[0:2000] if len(text) > 2000 else text
	url_book_dict.set_value(url, "content", content)
	url_book_dict.set_value_if_key(url, "message", extractor.error)
	logger.debug(f"Host {url_netloc} freed")
	busy_hosts[url_netloc].set()

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
		target_ext = request.form.get('target', '')
		file_name, source_ext = os.path.splitext(file_path)
		res, message, epub, mobi = 200, '', '', ''
		logger.error(f"file->{file_path}, is file->{os.path.isfile(file_path)} target->{target_ext}")
		if not (file_path and os.path.isfile(file_path) and target_ext):
			return jsonify(code=500, message=f"Failed converting: missing info: file->{file_path}, target->{target_ext}")

		if source_ext == '.txt' and target_ext == '.mobi':
			_, message, epub = txtToEpub(file_name, source_ext)
			source_ext = '.epub'
		if source_ext == '.txt' and target_ext == '.epub':
			res, temp, epub = txtToEpub(file_name, source_ext)
			message += '\n' + temp
		elif source_ext == '.epub' and target_ext == '.mobi':
			res, temp, mobi = epubToMobi(file_name, source_ext)
			message += '\n' + temp
		return jsonify(code=res, message=message, epub=epub, mobi=mobi)

def txtToEpub(file_name, source_ext):
	if not (file_name and source_ext == '.txt' and os.path.isfile(file_name + source_ext)):
		return 501, f"Failed converting: source path error: {file_name}{source_ext}!", ''
	txt_path = file_name + source_ext
	epub_path = file_name + '.epub'
	converter = EpubConverter(clean_txt(read_binary_file(txt_path)), "", None, clean_txt(Path(txt_path).stem),
		                          os.path.dirname(txt_path))
	output = converter.convert()
	res, message, output = 501, f"Failed converting {txt_path} to {epub_path}, output->{output}!", output
	if os.path.isfile(epub_path):
		res, message, output = 200, f"Succeed converting {txt_path} to {epub_path}!", epub_path
	return res, message, output

def epubToMobi(file_name, source_ext):
	if not (file_name and source_ext == '.epub' and os.path.isfile(file_name + source_ext)):
		return 501, f"Failed converting: source path error: {file_name}{source_ext}!", ''
	epub_path = file_name + source_ext
	mobi_path = file_name + '.mobi'
	gen = getCalibreCli()
	res, message, output = 502, f"Failed converting {gen}, {epub_path}, '-o', {mobi_path}!", ''
	try:
		subprocess.check_call([gen, epub_path, mobi_path])
	except subprocess.CalledProcessError as e:
		logger.error(e)
		res, message, output = 503, "Failed converting: " + e.__str__(), ''
	if os.path.isfile(mobi_path):
		res, message, output = 200, f"Succeed converting {epub_path} to {mobi_path}!", mobi_path
	return res, message, output


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
			for k, v in url_book_dict.status.items():
				remove_cached_files(v)
			for k, v in local_book_dict.status.items():
				remove_cached_files(v)
			url_book_dict.status.clear()
			local_book_dict.status.clear()
		else:
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
