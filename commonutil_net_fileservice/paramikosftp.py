# -*- coding: utf-8 -*-
"""
Adapter code for paramiko
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional, Tuple
import _thread
import io
import logging
import os
import shutil
import socketserver
import time

import paramiko

from commonutil_net_fileservice.config import (DEFAULT_REV_CONTENT, DEFAULT_REV_FILENAME, User, make_users_map)
from commonutil_net_fileservice.paramikosubexec import (run_rsync_exec, RsyncOptions, run_scp_sink)

_log = logging.getLogger(__name__)


def open_mode_from_flags(flags) -> str:
	if flags & os.O_EXCL:
		return 'xb'
	if flags & os.O_WRONLY:
		return 'ab' if (flags & os.O_APPEND) else 'wb'
	if flags & os.O_RDWR:
		return 'a+b' if (flags & os.O_APPEND) else 'r+b'
	return 'rb'


def _fp_opener(path, flags):
	return os.open(path, flags, 0o644)


def _remote_location_from_transport(transport: paramiko.Transport) -> str:
	try:
		remote_addr, remote_port = transport.getpeername()
		return f"{remote_addr}:{remote_port}"
	except Exception as e:
		return f"exception:{e!r}"


def make_sftp_attr(file_name: str, file_content: bytes) -> paramiko.SFTPAttributes:
	fattr = paramiko.SFTPAttributes()
	fattr.st_size = len(file_content)
	fattr.st_uid = 0
	fattr.st_gid = 0
	fattr.st_mode = 0o100444
	fattr.st_mtime = int(time.time())
	fattr.filename = file_name
	return fattr


class SFTPHandle(paramiko.SFTPHandle):
	# pylint: disable=too-many-arguments
	def __init__(self, fp, flags, local_path, rel_path, report_callable, stat_object=None):
		super().__init__(flags)
		self.readfile = fp
		self.writefile = fp
		self.local_path = local_path
		self.rel_path = os.path.normpath(rel_path.strip('/\\'))
		self.report_callable = report_callable
		self.stat_object = stat_object

	def close(self):
		super().close()
		if self.report_callable:
			self.report_callable(self.local_path, self.rel_path)

	def chattr(self, attr):
		_log.debug("chattr: %r", attr)
		return paramiko.SFTP_PERMISSION_DENIED

	def stat(self):
		if self.stat_object:
			return self.stat_object
		stat_obj = os.stat(self.local_path)
		basename = os.path.basename(self.local_path)
		return paramiko.SFTPAttributes.from_stat(stat_obj, basename)


class SFTPServerImpl(paramiko.SFTPServerInterface):
	__slots__ = (
			'u',
			'user_folder_path',
			'v_rev_filepath',
			'v_rev_content',
			'v_rev_stat',
			'_remote_location',
			'_process_callable',
	)

	def __init__(self, server: ServerImpl, transport: paramiko.Transport, *args, **kwargs):
		super().__init__(server, *args, **kwargs)
		self.u, _remote_username = server.lookup_user_via_transport(transport)
		self.user_folder_path = os.path.abspath(os.path.join(server.base_folder_path, self.u.username)) if self.u else '/dev/null'
		self.v_rev_filepath = '/' + server.v_rev_filename
		self.v_rev_content = server.v_rev_content
		self.v_rev_stat = server.v_rev_stat
		self._remote_location = _remote_location_from_transport(transport)
		self._process_callable = server.process_callable

	def _local_path(self, remote_path: str) -> str:
		p = os.path.abspath(os.path.join(self.user_folder_path, remote_path.strip('/\\')))
		if p.startswith(self.user_folder_path):
			return p
		_log.warning('escaped local path (user: %r): %r', self.user_folder_path, p)
		return self.user_folder_path

	def _do_report(self, local_path, rel_path):
		try:
			self._process_callable(self.u.username, self._remote_location, local_path, rel_path)
		except Exception:
			_log.exception("caught exception on invoking received file processor: user=%r, remote=%r, local_path=%r, rel_path=%r", self.u.username,
							self._remote_location, local_path, rel_path)

	def chattr(self, path, attr):
		_log.debug("chattr: %r, %r", path, attr)
		return paramiko.SFTP_PERMISSION_DENIED

	def list_folder(self, path):
		if not self.u:
			return paramiko.SFTP_PERMISSION_DENIED
		_log.debug("list_folder: %r", path)
		local_path = self._local_path(path)
		result = []
		aux = os.listdir(local_path)
		for n in aux:
			f_path = os.path.join(local_path, n)
			stat_obj = os.lstat(f_path)
			sftpattr = paramiko.SFTPAttributes.from_stat(stat_obj, n)
			result.append(sftpattr)
		if path == '/':
			result.append(self.v_rev_stat)
		return result

	def lstat(self, path):
		if not self.u:
			return paramiko.SFTP_PERMISSION_DENIED
		_log.debug("lstat: %r", path)
		if path == self.v_rev_filepath:
			return self.v_rev_stat
		local_path = self._local_path(path)
		try:
			stat_obj = os.lstat(local_path)
		except FileNotFoundError:
			return paramiko.SFTP_NO_SUCH_FILE
		return paramiko.SFTPAttributes.from_stat(stat_obj)

	def mkdir(self, path, attr):
		if not self.u:
			return paramiko.SFTP_PERMISSION_DENIED
		_log.debug("mkdir: %r, %r", path, attr)
		if path in ('/', self.v_rev_filepath):
			return paramiko.SFTP_PERMISSION_DENIED
		local_path = self._local_path(path)
		if local_path == self.user_folder_path:
			return paramiko.SFTP_PERMISSION_DENIED
		os.makedirs(local_path, 0o755, exist_ok=True)
		return paramiko.SFTP_OK

	def open(self, path, flags, attr):
		if not self.u:
			return paramiko.SFTP_PERMISSION_DENIED
		_log.debug("open: %r, %r, %r (%r)", path, flags, attr, self.user_folder_path)
		if path == self.v_rev_filepath:
			return SFTPHandle(io.BytesIO(self.v_rev_content), flags, path, path, None, self.v_rev_stat)
		local_path = self._local_path(path)
		fp = open(local_path, open_mode_from_flags(flags), opener=_fp_opener)  # pylint: disable=consider-using-with
		report_callable = self._do_report if ((flags & os.O_WRONLY) | (flags & os.O_RDWR)) else None
		return SFTPHandle(fp, flags, local_path, path, report_callable)

	def posix_rename(self, oldpath, newpath):
		if not self.u:
			return paramiko.SFTP_PERMISSION_DENIED
		_log.debug("posix_rename: %r, %r", oldpath, newpath)
		local_oldpath = self._local_path(oldpath)
		local_newpath = self._local_path(newpath)
		if self.user_folder_path in (local_oldpath, local_newpath):
			return paramiko.SFTP_PERMISSION_DENIED
		os.replace(local_oldpath, local_newpath)
		return paramiko.SFTP_OK

	def readlink(self, path):
		if not self.u:
			return paramiko.SFTP_PERMISSION_DENIED
		_log.debug("readlink: %r", path)
		local_path = self._local_path(path)
		realpath = os.path.realpath(local_path)
		if not realpath.startswith(self.user_folder_path):
			return paramiko.SFTP_NO_SUCH_FILE
		relatedpath = realpath[len(self.user_folder_path):]
		return relatedpath

	def remove(self, path):
		if not self.u:
			return paramiko.SFTP_PERMISSION_DENIED
		_log.debug("remove: %r", path)
		local_path = self._local_path(path)
		if local_path == self.user_folder_path:
			return paramiko.SFTP_PERMISSION_DENIED
		os.remove(local_path)
		return paramiko.SFTP_OK

	def rename(self, oldpath, newpath):
		if not self.u:
			return paramiko.SFTP_PERMISSION_DENIED
		_log.debug("rename: %r, %r", oldpath, newpath)
		local_oldpath = self._local_path(oldpath)
		local_newpath = self._local_path(newpath)
		if self.user_folder_path in (local_oldpath, local_newpath):
			return paramiko.SFTP_PERMISSION_DENIED
		shutil.move(local_oldpath, local_newpath)
		return paramiko.SFTP_OK

	def rmdir(self, path):
		if not self.u:
			return paramiko.SFTP_PERMISSION_DENIED
		_log.debug("rmdir: %r", path)
		local_path = self._local_path(path)
		if local_path == self.user_folder_path:
			return paramiko.SFTP_PERMISSION_DENIED
		os.rmdir(local_path)
		return paramiko.SFTP_OK

	def stat(self, path):
		if not self.u:
			return paramiko.SFTP_PERMISSION_DENIED
		_log.debug("stat: %r", path)
		if path == self.v_rev_filepath:
			return self.v_rev_stat
		local_path = self._local_path(path)
		stat_obj = os.stat(local_path)
		basename = os.path.basename(path)
		if not basename:
			basename = None
		return paramiko.SFTPAttributes.from_stat(stat_obj, basename)

	def symlink(self, target_path, path):
		if not self.u:
			return paramiko.SFTP_PERMISSION_DENIED
		_log.debug("symlink: %r, %r", target_path, path)
		local_target_path = self._local_path(target_path)
		local_sym_path = self._local_path(path)
		if self.user_folder_path in (local_target_path, local_sym_path):
			return paramiko.SFTP_PERMISSION_DENIED
		os.symlink(local_target_path, local_sym_path)
		return paramiko.SFTP_OK


class _ReportCallableWrap:
	__slots__ = (
			'u',
			'remote_location',
			'process_callable',
	)

	def __init__(self, channel: paramiko.Channel, u: User, process_callable: Callable[[str, str, str, str], None]) -> None:
		self.u = u
		self.remote_location = _remote_location_from_transport(channel.transport)
		self.process_callable = process_callable

	def __call__(self, local_path: str, rel_path: str) -> None:
		try:
			self.process_callable(self.u.username, self.remote_location, local_path, rel_path)
		except Exception:
			_log.exception("invoke process callable (%r, %r, %r, %r)", self.u.username, self.remote_location, local_path, rel_path)


class ServerImpl(paramiko.ServerInterface):
	__slots__ = (
			'base_folder_path',
			'users',
			'process_callable',
			'v_rev_filename',
			'v_rev_content',
			'v_rev_stat',
			'rsync_opts',
			'_users_lck',
	)

	# pylint: disable=too-many-arguments
	def __init__(self,
					base_folder_path: str,
					users: Iterable[User],
					process_callable: Callable[[str, str, str, str], None],
					v_rev_filename: str = DEFAULT_REV_FILENAME,
					v_rev_content: str = DEFAULT_REV_CONTENT,
					rsync_opts: Optional[RsyncOptions] = None) -> None:
		super().__init__()
		self.base_folder_path = base_folder_path
		self.users = make_users_map(users)
		self.process_callable = process_callable
		self.v_rev_filename = v_rev_filename
		self.v_rev_content = (v_rev_content.strip() + "\n").encode('utf-8', 'ignore')
		self.v_rev_stat = make_sftp_attr(v_rev_filename, self.v_rev_content)
		self.rsync_opts = rsync_opts
		self._users_lck = _thread.allocate_lock()

	def update_users(self, users: Iterable[User]):
		rec = make_users_map(users)
		with self._users_lck:
			to_drop = []
			for k in self.users:
				if k not in rec:
					to_drop.append(k)
			for k in to_drop:
				self.users.pop(k, None)
			for k, u in rec.items():
				self.users[k] = u

	def lookup_user_via_name(self, username: str) -> Optional[User]:
		if not username:
			return None
		with self._users_lck:
			return self.users.get(username)

	def lookup_user_via_transport(self, transport: paramiko.Transport) -> Tuple[Optional[User], str]:
		try:
			remote_username = transport.auth_handler.get_username()
		except Exception:
			_log.exception("cannot reach username for user lookup")
			return (None, '?')
		return (self.lookup_user_via_name(remote_username), remote_username)

	def _run_command(self, channel: paramiko.Channel, command: bytes) -> bool:
		u, remote_username = self.lookup_user_via_transport(channel.transport)
		if not u:
			_log.warning("cannot reach user [%r] for command: %r", remote_username, command)
			return False
		cmdpart = list(filter(None, command.decode(encoding='utf-8').split(' ')))
		if len(cmdpart) < 2:
			raise Exception('low argument count', cmdpart)
		cmdtarget = cmdpart[0]
		_log.info("run: [%s] as [%r]", cmdtarget, remote_username)
		user_folder_path = os.path.abspath(os.path.join(self.base_folder_path, u.username))
		report_callable = _ReportCallableWrap(channel, u, self.process_callable)
		if cmdtarget in ('rsync', '/bin/rsync', '/usr/bin/rsync'):
			if self.rsync_opts:
				_thread.start_new_thread(run_rsync_exec, (remote_username, user_folder_path, report_callable, channel, cmdpart, self.rsync_opts))
			else:
				_log.warning("requested rsync but rsync executable path is not configurated")
				return False
		elif cmdtarget in ('scp', '/bin/scp', '/usr/bin/scp'):
			_thread.start_new_thread(run_scp_sink, (user_folder_path, report_callable, channel, cmdpart))
		else:
			raise Exception('unknown command', cmdtarget, command)
		return True

	def check_auth_password(self, username, password):
		# _log.debug("check_auth_password: %r, %r", username, password)
		u = self.lookup_user_via_name(username)
		if not u:
			return paramiko.AUTH_FAILED
		if not u.check_credential(password):
			return paramiko.AUTH_FAILED
		return paramiko.AUTH_SUCCESSFUL

	def check_auth_publickey(self, username, key):
		# _log.debug("check_auth_publickey: %r, %r", username, key)
		u = self.lookup_user_via_name(username)
		if not u:
			return paramiko.AUTH_FAILED
		key_type = key.get_name()
		key_b64 = key.get_base64()
		matched_key = u.check_ssh_pkey(key_type, key_b64)
		if not matched_key:
			return paramiko.AUTH_FAILED
		return paramiko.AUTH_SUCCESSFUL

	def check_channel_request(self, kind, chanid):
		_log.debug("check_channel_request: %r, %r", kind, chanid)
		return paramiko.OPEN_SUCCEEDED

	def get_allowed_auths(self, username):
		_log.debug("get_allowed_auths: %r", username)
		return "password,publickey"

	def check_channel_exec_request(self, channel, command):
		_log.debug("check_channel_exec_request: %r", command)
		# ref: https://github.com/carletes/mock-ssh-server/blob/master/mockssh/server.py
		if not channel.transport:
			return False
		try:
			return self._run_command(channel, command)
		except Exception:
			_log.exception('cannot invoke command')
		return False


class SSHLinkHandler(socketserver.BaseRequestHandler):
	def handle(self):
		_log.info("connected: %r", self.client_address)
		transport = paramiko.Transport(self.request)
		transport.add_server_key(self.server.host_pkey)
		transport.set_subsystem_handler('sftp', paramiko.SFTPServer, SFTPServerImpl, transport)
		transport.start_server(server=self.server.server_impl)
		ch = transport.accept()
		_log.debug("channel open: %r", ch)
		while transport.is_active():
			time.sleep(1)
		_log.info("disconnecting: %r", self.client_address)


class SFTPServer(socketserver.ForkingTCPServer):
	__slots__ = (
			'host_pkey',
			'server_impl',
	)

	# pylint: disable=too-many-arguments
	def __init__(self,
					server_host: str,
					server_port: int,
					key_file_path: str,
					key_bits: int,
					base_folder_path: str,
					users: Iterable[User],
					process_callable: Callable[[str, str, str, str], None],
					v_rev_filename: str = DEFAULT_REV_FILENAME,
					v_rev_content: str = DEFAULT_REV_CONTENT,
					rsync_opts: Optional[RsyncOptions] = None) -> None:
		super().__init__((server_host, server_port), SSHLinkHandler)
		try:
			self.host_pkey = paramiko.RSAKey.from_private_key_file(key_file_path)
		except FileNotFoundError:
			self.host_pkey = paramiko.RSAKey.generate(key_bits)
			self.host_pkey.write_private_key_file(key_file_path)
		self.server_impl = ServerImpl(base_folder_path, users, process_callable, v_rev_filename, v_rev_content, rsync_opts)

	def update_users(self, users: Iterable[User]):
		self.server_impl.update_users(users)
