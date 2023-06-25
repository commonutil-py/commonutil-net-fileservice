# -*- coding: utf-8 -*-
"""
Adapter code for paramiko
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, List
import _thread
import logging
import os
import subprocess

import paramiko

from commonutil_net_fileservice.scp import SCPSinkHandler

_log = logging.getLogger(__name__)


def stream_exec_stdin(channel: paramiko.Channel, pobj: subprocess.Popen):
	loaded_bytes = 0
	streamed_bytes = 0
	try:
		while True:
			buf = channel.recv(2048)
			s = len(buf)
			if s == 0:
				pobj.stdin.close()
				_log.debug("stdin streamed %d/%d bytes", streamed_bytes, loaded_bytes)
				return
			loaded_bytes = loaded_bytes + s
			pobj.stdin.write(buf)
			streamed_bytes = streamed_bytes + s
	except Exception:
		_log.exception("cannot stream stdin data (streamed %d/%d bytes)", streamed_bytes, loaded_bytes)
		pobj.stdin.close()


def stream_exec_stdout(channel: paramiko.Channel, pobj: subprocess.Popen):
	loaded_bytes = 0
	streamed_bytes = 0
	try:
		while True:
			buf = pobj.stdout.read(2048)
			s = len(buf)
			if s == 0:
				_log.debug("stdout streamed %d/%d bytes", streamed_bytes, loaded_bytes)
				return
			loaded_bytes = loaded_bytes + s
			channel.sendall(buf)
			streamed_bytes = streamed_bytes + s
	except Exception:
		_log.exception("cannot stream stdout data (streamed %d/%d bytes)", streamed_bytes, loaded_bytes)


def stream_exec_stderr(channel: paramiko.Channel, pobj: subprocess.Popen):
	loaded_bytes = 0
	streamed_bytes = 0
	try:
		while True:
			buf = pobj.stderr.read(2048)
			s = len(buf)
			if s == 0:
				return
			loaded_bytes = loaded_bytes + s
			channel.sendall_stderr(buf)
			streamed_bytes = streamed_bytes + s
	except Exception:
		_log.exception("cannot stream stderr data (streamed %d/%d bytes)", streamed_bytes, loaded_bytes)


def rewrite_target_path(user_folder_path: str, target_path: str) -> str:
	if (not target_path) or (target_path == '.'):
		return user_folder_path
	end_with_slash = (target_path[-1] == '/')
	result_path = os.path.abspath(os.path.join(user_folder_path, target_path.strip('/\\')))
	if not result_path.startswith(user_folder_path):
		result_path = user_folder_path
	if end_with_slash:
		result_path = result_path + '/'
	return result_path


def run_rsync_exec(user_folder_path: str, _report_callable: Callable, channel: paramiko.Channel, cmdpart: List[str], binpath_rsync: str):
	cmdpart[-1] = rewrite_target_path(user_folder_path, cmdpart[-1])
	_log.debug('rsync command (rewritten): %r', cmdpart)
	with subprocess.Popen(cmdpart, bufsize=0, executable=binpath_rsync, stdin=subprocess.PIPE, stdout=subprocess.PIPE, close_fds=True) as pobj:
		_thread.start_new_thread(stream_exec_stdout, (channel, pobj))
		_thread.start_new_thread(stream_exec_stdin, (channel, pobj))
		# _thread.start_new_thread(stream_exec_stderr, (channel, pobj))  # , stderr=subprocess.PIPE
		_log.debug('stream ready')
		retcode = pobj.wait()
	_log.debug('command stopped: %r', retcode)
	# TODO: scan for changes
	channel.send_exit_status(retcode)
	channel.close()


class _SCPResponseCallable:
	__slots__ = ('channel', )

	def __init__(self, channel: paramiko.Channel) -> None:
		self.channel = channel

	def __call__(self, is_success: bool, message_text: str, *args: Any, **kwds: Any) -> None:
		if is_success:
			self.channel.sendall(b'\x00')
			return
		self.channel.sendall(b'\x01' + message_text.encode(encoding='utf-8') + b"\n")


def run_scp_sink(user_folder_path: str, report_callable: Callable, channel: paramiko.Channel, cmdpart: Iterable):
	_log.debug('scp command: %r', cmdpart)
	target_path = rewrite_target_path(user_folder_path, cmdpart[-1])
	streamed_bytes = 0
	loaded_bytes = 0
	resp_fn = _SCPResponseCallable(channel)
	scp_sink = SCPSinkHandler(target_path, report_callable=report_callable)
	try:
		resp_fn(True, '')
		while True:
			buf = channel.recv(2048)
			s = len(buf)
			if s == 0:
				scp_sink.close()
				_log.debug("scp streamed %d/%d bytes", streamed_bytes, loaded_bytes)
				channel.send_exit_status(0)
				break
			loaded_bytes = loaded_bytes + s
			scp_sink.feed(buf, resp_fn)
			streamed_bytes = streamed_bytes + s
	except Exception:
		_log.exception("cannot stream stdin data (streamed %d/%d bytes)", streamed_bytes, loaded_bytes)
		channel.send_exit_status(1)
	finally:
		scp_sink.close()
	channel.close()
