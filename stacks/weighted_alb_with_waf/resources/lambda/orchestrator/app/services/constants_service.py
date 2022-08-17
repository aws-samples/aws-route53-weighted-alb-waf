import logging

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

class ConstantsService:

    ALB_CREATION_TAG_KEY = "FLEET_ALB_CREATION"
    ALB_CREATION_TAG_VALUE= "DYNAMIC"
    FILTER_BY_GROUP = "FILTER_BY_GROUP"
    FILTER_BY_CREATION_DYNAMIC = "FILTER_BY_CREATION_DYNAMIC"