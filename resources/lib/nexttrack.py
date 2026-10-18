"""Read the active music queue and publish the Starlight countdown."""
import math
import time

import xbmc

from .kodi import RpcError, home, join_names, publish, rpc

PREFIX = 'Starlight.NextTrack.'
KEYS = ('Title', 'Artist', 'Album', 'Art', 'Remaining', 'Active', 'Available', 'Progress')


def seconds(value):
    return sum(float(value.get(key, 0)) * scale for key, scale in (
        ('hours', 3600), ('minutes', 60), ('seconds', 1), ('milliseconds', .001)))


class NextTrackService:
    def __init__(self):
        self.window = home()

    def clear(self):
        for key in KEYS:
            publish(self.window, PREFIX + key, '')

    def offset_item(self):
        if xbmc.getCondVisibility('Playlist.IsRepeatOne'):
            return {}, 0
        track = {key: xbmc.getInfoLabel('MusicPlayer.offset(1).' + label)
                 for key, label in (('title', 'Title'), ('artist', 'Artist'),
                                    ('album', 'Album'), ('thumb', 'Cover'))}
        remaining = xbmc.getInfoLabel('Player.TimeRemaining(ss)')
        return track, float(remaining or 0)

    def queue_item(self, state):
        position = state['position']
        if state['repeat'] == 'one':
            return {}
        if position < 0 or state['playlistid'] < 0:
            return self.offset_item()[0]
        result = rpc('Playlist.GetItems', playlistid=state['playlistid'],
                     limits={'start': position, 'end': position + 2},
                     properties=['title', 'artist', 'album', 'art', 'file'])
        items = result.get('items', [])
        if len(items) > 1:
            track = dict(items[1])
        elif state['repeat'] == 'all' and result.get('limits', {}).get('total', 0) > 1:
            wrapped = rpc('Playlist.GetItems', playlistid=state['playlistid'],
                          limits={'start': 0, 'end': 1},
                          properties=['title', 'artist', 'album', 'art', 'file'])
            track = dict((wrapped.get('items') or [{}])[0])
        else:
            return {}
        # Match the exact queued URL before accepting delayed Spotify hooks.
        hook_file = self.window.getProperty('Spotify.NextTrackFile')
        if (hook_file and hook_file == track.get('file')
                and self.window.getProperty('Spotify.CurrentTrackId')):
            for key, label in (('title', 'Title'), ('artist', 'Artist'), ('album', 'Album')):
                track[key] = track.get(key) or self.window.getProperty('Spotify.NextTrack' + label)
            art = dict(track.get('art') or {})
            art['thumb'] = art.get('thumb') or self.window.getProperty('Spotify.NextTrackThumb')
            track['art'] = art
        return track

    def refresh(self):
        try:
            if xbmc.getSkinDir() != 'skin.starlight':
                self.clear()
                return 5
            players = rpc('Player.GetActivePlayers')
            audio = next((player for player in players if player['type'] == 'audio'), None)
            if audio is None:
                self.clear()
                return 5
            try:
                state = rpc('Player.GetProperties', playerid=audio['playerid'], properties=[
                    'time', 'totaltime', 'speed', 'position', 'playlistid', 'repeat'])
                remaining = seconds(state['totaltime']) - seconds(state['time'])
                if not seconds(state['totaltime']):
                    remaining = 0
                track = self.queue_item(state)
            except RpcError:
                track, remaining = self.offset_item()
            if not math.isfinite(remaining):
                remaining = 0
            title = track.get('title') or track.get('label') or ''
            art = track.get('art') or {}
            cover = next((art[key] for key in ('thumb', 'poster', 'icon', 'fanart') if art.get(key)), '')
            active = bool(title) and 0 < remaining <= 15
            values = {
                'Title': title, 'Artist': join_names(track.get('artist')),
                'Album': track.get('album', ''), 'Art': cover or track.get('thumb', ''),
                'Available': 'true' if title else '',
                'Remaining': str(math.ceil(remaining)) if active else '',
                'Progress': str(round(remaining / 15 * 100)) if active else '',
                'Active': 'true' if active else '',
            }
            # Hide the pill before replacing metadata. Publish Active last.
            if any(self.window.getProperty(PREFIX + key) != str(values[key])
                   for key in ('Title', 'Artist', 'Album', 'Art')):
                publish(self.window, PREFIX + 'Active', '')
            for key, value in values.items():
                publish(self.window, PREFIX + key, value)
            if active:
                return 1
            return max(.1, min(5, remaining - 15)) if remaining > 15 else 5
        except (RpcError, KeyError, TypeError, ValueError, AttributeError):
            self.clear()
            return 5


class QueueMonitor(xbmc.Monitor):
    dirty = True

    def onNotification(self, sender, method, data):
        if method.startswith(('Player.', 'Playlist.')):
            self.dirty = True


def run():
    monitor = QueueMonitor()
    service = NextTrackService()
    service.clear()
    due = 0
    try:
        while not monitor.abortRequested():
            now = time.monotonic()
            if monitor.dirty or now >= due:
                monitor.dirty = False
                due = now + service.refresh()
            if monitor.waitForAbort(.25):
                break
    finally:
        service.clear()
