# -*- coding: utf-8 -*-
"""
Example code for setting up FTP service with pyftpdlib
"""

import sys
import os
import getopt
import logging

from commonutil_net_fileservice.paramikosftp import SFTPServer, RsyncOptions

from examples.common import make_example_users, process_callable

_log = logging.getLogger(__name__)

_HELP_TEXT = """
Options:
	-h | --help
		Print help message
	--port=[FTP_CONTROL_PORT]
		Port of SFTP service. (default: 2222)
	--base-folder=[BASE_FOLDER_PATH]
		Path of base folder. (default: /tmp/common-net-paramikosftpd-example)
	--rsync-bin=[RSYNC_BINPATH]
		Path of rsync binary. (default: /usr/bin/rsync)
	--shadow-folder=[SHADOW_FOLDER]
		Path of rsync shadow folder.
	--state-folder=[STATE_FOLDER]
		Path of rsync state folder. (default: /tmp/common-net-paramikosftpd-exrsyncstate)
""".replace("\t", "    ")


def parse_options(argv):
	sftp_port = 2222
	base_folder_path = '/tmp/common-net-paramikosftpd-example'
	host_key_path = os.path.abspath('examples/host.key')
	rsync_binpath = '/usr/bin/rsync'
	shadow_folder_path = None
	rsync_state_folder = '/tmp/common-net-paramikosftpd-exrsyncstate'
	try:
		opts, _args, = getopt.getopt(argv, "hv", (
				"port=",
				"base-folder=",
				"host-key=",
				"rsync-bin=",
				"shadow-folder=",
				"state-folder=",
				"help",
		))
		for opt, arg, in opts:
			if opt in ("-h", "--help"):
				print(_HELP_TEXT)
				raise SystemExit(1)
			if opt == "-v":
				pass
			elif opt == "--port":
				sftp_port = int(arg)
			elif opt == "--base-folder":
				base_folder_path = os.path.abspath(arg)
			elif opt == "--host-key":
				host_key_path = os.path.abspath(arg)
			elif opt == "--rsync-bin":
				rsync_binpath = os.path.abspath(arg)
			elif opt == "--shadow-folder":
				shadow_folder_path = os.path.abspath(arg)
			elif opt == "--state-folder":
				rsync_state_folder = os.path.abspath(arg)
	except Exception:
		_log.exception("failed on parsing CLI options")
	if not base_folder_path:
		raise ValueError("option `--base-folder` is required.")
	os.makedirs(base_folder_path, exist_ok=True)
	return (
			sftp_port,
			base_folder_path,
			host_key_path,
			rsync_binpath,
			shadow_folder_path,
			rsync_state_folder,
	)


class RsyncStateAccess:
	def __init__(self, folder_path: str) -> None:
		self.folder_path = folder_path

	def _make_state_file_path(self, remote_username: str) -> str:
		return os.path.abspath(os.path.join(self.folder_path, remote_username + ".txt"))

	def fetch_state(self, remote_username: str) -> str:
		try:
			with open(self._make_state_file_path(remote_username), "r", encoding="utf-8") as fp:
				d = fp.read()
			return d
		except FileNotFoundError:
			pass
		except Exception:
			_log.exception("cannot fetch rsync state for [%r]", remote_username)
		return ''

	def save_state(self, remote_username: str, state_text: str) -> None:
		try:
			with open(self._make_state_file_path(remote_username), "w", encoding="utf-8") as fp:
				fp.write(state_text)
		except Exception:
			_log.exception("cannot save rsync state for [%r]", remote_username)


def main():
	log_level = logging.INFO if ("-v" not in sys.argv) else logging.DEBUG
	logging.basicConfig(stream=sys.stderr, level=log_level)
	sftp_port, base_folder_path, host_key_path, rsync_binpath, shadow_folder_path, rsync_state_folder = parse_options(sys.argv[1:])
	user_cfgs = make_example_users()
	for u in user_cfgs:
		u.prepare_user_folders(base_folder_path)
	os.makedirs(rsync_state_folder, mode=0o755, exist_ok=True)
	rsync_state_access = RsyncStateAccess(rsync_state_folder)
	rsync_opts = RsyncOptions(rsync_binpath, shadow_folder_path, rsync_state_access.fetch_state, rsync_state_access.save_state)
	SFTPServer.allow_reuse_address = True
	server = SFTPServer('', sftp_port, host_key_path, 4096, base_folder_path, user_cfgs, process_callable, rsync_opts=rsync_opts)
	with server:
		_log.info("listen on %r", server.server_address)
		server.serve_forever()


if __name__ == '__main__':
	main()
