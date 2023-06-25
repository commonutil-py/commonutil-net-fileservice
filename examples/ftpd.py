# -*- coding: utf-8 -*-
"""
Example code for setting up FTP service with pyftpdlib
"""

import sys
import os
import getopt
import logging
import socket
from pyftpdlib.servers import FTPServer

from commonutil_net_fileservice.pyftpdlib import setup_handlers

from examples.common import make_example_users, process_callable

_log = logging.getLogger(__name__)

_HELP_TEXT = """
Options:
	-h | --help
		Print help message
	--port=[FTP_CONTROL_PORT]
		Control port of FTP service. (default: 2121)
	--base-folder=[BASE_FOLDER_PATH]
		Path of base folder. (default: /tmp/common-net-pyftpdlib-example)
""".replace("\t", "    ")


def parse_options(argv):
	ftp_port = 2121
	base_folder_path = '/tmp/common-net-pyftpdlib-example'
	try:
		opts, _args, = getopt.getopt(argv, "hv", (
				"port=",
				"base-folder=",
				"help",
		))
		for opt, arg, in opts:
			if opt in ("-h", "--help"):
				print(_HELP_TEXT)
				raise SystemExit(1)
			if opt == "-v":
				pass
			elif opt == "--port":
				ftp_port = int(arg)
			elif opt == "--base-folder":
				base_folder_path = os.path.abspath(arg)
	except Exception:
		_log.exception("failed on parsing CLI options")
	if not base_folder_path:
		raise ValueError("option `--base-folder` is required.")
	os.makedirs(base_folder_path, exist_ok=True)
	return (
			ftp_port,
			base_folder_path,
	)


def main():
	log_level = logging.INFO if ("-v" not in sys.argv) else logging.DEBUG
	logging.basicConfig(stream=sys.stderr, level=log_level)
	ftp_port, base_folder_path, = parse_options(sys.argv[1:])
	user_cfgs = make_example_users()
	for u in user_cfgs:
		u.prepare_user_folders(base_folder_path)
	ftp_hnd, _auth_hnd = setup_handlers("Example FTP service", base_folder_path, user_cfgs, process_callable, None, None)
	sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
	sock.bind(('', ftp_port))
	with FTPServer(sock, ftp_hnd) as server:
		server.serve_forever()
	sock.close()


if __name__ == '__main__':
	main()
