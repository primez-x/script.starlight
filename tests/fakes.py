"""Small Kodi boundary fakes; runtime logic remains real."""
import json
import sys
import types


class VideoTag:
    FIELDS = {'setDbId': 'dbid', 'setMediaType': 'mediatype', 'setTitle': 'title',
              'setYear': 'year', 'setPlot': 'plot', 'setDuration': 'duration',
              'setGenres': 'genre', 'setRating': 'rating', 'setPlaycount': 'playcount',
              'setTrailer': 'trailer', 'setTvShowTitle': 'tvshowtitle', 'setSeason': 'season',
              'setEpisode': 'episode', 'setFilenameAndPath': 'filenameandpath', 'setCast': 'cast'}

    def __init__(self, info):
        self.info = info

    def __getattr__(self, name):
        if name not in self.FIELDS:
            raise AttributeError(name)
        return lambda value: self.info.__setitem__(self.FIELDS[name], value)

    def setResumePoint(self, position, total):
        self.info['resume'] = (position, total)


class ListItem:
    def __init__(self, label='', label2='', path='', **kwargs):
        self.label, self.label2, self.path = label, label2, path
        self.properties, self.art, self.info = {}, {}, {}

    def setProperty(self, name, value):
        self.properties[name] = value

    def getProperty(self, name):
        return self.properties.get(name, '')

    def setArt(self, art):
        self.art.update(art)

    def setInfo(self, kind, info):
        self.info.update(info)

    def getPath(self):
        return self.path

    def setPath(self, path):
        self.path = path

    def getVideoInfoTag(self):
        return VideoTag(self.info)


class Control:
    def __init__(self):
        self.items = []

    def reset(self):
        self.items = []

    def addItems(self, items):
        self.items.extend(items)

    def getSelectedItem(self):
        return self.items[0] if self.items else None


class Window:
    def __init__(self):
        self.props, self.controls = {}, {}

    def setProperty(self, key, value):
        self.props[key] = value

    def getProperty(self, key):
        return self.props.get(key, '')

    def clearProperty(self, key):
        self.props.pop(key, None)

    def getControl(self, number):
        return self.controls.setdefault(number, Control())


class Kodi:
    def __init__(self):
        self.windows, self.labels, self.conditions = {}, {}, set()
        self.calls, self.builtins, self.notices, self.infos = [], [], [], []
        self.responses = {}
        self.selection = -1
        self.skin = 'skin.starlight'

    def window(self, number):
        return self.windows.setdefault(number, Window())

    def rpc(self, payload):
        request = json.loads(payload)
        self.calls.append(request)
        result = self.responses.get(request['method'], {})
        if callable(result):
            result = result(request.get('params', {}))
        return json.dumps({'jsonrpc': '2.0', 'id': request['id'], **result})

    def install(self):
        xbmc = types.ModuleType('xbmc')
        xbmc.executeJSONRPC = self.rpc
        xbmc.getInfoLabel = lambda name: self.labels.get(name, '')
        xbmc.getCondVisibility = lambda name: name in self.conditions
        xbmc.executebuiltin = self.builtins.append
        xbmc.getSkinDir = lambda: self.skin
        xbmc.Actor = lambda **values: types.SimpleNamespace(**values)
        xbmc.LOGWARNING = 2
        xbmc.log = lambda *args: None
        xbmc.Monitor = type('Monitor', (), {'waitForAbort': lambda monitor, timeout: False})
        gui = types.ModuleType('xbmcgui')
        gui.Window = self.window
        gui.ListItem = ListItem
        gui.Dialog = lambda: types.SimpleNamespace(
            select=lambda *args: self.selection,
            notification=lambda *args, **kwargs: self.notices.append(args),
            info=self.infos.append,
        )
        sys.modules.update(xbmc=xbmc, xbmcgui=gui)
        return self
