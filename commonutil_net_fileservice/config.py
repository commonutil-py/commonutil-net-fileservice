# -*- coding: utf-8 -*-
"""
Configuration data classes
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Optional
import logging
import os

_log = logging.getLogger(__name__)

_REV = "0.0.2; 54e6001c024d5e934f47fe6f4451084986e8f9f0"  # REV-CONSTANT:full 5d022db7d38f580a850cd995e26a6c2f

DEFAULT_REV_FILENAME = "_rev-info.txt"
DEFAULT_REV_CONTENT = _REV + "\n"


def _default_credential_checker(u: User, remote_credential: str) -> bool:
	return (u.credential == remote_credential)


class SSHPKey:
	__slots__ = (
			'key_type',
			'b64_text',
	)

	def __init__(self, key_type: str, b64_text: str) -> None:
		self.key_type = key_type
		self.b64_text = b64_text


def unpack_ssh_pkey(pkey_text: str) -> Optional[SSHPKey]:
	aux = pkey_text.split(' ', 3)
	if len(aux) < 2:
		return None
	return SSHPKey(aux[0], aux[1])


class User:
	__slots__ = (
			'username',
			'prebuild_folders',
			'credential',
			'ssh_pkeys',
	)

	credential_checker: Callable[[User, str], bool] = _default_credential_checker

	def __init__(self, username: str, prebuild_folders: Iterable[str], credential: Any, ssh_pkeys: Iterable[SSHPKey]) -> None:
		self.username = username
		self.prebuild_folders = prebuild_folders
		self.credential = credential
		self.ssh_pkeys = ssh_pkeys if ssh_pkeys else ()

	def check_credential(self, remote_credential: str) -> bool:
		""" Return True if given `remote_credential` is accepted.
		"""
		return self.credential_checker(remote_credential)

	def check_ssh_pkey(self, key_type: str, b64_text: str) -> Optional[SSHPKey]:
		""" Return SSHPKey instance if matching key is found.
		"""
		for pk in self.ssh_pkeys:
			if (pk.key_type == key_type) and (pk.b64_text == b64_text):
				return pk
		return None

	def prepare_user_folders(self, base_folder_path: str) -> None:
		if not self.prebuild_folders:
			return
		user_folder_path = os.path.abspath(os.path.join(base_folder_path, self.username))
		for d_path in self.prebuild_folders:
			target_path = os.path.abspath(os.path.join(user_folder_path, d_path))
			if not target_path.startswith(user_folder_path):
				_log.warning("escaped user pre-build folder (username=%r, user-folder=%r): %r", self.username, user_folder_path, target_path)
				continue
			os.makedirs(target_path, exist_ok=True)


def make_users_map(users: Iterable[User]) -> Mapping[str, User]:
	result = {}
	for u in users:
		result[u.username] = u
	return result
