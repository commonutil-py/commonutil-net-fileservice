#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path

from setuptools import setup

long_description = (Path(__file__).parent / "README.md").read_text()

setup(
		name="commonutil-net-fileservice",
		version="0.1.3",  # REV-CONSTANT:rev 5d022db7d38f580a850cd995e26a6c2f
		description="Helper routine for file service",
		packages=[
				"commonutil_net_fileservice",
		],
		classifiers=[
				"Development Status :: 5 - Production/Stable",
				"Intended Audience :: Developers",
				"License :: OSI Approved :: MIT License",
				"Operating System :: POSIX",
				"Programming Language :: Python :: 3.8",
		],
		long_description=long_description,
		long_description_content_type="text/markdown",
		license="MIT License",
)

# vim: ts=4 sw=4 ai nowrap
