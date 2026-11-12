"""
Helpers for the folder (path) of a sandbox definition inside its Git repository.
"""
import posixpath
import re

from crczp.sandbox_common_lib import exceptions

ALLOWED_PATH_CHARACTERS = re.compile(r'^[A-Za-z0-9._/-]*$')


def normalize_definition_path(path: str | None) -> str:
    """Validate the folder of a definition inside the repository and return its normalized form.

    Leading and trailing whitespace and slashes are stripped. Empty path means the repository root.

    :param path: Folder inside the Git repository, relative to the repository root
    :return: Normalized path ('' for the repository root)
    :raise: ValidationError if the path is absolute, contains '..' or contains forbidden characters
    """
    if path is None:
        return ''
    if not isinstance(path, str):
        raise exceptions.ValidationError('Definition path must be a string.')
    path = path.strip()
    if path.startswith(('/', '\\')):
        raise exceptions.ValidationError(f'Definition path must be relative, got "{path}".')
    path = path.rstrip('/').strip()
    if not ALLOWED_PATH_CHARACTERS.match(path):
        raise exceptions.ValidationError(
            f'Definition path "{path}" contains forbidden characters. Allowed: A-Z a-z 0-9 . _ / -')
    if '..' in path.split('/'):
        raise exceptions.ValidationError(f'Definition path "{path}" must not contain ".." segments.')
    return path


def join_definition_path(path: str, file_path: str) -> str:
    """Prefix a file path inside the definition folder with the definition path."""
    return posixpath.join(path, file_path) if path else file_path
