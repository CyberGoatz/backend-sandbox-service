from django.core.cache import cache
import structlog

from crczp.sandbox_common_lib import utils

IMAGE_LIST_CACHE_KEY = 'image_list'
IMAGE_LIST_CACHE_TIMEOUT = 60 * 60 * 24
LOG = structlog.get_logger()


def _cache_get(key, default=None):
    try:
        return cache.get(key, default)
    except Exception as ex:
        LOG.warning('Cloud image cache get failed; continuing without cache.', key=key, error=str(ex))
        return default


def _cache_set(key, value, timeout):
    try:
        cache.set(key, value, timeout)
    except Exception as ex:
        LOG.warning('Cloud image cache set failed; continuing without cache.', key=key, error=str(ex))


def list_images(cached=True):
    """
    Get list of images as generator
    """
    if cached:
        image_set = _cache_get(IMAGE_LIST_CACHE_KEY)
        if image_set is not None:
            return image_set

    client = utils.get_terraform_client()
    image_set = client.list_images()
    _cache_set(IMAGE_LIST_CACHE_KEY, image_set, IMAGE_LIST_CACHE_TIMEOUT)
    return image_set
