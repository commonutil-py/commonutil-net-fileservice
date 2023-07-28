# -*- coding: utf-8 -*-
"""
Adapter code for paramiko
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, List, Optional, Tuple
import _thread
import logging
import multiprocessing
import os
import queue
import shutil
import stat
import subprocess
import time

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


def rewrite_target_path(user_folder_path: str, target_path: str) -> Tuple[str, str]:
	if (not target_path) or (target_path == '.'):
		return (user_folder_path, user_folder_path)
	end_with_slash = (target_path[-1] == '/')
	abs_result_path = os.path.abspath(os.path.join(user_folder_path, target_path.strip('/\\')))
	if not abs_result_path.startswith(user_folder_path):
		abs_result_path = user_folder_path
	cmd_result_path = (abs_result_path + '/') if end_with_slash else abs_result_path
	return (abs_result_path, cmd_result_path)


class RsyncOptions:
	__slots__ = (
			'binpath',
			'shadow_folder_path',
			'fetch_state_callable',
			'save_state_callable',
			'invoke_timeout',
	)

	# pylint: disable=too-many-arguments
	def __init__(self,
					binpath: str,
					shadow_folder_path: Optional[str],
					fetch_state_callable: Optional[Callable[[str, str], str]] = None,
					save_state_callable: Optional[Callable[[str, str, str], None]] = None,
					invoke_timeout: float = 10.0) -> None:
		self.binpath = binpath
		self.shadow_folder_path = shadow_folder_path
		self.fetch_state_callable = fetch_state_callable
		self.save_state_callable = save_state_callable
		self.invoke_timeout = invoke_timeout

	def _fetch_state_invoke(self, q: multiprocessing.Queue, remote_username: str, folder_path: str) -> None:
		f_callable = self.fetch_state_callable
		try:
			result = f_callable(remote_username, folder_path)
			if not result:
				result = ''
		except Exception:
			_log.exception('run fetch_state_callable for %r [%r] failed', remote_username, folder_path)
			result = ''
		q.put(result, False)

	def run_fetch_state(self, remote_username: str, folder_path: str) -> str:
		if not self.fetch_state_callable:
			return ''
		q = multiprocessing.Queue(1)
		p = multiprocessing.Process(target=self._fetch_state_invoke, args=(q, remote_username, folder_path))
		p.start()
		p.join(self.invoke_timeout)
		try:
			result = q.get_nowait()
		except queue.Empty:
			_log.warning('invoke fetch_state_callable for %r [%r] timeout', remote_username, folder_path)
			result = ''
		except Exception:
			_log.exception('invoke fetch_state_callable for %r [%r] failed', remote_username, folder_path)
			result = ''
		p.terminate()
		return result

	def _save_state_invoke(self, remote_username: str, folder_path: str, state_text: str) -> None:
		f_callable = self.save_state_callable
		try:
			f_callable(remote_username, folder_path, state_text)
		except Exception:
			_log.exception('run save_state_callable for %r [%r] failed', remote_username, folder_path)

	def run_save_state(self, remote_username: str, folder_path: str, state_text: str) -> None:
		if not self.save_state_callable:
			return
		p = multiprocessing.Process(target=self._save_state_invoke, args=(remote_username, folder_path, state_text))
		p.start()
		p.join(self.invoke_timeout)
		p.terminate()
		return


def _extract_rsync_mtime(state_text) -> int:
	if (not state_text) or (len(state_text) < 3) or (state_text[:2] != '1,'):
		return 0
	try:
		return int(state_text[2:])
	except Exception:
		_log.warning("having error on convert state into rsync_mtime [%r]", state_text)
	return 0


def _wrap_rsync_mtime(v) -> str:
	return '1,' + str(v)


def _scan_latest_mtime(target_folder_path: str) -> int:
	if not os.path.isdir(target_folder_path):
		return 0  # we do not support this scenario
	start_at = time.time()
	scancount = 0
	latest_mtime = 0
	for root, _dirs, files in os.walk(target_folder_path):
		for filename in files:
			f_path = os.path.join(root, filename)
			try:
				st = os.lstat(f_path)
				if not stat.S_ISREG(st.st_mode):
					continue
				if st.st_mtime_ns > latest_mtime:
					latest_mtime = st.st_mtime_ns
				scancount = scancount + 1
			except Exception:
				_log.exception("_scan_latest_mtime: check m-time for [%r] failed", f_path)
	finish_at = time.time()
	_log.debug("_scan_latest_mtime: scanned %d entries, cost %r sec", scancount, finish_at - start_at)
	return latest_mtime


def _check_updated_mtime(target_folder_path: str, prev_latest_mtime: int) -> Tuple[int, Iterable[str]]:
	if not os.path.isdir(target_folder_path):
		return (0, [])  # we do not support this scenario
	start_at = time.time()
	scancount = 0
	latest_mtime = 0
	updated_paths = []
	for root, _dirs, files in os.walk(target_folder_path):
		for filename in files:
			f_path = os.path.join(root, filename)
			try:
				st = os.lstat(f_path)
				if not stat.S_ISREG(st.st_mode):
					continue
				f_mtime_ns = st.st_mtime_ns
				if f_mtime_ns > latest_mtime:
					latest_mtime = f_mtime_ns
				if f_mtime_ns > prev_latest_mtime:
					updated_paths.append(os.path.abspath(f_path))
				scancount = scancount + 1
			except Exception:
				_log.exception("_check_updated_mtime: check m-time for [%r] failed", f_path)
	finish_at = time.time()
	_log.debug("_check_updated_mtime: scanned %d entries, cost %r sec", scancount, finish_at - start_at)
	return (latest_mtime, updated_paths)


def _report_changed_paths(remote_username: str, user_folder_path: str, report_callable: Callable, changed_paths: Iterable[str], rsync_opts: RsyncOptions):
	if len(changed_paths) == 0:
		return
	shadow_folder_path = os.path.join(rsync_opts.shadow_folder_path, remote_username) if rsync_opts.shadow_folder_path else None
	if shadow_folder_path:
		os.makedirs(shadow_folder_path, 0o755, exist_ok=True)
	for abs_path in changed_paths:
		if not abs_path.startswith(user_folder_path):
			_log.warning("_report_changed_paths: changed file [%r] not in user folder [%r]", abs_path, user_folder_path)
			continue
		rel_path = os.path.relpath(abs_path, user_folder_path)
		if shadow_folder_path:
			subfolder_path = os.path.dirname(rel_path)
			if subfolder_path:
				os.makedirs(os.path.join(shadow_folder_path, subfolder_path), 0o755, exist_ok=True)
			shadow_abs_path = os.path.join(shadow_folder_path, rel_path)
			shutil.copyfile(abs_path, shadow_abs_path)
			abs_path = shadow_abs_path
		try:
			report_callable(abs_path, rel_path)
		except Exception:
			_log.exception("_report_changed_paths: invoke report callable with (%r, %r) failed", abs_path, rel_path)


# pylint: disable=too-many-arguments
def run_rsync_exec(remote_username: str, user_folder_path: str, report_callable: Callable, channel: paramiko.Channel, cmdpart: List[str],
					rsync_opts: RsyncOptions):
	abs_target_path, cmdpart[-1] = rewrite_target_path(user_folder_path, cmdpart[-1])
	rel_target_path = os.path.relpath(abs_target_path, user_folder_path)
	_log.debug('rsync command (rewritten): %r', cmdpart)
	prev_latest_mtime = _extract_rsync_mtime(rsync_opts.run_fetch_state(remote_username, rel_target_path))
	if prev_latest_mtime == 0:
		prev_latest_mtime = _scan_latest_mtime(abs_target_path)
	with subprocess.Popen(cmdpart, bufsize=0, executable=rsync_opts.binpath, stdin=subprocess.PIPE, stdout=subprocess.PIPE, close_fds=True) as pobj:
		_thread.start_new_thread(stream_exec_stdout, (channel, pobj))
		_thread.start_new_thread(stream_exec_stdin, (channel, pobj))
		# _thread.start_new_thread(stream_exec_stderr, (channel, pobj))  # , stderr=subprocess.PIPE
		_log.debug('stream ready')
		retcode = pobj.wait()
	_log.debug('command stopped: %r', retcode)
	try:
		synced_latest_mtime, changed_paths = _check_updated_mtime(abs_target_path, prev_latest_mtime)
		rsync_opts.run_save_state(remote_username, rel_target_path, _wrap_rsync_mtime(synced_latest_mtime))
		_report_changed_paths(remote_username, user_folder_path, report_callable, changed_paths, rsync_opts)
	except Exception:
		_log.exception("run_rsync_exec: caught failure on cheking update")
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
	_abs_target_path, target_path = rewrite_target_path(user_folder_path, cmdpart[-1])
	streamed_bytes = 0
	loaded_bytes = 0
	resp_fn = _SCPResponseCallable(channel)
	scp_sink = SCPSinkHandler(user_folder_path, target_path, report_callable=report_callable)
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
