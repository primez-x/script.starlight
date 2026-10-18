"""RunScript(script.starlight,command,key=value,...) entry point."""
import sys

import xbmc
import xbmcgui

from resources.lib import actions, search
from resources.lib.kodi import RpcError


def dispatch(argv):
    if not argv:
        raise ValueError('A Starlight command is required.')
    command, *arguments = argv
    args = {}
    for argument in arguments:
        key, separator, value = argument.partition('=')
        if not separator or not key or key in args:
            raise ValueError('Use unique key=value arguments.')
        args[key] = value
    if command in actions.COMMANDS:
        return actions.run(command, args)
    handler = {'search': search.run, 'search.render': search.render_saved,
               'search.select': search.select}.get(command)
    if handler is None:
        raise ValueError('Unknown Starlight command.')
    return handler(args)


def main():
    try:
        dispatch(sys.argv[1:])
    except (ValueError, RpcError, KeyError, TypeError, RuntimeError) as error:
        # Keep payloads and provider URLs out of logs and notifications.
        message = str(error) if isinstance(error, ValueError) else 'The requested data is unavailable.'
        xbmc.log('[script.starlight] ' + message, xbmc.LOGWARNING)
        xbmcgui.Dialog().notification('Starlight', message, time=4000, sound=False)


if __name__ == '__main__':
    main()
