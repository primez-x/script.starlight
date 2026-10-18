"""Shared Kodi boundaries."""
import json
import re
import unicodedata

import xbmc
import xbmcgui


class RpcError(RuntimeError):
    """A Kodi RPC failed without exposing the request payload."""


def rpc(method, **params):
    try:
        response = json.loads(xbmc.executeJSONRPC(json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params,
        })))
    except (RuntimeError, TypeError, ValueError) as error:
        raise RpcError(method) from error
    if not isinstance(response, dict) or 'error' in response or 'result' not in response:
        raise RpcError(method)
    return response['result']


def home():
    return xbmcgui.Window(10000)


def publish(window, name, value):
    value = str(value) if value is not None else ''
    if value:
        if window.getProperty(name) != value:
            window.setProperty(name, value)
    elif window.getProperty(name):
        window.clearProperty(name)


def words(value):
    text = unicodedata.normalize('NFKD', str(value or '')).casefold()
    text = ''.join(char for char in text if not unicodedata.combining(char))
    return re.findall(r'[^\W_]+', text, re.UNICODE)


def positive_id(value):
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]*', str(value)):
        raise ValueError('A positive database ID is required.')
    return int(value)


def join_names(value):
    return ' / '.join(str(name) for name in value if name) if isinstance(value, list) else str(value or '')
