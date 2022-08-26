#!/usr/bin/env python

"""
    constants_service.py:
    Provides project wide constant values.
"""

import logging

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)


class ConstantsService:
    """
        Provides project wide constant values.
    """

    ALB_CREATION_TAG_KEY = "FLEET_ALB_CREATION"
    ALB_CREATION_TAG_VALUE= "DYNAMIC"
    FILTER_BY_GROUP = "FILTER_BY_GROUP"
    FILTER_BY_CREATION_DYNAMIC = "FILTER_BY_CREATION_DYNAMIC"
