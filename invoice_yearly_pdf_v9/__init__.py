# -*- coding: utf-8 -*-
import logging
import os
import subprocess

_logger = logging.getLogger(__name__)

try:
    _dir = os.path.dirname(os.path.abspath(__file__))
    _commit = subprocess.check_output(
        ['git', '-C', _dir, 'rev-parse', '--short', 'HEAD'],
        stderr=subprocess.DEVNULL,
    ).decode().strip()
    _logger.info('invoice_yearly_pdf_v9 loaded — commit: %s', _commit)
except Exception:
    pass

from . import models
from . import wizard
