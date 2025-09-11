import logging
import os

from flask import typing as ft, request, jsonify
from flask.views import View

from Utils import ConfigIO, getSubfolders


logger = logging.getLogger(__name__)


class UpdateConfig(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		keys = request.form.get("key").split(" ")
		value = request.form.get("value")
		if len(keys) == 1:
			ConfigIO.set(keys[0], value)
		else:
			ConfigIO.set(keys[0], value, subkey=keys[1])
		logger.debug(f"Updating config: {keys} = {value}")
		return jsonify(code=200)


class UpdateDir(View):
	"""
	Using a POST method to retrieve the javascript id, value pair for one of the
	archive path(video, audio, book...)
	Response is the validated retrieved path. If this path is not a valid directory,
	send the previous path as response.
	"""
	def dispatch_request(self) -> ft.ResponseReturnValue:
		new_path = request.form.get("dir")
		dir_type = request.form.get("id")
		old_path = ConfigIO.get(dir_type)
		code = 500
		logger.debug(f"Updating directory: {dir_type} = {new_path}")
		if new_path and os.path.isdir(new_path):
			code = 200
			ConfigIO.set(dir_type, new_path)
		elif old_path and os.path.isdir(old_path):
			code = 201
			new_path = old_path
		else:
			new_path = os.sep
			ConfigIO.set(dir_type, new_path)
		logger.debug(f"Updated directory: {dir_type} = {new_path}, code = {code}")
		return jsonify(code=code, dir=new_path)


class ListSubfolders(View):
	def dispatch_request(self) -> ft.ResponseReturnValue:
		cur_dir = request.form.get("cur_dir")
		folders = getSubfolders(cur_dir)
		logger.debug(f"List subfolders of {cur_dir}: {folders}")
		return jsonify(folders=folders)