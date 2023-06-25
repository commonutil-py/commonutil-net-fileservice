# -*- coding: utf-8 -*-
"""
Adapter code for pyftpdlib
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional, Tuple
import logging
import os
import stat
import time
from collections import namedtuple
from io import BytesIO

from pyftpdlib.authorizers import AuthenticationFailed
from pyftpdlib.filesystems import AbstractedFS
from pyftpdlib.handlers import FTPHandler as _FTPHandler

from commonutil_net_fileservice.config import (DEFAULT_REV_CONTENT, DEFAULT_REV_FILENAME, User, make_users_map)

_log = logging.getLogger(__name__)

_ALL_PERM = "elradfmwM"
"""
Read permissions:
- "e" = change directory (CWD command)
- "l" = list files (LIST, NLST, STAT, MLSD, MLST, SIZE, MDTM commands)
- "r" = retrieve file from the server (RETR command)
Write permissions:
- "a" = append data to an existing file (APPE command)
- "d" = delete file or directory (DELE, RMD commands)
- "f" = rename file or directory (RNFR, RNTO commands)
- "m" = create directory (MKD command)
- "w" = store a file to the server (STOR, STOU commands)
- "M" = change file mode (SITE CHMOD command)
- "T" = update file last modified time (MFMT command)
"""

_StatResult = namedtuple("_StatResult", (
		"st_mode",
		"st_ino",
		"st_dev",
		"st_nlink",
		"st_uid",
		"st_gid",
		"st_size",
		"st_atime",
		"st_mtime",
		"st_ctime",
))
_BOOTUP_TSTAMP = time.time()


class Authorizer:
	__slots__ = (
			'users',
			'base_folder_path',
	)

	def __init__(self, users: Iterable[User], base_folder_path: str, *args, **kwds):
		super().__init__(*args, **kwds)
		self.users = make_users_map(users)
		self.base_folder_path = os.path.abspath(base_folder_path)

	def update_users(self, users: Iterable[User]):
		rec = make_users_map(users)
		to_drop = []
		for k in self.users:
			if k not in rec:
				to_drop.append(k)
		for k in to_drop:
			self.users.pop(k, None)
		for k, u in rec.items():
			self.users[k] = u

	def validate_authentication(self, username, password, handler):  # pylint: disable=unused-argument
		"""
		Raises AuthenticationFailed if supplied username and password don't
		match the stored credentials, else return None.
		"""
		u = self.users.get(username)
		if not u:
			raise AuthenticationFailed(f"user not found: {username!r}")
		if not u.check_credential(password):
			raise AuthenticationFailed(f"authorize failed: {username!r}")

	def get_home_dir(self, username):
		"""
		Return the user's home directory.
		Since this is called during authentication (PASS), AuthenticationFailed
		can be freely raised by subclasses in case the provided username no
		longer exists.
		"""
		u = self.users[username]
		wd = os.path.abspath(os.path.join(self.base_folder_path, u.username))
		if not wd.startswith(self.base_folder_path):
			raise AuthenticationFailed(f"home folder escaped: {username!r}: {wd!r}")
		return wd

	def impersonate_user(self, username, password):
		"""
		Impersonate another user (noop).
		It is always called before accessing the filesystem.
		By default it does nothing.  The subclass overriding this method is
		expected to provide a mechanism to change the current user.
		"""

	def terminate_impersonation(self, username):
		"""
		Terminate impersonation (noop).
		It is always called after having accessed the filesystem. By default it
		does nothing.  The subclass overriding this method is expected to
		provide a mechanism to switch back to the original user.
		"""

	def has_user(self, username):
		"""
		Whether the username exists in the virtual users table.
		"""
		return username in self.users

	def has_perm(self, username, perm, path=None):  # pylint: disable=unused-argument
		"""
		Whether the user has permission over path (an absolute pathname of a
		file or a directory).
		Expected perm argument is one of the following letters: "elradfmwMT".
		"""
		return perm in _ALL_PERM

	def get_perms(self, username):  # pylint: disable=unused-argument
		"""
		Return current user permissions.
		"""
		return _ALL_PERM

	def get_msg_login(self, username):
		"""
		Return the user's login message.
		"""
		return f"Login successful ({username})"

	def get_msg_quit(self, username):
		"""
		Return the user's quitting message.
		"""
		return f"Bye ({username})"


# FileSystem = AbstractedFS


class NamedBytesIO(BytesIO):
	def __init__(self, name, *args, **kwds):
		super().__init__(*args, **kwds)
		self.name = name


class FileSystem(AbstractedFS):
	_v_rev_filename = DEFAULT_REV_FILENAME
	_v_rev_content = DEFAULT_REV_CONTENT.encode('utf-8', 'ignore')
	_v_rev_stat = _StatResult(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH, 0, 0, 1, 0, 0, len(_v_rev_content), _BOOTUP_TSTAMP, _BOOTUP_TSTAMP, _BOOTUP_TSTAMP)

	def __init__(self, root, cmd_channel, *args, **kwds):
		super().__init__(root, cmd_channel, *args, **kwds)
		self._rev_path = os.path.normpath(os.path.join(self.root, self._v_rev_filename))
		self._begin_relpath = self._index_relpath_begin()

	@classmethod
	def set_v_rev_file(cls, filename: str, content: str) -> None:
		content = content.strip() + "\n"
		cls._v_rev_filename = filename
		cls._v_rev_content = content.encode('utf-8', 'ignore')
		cls._v_rev_stat = _StatResult(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH, 0, 0, 1, 0, 0, len(cls._v_rev_content), _BOOTUP_TSTAMP, _BOOTUP_TSTAMP,
										_BOOTUP_TSTAMP)

	def _index_relpath_begin(self):
		p = self.root
		if p[-1] == "/":
			return len(p)
		return (len(p) + 1)

	def fs2relpath(self, filepath):
		try:
			return filepath[self._begin_relpath:]
		except Exception:
			pass
		return None

	def open(self, filename, mode):
		if filename == self._rev_path:
			return NamedBytesIO(self._v_rev_filename, self._v_rev_content)
		return super().open(filename, mode)

	def listdir(self, path):
		"""
		List the content of a directory.
		"""
		_log.debug("listdir: %r", path)
		result = super().listdir(path)
		if path == self._root:
			return result + [
					self._v_rev_filename,
			]
		return result

	def listdirinfo(self, path):
		"""
		List the content of a directory.
		"""
		_log.debug("listdirinfo: %r", path)
		return self.listdir(path)

	def stat(self, path):
		"""
		Perform a stat() system call on the given path.
		"""
		_log.debug("stat: %r", path)
		if self._rev_path == path:
			return self._v_rev_stat
		return super().stat(path)

	def lstat(self, path):
		"""
		Perform a lstat() system call on the given path.
		"""
		_log.debug("lstat: %r", path)
		if self._rev_path == path:
			return self._v_rev_stat
		return super().lstat(path)

	def isfile(self, path):
		"""
		Return True if path is a file.
		"""
		_log.debug("isfile: %r", path)
		if self._rev_path == path:
			return True
		return super().isfile(path)

	def getsize(self, path):
		"""
		Return the size of the specified file in bytes.
		"""
		_log.debug("getsize: %r", path)
		if self._rev_path == path:
			return len(self._v_rev_content)
		return super().getsize(path)

	def getmtime(self, path):
		"""
		Return the last modified time as a number of seconds since the epoch.
		"""
		_log.debug("getmtime: %r", path)
		if self._rev_path == path:
			return _BOOTUP_TSTAMP
		return super().getmtime(path)


class FTPHandler(_FTPHandler):
	process_callable = None
	default_transfer_type = 'i'

	def on_connect(self):
		_log.info("connect: (%r:%r), (%r connections/ %r total connections)", self.remote_ip, self.remote_port, self.server.ip_map.count(self.remote_ip),
					len(self.server.ip_map))

	def on_login(self, username):
		"""
		Called on user login.
		"""
		self._current_type = self.default_transfer_type
		_log.info("user login: %r (%r:%r)", username, self.remote_ip, self.remote_port)

	def on_login_failed(self, username, password):
		"""
		Called on failed login attempt.
		At this point client might have already been disconnected if it failed
		too many times.
		"""
		_log.info("login rejected: %r (%r:%r)", username, self.remote_ip, self.remote_port)

	def on_logout(self, username):
		"""
		Called when user "cleanly" logs out due to QUIT or USER issued twice (re-login).
		This is not called if the connection is simply closed by client.
		"""
		_log.info("user logout: %r (%r:%r)", username, self.remote_ip, self.remote_port)

	def on_disconnect(self):
		"""
		Called when connection is closed.
		"""
		self.file_rule_set = None
		_log.info("disconnect: %r (%r:%r), (%r connections/ %r total connections)", self.username, self.remote_ip, self.remote_port,
					self.server.ip_map.count(self.remote_ip), len(self.server.ip_map))

	def on_file_sent(self, file):
		"""
		Called every time a file has been succesfully sent.
		`file` is the absolute name of the file just being sent.
		"""
		_log.info("sent: (by %r) %r", self.username, file)

	def on_file_received(self, file):
		"""
		Called every time a file has been succesfully received.
		`file` is the absolute name of the file just being received.
		"""
		_log.info("recv: (by %r) %r", self.username, file)
		if not self.fs:
			return
		relfilepath = self.fs.fs2relpath(file)
		_log.debug("got %r from remote", relfilepath)
		if not relfilepath:
			return
		if not FTPHandler.process_callable:
			return
		try:
			remote_location = f"{self.remote_ip}:{self.remote_port}"
		except Exception as e:
			remote_location = f"UNKNOWN: {e!r}"
			_log.exception("cannot have remote location (user=%r)", self.username)
		try:
			FTPHandler.process_callable(self.username, remote_location, file, relfilepath)  # pylint: disable=not-callable
		except Exception:
			_log.exception("caught exception on invoking received file processor: user=%r, remote=%r, filename=%r, relfilepath=%r", self.username,
							remote_location, file, relfilepath)

	def ftp_USER(self, line):
		"""
		Set the username for the current session.
		"""
		try:
			line = line.lower()
		except Exception as e:
			self.respond(f"530 Invalid username: {line!r} ({e!r}).")
			return
		_FTPHandler.ftp_USER(self, line)

	def ftp_RMD(self, path):
		"""
		Remove the specified directory.
		On success return the directory path, else None.
		"""
		_log.info("rmdir: (by %r) %r", self.username, path)
		return _FTPHandler.ftp_RMD(self, path)

	def ftp_DELE(self, path):
		"""
		Delete the specified file.
		On success return the file path, else None.
		"""
		_log.info("dele: (by %r) %r", self.username, path)
		return _FTPHandler.ftp_DELE(self, path)

	def ftp_RNFR(self, path):
		"""
		Rename the specified (only the source name is specified here, see RNTO command)
		"""
		_log.info("rnfr: (by %r) %r", self.username, path)
		return _FTPHandler.ftp_RNFR(self, path)

	def ftp_RNTO(self, path):
		"""
		Rename file (destination name only, source is specified with RNFR).
		On success return a (source_path, destination_path) tuple.
		"""
		_log.info("rnto-request: (by %r) %r", self.username, path)
		ret = _FTPHandler.ftp_RNTO(self, path)
		if ret is not None:
			_log.info("rnto: (by %r) %r", self.username, ret)
		return ret


# pylint: disable=too-many-arguments
def setup_handlers(banner_text: str,
					base_folder_path: str,
					users: Iterable[User],
					process_callable: Optional[Callable],
					ftp_passive_port_range: Optional[Iterable[int]],
					ftp_public_address: Optional[str],
					v_rev_filename: str = DEFAULT_REV_FILENAME,
					v_rev_content: str = DEFAULT_REV_CONTENT) -> Tuple[FTPHandler, Authorizer]:
	"""
	Prepare handlers for FTP server
	"""
	ftp_hnd = FTPHandler
	auth_hnd = Authorizer(users, base_folder_path)
	ftp_hnd.authorizer = auth_hnd
	fsabs = FileSystem  # implementation default
	fsabs.set_v_rev_file(v_rev_filename, v_rev_content)
	ftp_hnd.abstracted_fs = fsabs
	ftp_hnd.banner = banner_text
	ftp_hnd.passive_ports = ftp_passive_port_range
	if ftp_public_address:
		ftp_hnd.masquerade_address = ftp_public_address
	ftp_hnd.permit_foreign_addresses = True
	ftp_hnd.process_callable = process_callable
	return (ftp_hnd, auth_hnd)
