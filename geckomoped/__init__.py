import os

# -*- coding: utf-8 -*-
# Copyright (c) 2014 Geckodrive Inc.

thisdir = os.path.dirname(os.path.realpath(__file__))

_gladedir = thisdir
_imagedir = os.path.join(thisdir, "images")
_icondir = os.path.join(thisdir, "images")
_version = "1.0.32"
_app_fullname = "geckomoped-" + _version
 
__all__ = ['assemble.py', 'devices.py', 'mockui.py', 'gm_api.py']

