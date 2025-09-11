import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from flask import Flask, render_template
from flask.views import View
from werkzeug.serving import WSGIRequestHandler

from Commons import UpdateDir, UpdateConfig, ListSubfolders
from EbookSender import EBook, EbookUploads, EbookConverterTask, EbookCover, EbookCoverUrl, EbookEmail, \
	EbookUrls, EbookExtractorTask, EbookSyncInput, EbookDownload, EbookRemoveItem, EbookSyncOutput, \
	EbookPreview, EbookToFormat, EbookListfilesMain, EbookListfiles
from Utils import log_path, log_file


class Index(View):
	def dispatch_request(self):
		return render_template('index.html')

class MyRequestHandler(WSGIRequestHandler):
	def log_request(self, code='-', size='-'):
		pass

class HTTPFilter(logging.Filter):
		def filter(self, record: logging.LogRecord) -> bool:
			if record.name == "EbookSender":
				return True
			return False

def init_logging():
	if not os.path.isdir(log_path):
		os.mkdir(log_path)
	# http_handler: logging.Handler = HTTPHandler("localhost:8008", "/log", method="GET")
	# http_handler.addFilter(HTTPFilter())
	logging.basicConfig(
		handlers=[RotatingFileHandler(os.path.join(log_path, log_file), maxBytes=100000, backupCount=10),
		          # http_handler,
		          logging.StreamHandler(sys.stdout)],
		format="[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s",
		datefmt='%Y-%m-%d %H:%M:%S',
		level=logging.DEBUG)


def main():
	init_logging()

	app = Flask(__name__)
	app.secret_key = 'mimamuahilachocobooooo'
	app.add_url_rule("/", view_func=Index.as_view("index"))
	app.add_url_rule("/update_dir", methods=['POST'], view_func=UpdateDir.as_view("update_dir"))
	app.add_url_rule("/update_config", methods=['POST'], view_func=UpdateConfig.as_view("update_config"))
	app.add_url_rule("/list_subfolders", methods=['POST'], view_func=ListSubfolders.as_view("list_subfolders"))
	app.add_url_rule("/ebook", methods=['GET'], view_func=EBook.as_view("ebook"))
	app.add_url_rule("/ebk_urls", methods=['GET', 'POST'], view_func=EbookUrls.as_view("ebk_urls"))
	app.add_url_rule("/ebk_uploads", methods=['GET', 'POST'], view_func=EbookUploads.as_view("ebk_upload"))
	app.add_url_rule("/ebk_listfiles", methods=['GET'], view_func=EbookListfiles.as_view("ebk_listfiles"))
	app.add_url_rule("/ebk_listfiles_main", methods=['GET', 'POST'], view_func=EbookListfilesMain.as_view("ebk_listfiles_main"))
	app.add_url_rule("/ebk_sync_input", methods=['POST'], view_func=EbookSyncInput.as_view("ebk_sync_input"))
	app.add_url_rule("/ebk_sync_output", methods=['POST'], view_func=EbookSyncOutput.as_view("ebk_sync_output"))
	app.add_url_rule("/ebk_cover", methods=['POST'], view_func=EbookCover.as_view("ebk_cover"))
	app.add_url_rule("/ebk_cover_url", methods=['POST'], view_func=EbookCoverUrl.as_view("ebk_cover_url"))
	app.add_url_rule("/ebk_convert_task", methods=['POST'], view_func=EbookConverterTask.as_view("ebk_convert_task"))
	app.add_url_rule("/ebk_extractor_task", methods=['POST'], view_func=EbookExtractorTask.as_view("ebk_extractor_task"))
	app.add_url_rule("/ebk_download", methods=['GET'], view_func=EbookDownload.as_view("ebk_download"))
	app.add_url_rule("/ebk_email", methods=['POST'], view_func=EbookEmail.as_view("ebk_email"))
	app.add_url_rule("/ebk_remove", methods=['POST'], view_func=EbookRemoveItem.as_view("ebk_remove"))
	app.add_url_rule("/ebk_preview", methods=['POST'], view_func=EbookPreview.as_view("ebk_preview"))
	app.add_url_rule("/ebk_to_format", methods=['POST'], view_func=EbookToFormat.as_view("ebk_to_format"))
	app.run(debug=True, host='0.0.0.0', port=8008, request_handler=MyRequestHandler)


if __name__ == "__main__":
	main()
