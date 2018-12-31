#!/usr/bin/env python3

from setuptools import setup

setup(
	name='GeckoMoped',
	version='1.0.32dev',
	license='Public Domain',

	# author info
	author='Jamie Smith at USC RPL',
	author_email='jsmith@crackofdawn.onmicrosoft.com',

	# source info
	packages=['geckomoped'],
	scripts=['bin/gmgui.py'],
	url='https://github.com/USCRPL/GeckoMoped',
	package_data={
		'': ['geckomoped/gm.glade', 'geckomoped/images/*']
	},
	include_package_data=True,
	zip_safe = False,

	# description
	description='Improved driver and API for the GeckoDrive GM215 motor controllers',
	long_description=open('README.md').read(),
	long_description_content_type='text/markdown',

	# dependencies
    install_requires=[
        "pyserial >= 3.0",
        "pygobject >= 3.0.0",
    ],
)