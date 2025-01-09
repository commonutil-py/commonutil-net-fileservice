# -*- coding: utf-8 -*-
"""
Example code for setting up FTP service with pyftpdlib
"""

import getopt
import logging
import os
import sys

from commonutil_net_fileservice.paramikosftp import SFTPServer

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
""".replace("\t", "    ")


def parse_options(argv):
	sftp_port = 2222
	base_folder_path = "/tmp/common-net-paramikosftpd-example"
	host_key_path = os.path.abspath("examples/host.key")
	moduli_path = os.path.abspath("examples/moduli")
	try:
		opts, _args = getopt.getopt(
			argv,
			"hv",
			(
				"port=",
				"base-folder=",
				"host-key=",
				"moduli=",
				"help",
			),
		)
		for opt, arg in opts:
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
			elif opt == "--moduli":
				if arg:
					moduli_path = os.path.abspath(arg)
				else:
					moduli_path = None
	except Exception:
		_log.exception("failed on parsing CLI options")
	if not base_folder_path:
		raise ValueError("option `--base-folder` is required.")
	os.makedirs(base_folder_path, exist_ok=True)
	return (
		sftp_port,
		base_folder_path,
		host_key_path,
		moduli_path,
	)


def main():
	log_level = logging.INFO if ("-v" not in sys.argv) else logging.DEBUG
	logging.basicConfig(stream=sys.stderr, level=log_level)
	sftp_port, base_folder_path, host_key_path, moduli_path = parse_options(sys.argv[1:])
	user_cfgs = make_example_users()
	for u in user_cfgs:
		u.prepare_user_folders(base_folder_path)
	SFTPServer.allow_reuse_address = True
	server = SFTPServer(
		"", sftp_port, host_key_path, 4096, base_folder_path, user_cfgs, process_callable, moduli_path=moduli_path
	)
	with server:
		_log.info("listen on %r", server.server_address)
		server.serve_forever()


if __name__ == "__main__":
	main()
