# -*- coding: utf-8 -*-
"""
Shared logic between examples
"""

from typing import Iterable
import logging

from commonutil_net_fileservice.config import User, unpack_ssh_pkey

_log = logging.getLogger(__name__)

_SSHPUBKEY1 = ("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDhEvdiQ1XS/I+55V/vL6oe3QW/ktB1I"
				"HZszNkEP6sNUCY02vLAgFVE0ouaPUWGX3+ZzWEmxoFx0rRRuxRNFwzrczQw8ksdrhjSSm"
				"gHttMTQy71LiiHin1flZInZBeIPx6D1tcc0dA7V+/lykmNZqGKXReTHg+8l29HJG7ebp8"
				"Nhzh0zBsiCXsv2hcbuGeWzl/UhIan4UXShA8lbOB9p+Bea4k8yippO78oi3j5UiiOw75L"
				"S0XHf2pUJ06e07yfavuuh+/jPH0lE+rdBEF+TW9pVXIqPkx0MqdozLVp7X3/+fu3OTvTv"
				"uM21w0RUQgejNDhhuhsoHrSvV3GCjAGs4q+Zlktny9mwiaQk15+HtlHNrkj/oY4g9kZga"
				"TTE4WlbSmsPWfYIHIjwsWBRqTTohIkL8oZnd8qQr31KeB1uPHyA/I7uUxppG9EXeeQeUV"
				"0ciPRyf1k/lW3EivEoc9QoHjolMocDZurk9wDZ2kP+ZVmFQp7cyKMsELaERI+jcyAqyU="
				" dev@example.net")


def make_example_users() -> Iterable[User]:
	result = [
			User("user1", None, "pass1", (unpack_ssh_pkey(_SSHPUBKEY1), )),
			User("user2", (
					"d1/f01",
					"d1/f02",
					"d2/f01",
					"d3",
			), "pass2", None),
			User("user3", None, "pass3", None, "/tmp/common-net-fsrv-user3")
	]
	return result


def process_callable(username: str, remote_location: str, abspath: str, relpath: str) -> None:
	_log.info("process_callable(username=%r, remote_location=%r, abspath=%r, relpath=%r)", username, remote_location, abspath, relpath)
