# -*- coding: utf-8 -*-
"""
SCP protocol handler
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple
import logging
import os

_log = logging.getLogger(__name__)


class SCPSinkHandler:
	__slots__ = (
			'_user_folder_path',
			'_work_folder_path',
			'_mkdirmode',
			'_createfilemode',
			'_report_callable',
			'_parser',
			'_filesize',
			'_filename',
			'_dirstack',
			'_dirname',
			'_recv_fp',
			'_recv_done',
			'_last_absfilepath',
			'_last_relfilepath',
	)

	# pylint: disable=too-many-arguments
	def __init__(self,
					user_folder_path: str,
					work_folder_path: str,
					mkdirmode: int = 0o755,
					createfilemode: int = 0o644,
					report_callable: Optional[Callable[[str, str], None]] = None) -> None:
		self._user_folder_path = user_folder_path
		self._work_folder_path = os.path.abspath(work_folder_path)
		self._mkdirmode = mkdirmode
		self._createfilemode = createfilemode
		self._report_callable = report_callable
		self._parser = self._start
		self._filesize = 0
		self._filename = []
		self._dirstack = []
		self._dirname = []
		self._recv_fp = None
		self._recv_done = 0
		self._last_absfilepath = ''
		self._last_relfilepath = ''

	def feed(self, d: bytes, resp_fn: Callable[[bool, str], None]):
		while len(d) > 0:
			remain_d, next_parser = self._parser(d, resp_fn)
			if next_parser is not None:
				self._parser = next_parser
			d = remain_d

	def _close_recv_fp(self):
		if self._recv_fp is None:
			return
		try:
			self._recv_fp.close()
		except Exception:
			_log.exception("close receving file failed")
		self._recv_fp = None
		try:
			if self._report_callable:
				self._report_callable(self._last_absfilepath, self._last_relfilepath)
		except Exception:
			_log.exception("invoke report callable with (%r, %r) failed", self._last_absfilepath, self._last_relfilepath)

	def close(self):
		self._close_recv_fp()

	def _prepare_dirstack_folder(self):
		p = os.path.abspath(os.path.join(self._work_folder_path, *self._dirstack))
		if not p.startswith(self._user_folder_path):
			_log.warning("escaped dir stack (%r): %r", self._user_folder_path, p)
			return
		os.makedirs(p, self._mkdirmode, exist_ok=True)

	def _open_file_path(self) -> Optional[str]:
		n = bytes(self._filename).decode(encoding='utf-8').strip('/\\')
		if not n:
			return None
		pathcomps = self._dirstack + [
				n,
		]
		p = os.path.abspath(os.path.join(self._work_folder_path, *pathcomps))
		if not p.startswith(self._user_folder_path):
			_log.warning("escaped file name (%r): %r", self._user_folder_path, p)
			return None
		fp = open(p, 'wb', opener=self._fp_opener)  # pylint: disable=consider-using-with
		self._last_absfilepath = p
		self._last_relfilepath = p[len(self._user_folder_path) + 1:]
		return fp

	def _fp_opener(self, path, flags):
		return os.open(path, flags, self._createfilemode)

	def _start(self, d: bytes, resp_fn: Callable) -> Tuple[bytes, Callable]:
		cmdch = d[0]
		if cmdch == 0x43:  # b'C':
			self._filesize = 0
			self._filename = []
			self._recv_fp = None
			self._recv_done = 0
			return (d[1:], self._c_mode0)
		if cmdch == 0x54:  # b'T':
			return (d[1:], self._t_drop)
		if cmdch == 0x44:  # b'D':
			self._dirname = []
			return (d[1:], self._d_mode0)
		if cmdch == 0x45:  # b'E':
			return (d[1:], self._e_popdir)
		resp_fn(False, f"unknown command character {cmdch!r}")
		_log.error('unknown command character: %r', cmdch)
		raise Exception('unknown command character', cmdch)

	def _c_mode0(self, d: bytes, _resp_fn: Callable[[bool, str], None]) -> Tuple[bytes, Callable]:
		for idx, ch in enumerate(d):
			if ch == 0x20:  # b' ':
				return (d[idx:], self._c_mode1)
		return (b'', None)

	def _c_mode1(self, d: bytes, _resp_fn: Callable[[bool, str], None]) -> Tuple[bytes, Callable]:
		for idx, ch in enumerate(d):
			if ch != 0x20:  # b' ':
				return (d[idx:], self._c_size0)
		return (b'', None)

	def _c_size0(self, d: bytes, _resp_fn: Callable[[bool, str], None]) -> Tuple[bytes, Callable]:
		for idx, ch in enumerate(d):
			if ch in b'0123456789':
				self._filesize = (self._filesize * 10) + (ch - 0x30)  # 0x30 = b'0'
			elif ch == 0x20:  # b' ':
				return (d[idx:], self._c_size1)
		return (b'', None)

	def _c_size1(self, d: bytes, _resp_fn: Callable[[bool, str], None]) -> Tuple[bytes, Callable]:
		for idx, ch in enumerate(d):
			if ch != 0x20:  # ' ':
				return (d[idx:], self._c_filename)
		return (b'', None)

	def _c_filename(self, d: bytes, resp_fn: Callable[[bool, str], None]) -> Tuple[bytes, Callable]:
		for idx, ch in enumerate(d):
			if ch == 0x0A:  # b"\n":
				resp_fn(True, '')
				self._recv_fp = self._open_file_path()
				return (d[idx + 1:], self._c_file_recv0)
			self._filename.append(ch)
		return (b'', None)

	def _c_file_recv0(self, d: bytes, _resp_fn: Callable[[bool, str], None]) -> Tuple[bytes, Callable]:
		buf_size = len(d)
		remain_size = self._filesize - self._recv_done
		if remain_size == 0:
			f_content = b''
		elif buf_size > remain_size:
			f_content = d[:remain_size]
			d = d[remain_size:]
		else:
			f_content = d
			d = b''
		if self._recv_fp:
			self._recv_fp.write(f_content)
		self._recv_done = self._recv_done + len(f_content)
		if self._filesize == self._recv_done:
			self._close_recv_fp()
			self._recv_fp = None
			return (d, self._c_file_recv1)
		return (d, None)

	def _c_file_recv1(self, d: bytes, resp_fn: Callable[[bool, str], None]) -> Tuple[bytes, Callable]:
		ch = d[0]
		if ch == 0x00:  # b'\x00':
			resp_fn(True, '')
		else:
			resp_fn(False, f"unexpected stop character {ch!r}")
			raise Exception('unexpected stop character', ch)
		return (d[1:], self._start)

	def _t_drop(self, d: bytes, resp_fn: Callable[[bool, str], None]) -> Tuple[bytes, Callable]:
		for idx, ch in enumerate(d):
			if ch == 0x0A:  # b"\n":
				resp_fn(True, '')
				# self._recv_fp = None # TODO: save timestamp for later use
				return (d[idx + 1:], self._start)
		return (b'', None)

	def _d_mode0(self, d: bytes, _resp_fn: Callable[[bool, str], None]) -> Tuple[bytes, Callable]:
		for idx, ch in enumerate(d):
			if ch == 0x20:  # b' ':
				return (d[idx:], self._d_mode1)
		return (b'', None)

	def _d_mode1(self, d: bytes, _resp_fn: Callable[[bool, str], None]) -> Tuple[bytes, Callable]:
		for idx, ch in enumerate(d):
			if ch != 0x20:  # b' ':
				return (d[idx:], self._d_size0)
		return (b'', None)

	def _d_size0(self, d: bytes, _resp_fn: Callable[[bool, str], None]) -> Tuple[bytes, Callable]:
		for idx, ch in enumerate(d):
			if ch == 0x20:  # b' ':
				return (d[idx:], self._d_size1)
		return (b'', None)

	def _d_size1(self, d: bytes, _resp_fn: Callable[[bool, str], None]) -> Tuple[bytes, Callable]:
		for idx, ch in enumerate(d):
			if ch != 0x20:  # ' ':
				return (d[idx:], self._d_dirname)
		return (b'', None)

	def _d_dirname(self, d: bytes, resp_fn: Callable[[bool, str], None]) -> Tuple[bytes, Callable]:
		for idx, ch in enumerate(d):
			if ch == 0x0A:  # b"\n":
				n = bytes(self._dirname).decode(encoding='utf-8').strip('/\\')
				self._dirstack.append(n)
				self._prepare_dirstack_folder()
				resp_fn(True, '')
				return (d[idx + 1:], self._start)
			self._dirname.append(ch)
		return (b'', None)

	def _e_popdir(self, d: bytes, resp_fn: Callable[[bool, str], None]) -> Tuple[bytes, Callable]:
		for idx, ch in enumerate(d):
			if ch == 0x0A:  # b"\n":
				if len(self._dirstack) > 0:
					self._dirstack.pop()
					# TODO: setup other folder variables
				resp_fn(True, '')
				return (d[idx + 1:], self._start)
		return (b'', None)
