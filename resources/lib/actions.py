"""Resolve music identities and call the owning add-on's public routes."""
import re
from urllib.parse import parse_qs, urlencode, urlsplit

import xbmc
import xbmcgui

from .kodi import home, join_names, positive_id, rpc, words

SPOTIFY = 'plugin://plugin.audio.spotifykodiconnect/'
ARTIST_ROUTES = ('browse_artist_everything', 'browse_artist_just_albums_and_singles')
COMMANDS = ('spotify.like', 'spotify.playlist', 'spotify.radio', 'spotify.follow',
            'music.artist', 'music.album')


def spotify_id(value):
    if not re.fullmatch(r'[A-Za-z0-9]{22}', str(value)):
        raise ValueError('A Spotify ID with 22 letters or digits is required.')
    return value


def url(action, **params):
    return SPOTIFY + '?' + urlencode({'action': action, **params})


def music_window(path):
    # Paths are constructed here or pass the provider URL validator.
    if any(char in path for char in '\r\n"'):
        raise ValueError('The music path contains invalid characters.')
    xbmc.executebuiltin('ActivateWindow(Music,"' + path + '",return)')


def spotify_search(kind, query, current=lambda: True):
    action, key = {'song': ('search_tracks', 'trackid'), 'album': ('search_albums', 'albumid'),
                   'artist': ('search_artists', 'artistid')}[kind]
    path, offset, count, page_count = url(action, **{key: query}), 0, 0, 0
    while current() and count < 100 and page_count < 2:
        page_count += 1
        result = rpc('Files.GetDirectory', directory=path, media='music',
                     properties=['title', 'artist', 'year', 'art'])
        next_offset = None
        for row in result.get('files', []):
            parsed = urlsplit(row.get('file', ''))
            params = parse_qs(parsed.query)
            if (parsed.scheme == 'plugin' and parsed.netloc == 'plugin.audio.spotifykodiconnect'
                    and params.get('action') == [action] and params.get(key) == [query]):
                value = params.get('offset', [''])[0]
                if re.fullmatch(r'[0-9]+', value) and int(value) > offset:
                    next_offset = int(value)
                continue
            yield row
            count += 1
            if count >= 100:
                break
        if next_offset is None:
            break
        offset = next_offset
        path = url(action, **{key: query}, offset=offset, limit=50)


def resolve_spotify(kind):
    name = xbmc.getInfoLabel('MusicPlayer.' + kind.title())
    if not name:
        raise ValueError('The current ' + kind + ' name is unavailable.')
    if kind == 'artist' and ' / ' in name:
        names = list(dict.fromkeys(part.strip() for part in name.split(' / ') if part.strip()))
        choice = xbmcgui.Dialog().select('Choose artist', names)
        if not 0 <= choice < len(names):
            return None
        name = names[choice]
    key = kind + 'id'
    matches = {}
    for row in spotify_search(kind, name):
        if words(row.get('title') or row.get('label')) != words(name):
            continue
        parsed = urlsplit(row.get('file', ''))
        if parsed.scheme != 'plugin' or parsed.netloc != 'plugin.audio.spotifykodiconnect':
            continue
        params = parse_qs(parsed.query)
        expected = ('browse_album',) if kind == 'album' else ARTIST_ROUTES
        if params.get('action', [''])[0] not in expected or len(params.get(key, [])) != 1:
            continue
        ident = spotify_id(params[key][0])
        matches[ident] = row
    if not matches:
        raise ValueError('No exact Spotify ' + kind + ' match is available.')
    choices = sorted(matches)
    if len(choices) == 1:
        return choices[0]
    labels = [(matches[value].get('label') or name) + ' · ' +
              (join_names(matches[value].get('artist')) or value) for value in choices]
    choice = xbmcgui.Dialog().select('Choose ' + kind, labels)
    return choices[choice] if 0 <= choice < len(choices) else None


def local_music(kind):
    song = positive_id(xbmc.getInfoLabel('MusicPlayer.DBID'))
    details = rpc('AudioLibrary.GetSongDetails', songid=song,
                  properties=['artistid', 'albumid'])['songdetails']
    if kind == 'album':
        ident = positive_id(details['albumid'])
    else:
        artists = [positive_id(value) for value in details.get('artistid', [])]
        if not artists:
            raise ValueError('The current song has no library artist.')
        choice = 0
        if len(artists) > 1:
            labels = [rpc('AudioLibrary.GetArtistDetails', artistid=value)['artistdetails']['label']
                      for value in artists]
            choice = xbmcgui.Dialog().select('Choose artist', labels)
            if choice < 0:
                return
        ident = artists[choice]
    music_window('musicdb://' + kind + 's/' + str(ident) + '/')


def run(command, args):
    if command not in COMMANDS:
        raise ValueError('Unknown music command.')
    allowed = {'music.artist': {'artistid'}, 'spotify.follow': {'artistid'},
               'music.album': {'albumid'}}.get(command, set())
    if set(args) - allowed:
        raise ValueError('Unknown argument for this music command.')
    if not xbmc.getCondVisibility('Player.HasAudio'):
        raise ValueError('Start an audio track before this action.')
    window = home()
    track = window.getProperty('Spotify.CurrentTrackId')
    if command.startswith('music.') and not track:
        if args:
            raise ValueError('Spotify IDs require a current Spotify track.')
        return local_music(command.split('.')[1])
    if not xbmc.getCondVisibility('System.HasAddon(plugin.audio.spotifykodiconnect)'):
        raise ValueError('Spotify Kodi Connect is unavailable.')
    spotify_id(track)
    if command == 'spotify.like':
        xbmc.executebuiltin('NotifyAll(plugin.audio.spotifykodiconnect,Other.ToggleLike)')
    elif command == 'spotify.playlist':
        xbmc.executebuiltin('RunPlugin(' + url('add_track_to_playlist', trackid=track) + ')')
    elif command == 'spotify.radio':
        music_window(url('browse_radio', trackid=track))
    else:
        kind = 'album' if command == 'music.album' else 'artist'
        ident = spotify_id(args[kind + 'id']) if args.get(kind + 'id') else resolve_spotify(kind)
        if ident is None:
            return
        if window.getProperty('Spotify.CurrentTrackId') != track:
            raise ValueError('The current track changed. Choose the action again.')
        if command == 'spotify.follow':
            xbmc.executebuiltin('RunPlugin(' + url('follow_artist', artistid=ident) + ')')
        else:
            action = 'browse_album' if kind == 'album' else 'browse_artist_everything'
            music_window(url(action, **{kind + 'id': ident}))
