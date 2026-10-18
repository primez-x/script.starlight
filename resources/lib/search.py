"""Merge native library matches with separate optional provider rows."""
import json
import re
import uuid
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import xbmc
import xbmcgui

from .actions import ARTIST_ROUTES, music_window, spotify_id, spotify_search
from .kodi import RpcError, home, join_names, positive_id, publish, rpc, words

ROWS = ('top', 'movies', 'tvshows', 'episodes', 'music', 'spotify', 'people',
        'collections', 'genres', 'plex')
VIDEO = {'movie': 'movies', 'tvshow': 'tvshows', 'episode': 'episodes'}
SHAPES = {'movie': 'poster', 'tvshow': 'poster', 'episode': 'landscape',
          'song': 'square', 'album': 'square', 'artist': 'circle', 'person': 'circle',
          'set': 'poster', 'genre': 'square'}
PREFIX = 'Starlight.Search.'
PKC = 'plugin://plugin.video.plexkodiconnect/starlight/'


def matches(query, title):
    needles, haystack = words(query), words(title)
    return bool(needles) and any(
        all(haystack[start + offset].startswith(word) for offset, word in enumerate(needles))
        for start in range(len(haystack) - len(needles) + 1))


def row_name(record):
    if record['source'] == 'plex':
        return 'plex'
    if record['source'] == 'spotify':
        return 'spotify'
    return {**VIDEO, 'song': 'music', 'album': 'music', 'artist': 'music',
            'person': 'people', 'set': 'collections', 'genre': 'genres'}[record['type']]


def rank_key(record):
    kind = record['type']
    tier = 1 if kind in ('person', 'artist') else 2 if kind in ('set', 'genre') else 0
    resume = (record.get('resume') or {}).get('position', 0)
    progress = record.get('inprogress') or resume > 0
    status = 0 if progress else 2 if record.get('playcount', 0) > 0 else 1
    return tier, status, words(record['title']), record['source'], kind, str(record['id'])


def rank(query, records):
    rows = {name: [] for name in ROWS}
    unique = {}
    # Canonical duplicates and stable sort remove dependence on provider order.
    for record in sorted(records, key=lambda value: json.dumps(value, sort_keys=True)):
        if (record.get('type') not in SHAPES or record.get('source') not in ('library', 'spotify', 'plex')
                or not matches(query, record.get('title'))):
            continue
        unique.setdefault((record['source'], record['type'], str(record['id'])), record)
    for record in sorted(unique.values(), key=rank_key):
        name = row_name(record)
        rows[name].append(record)
        if name != 'plex':
            rows['top'].append(record)
    return rows


def pages(method, key, params, current):
    start = 0
    while current():
        result = rpc(method, **params, limits={'start': start, 'end': start + 200})
        items = result.get(key, [])
        if not isinstance(items, list):
            raise RpcError(method)
        yield from items
        limits = result.get('limits', {})
        end = limits.get('end', start + len(items))
        if not items or end <= start or end >= limits.get('total', end):
            break
        start = end


def library_record(item, kind):
    ident = positive_id(item[kind + 'id'])
    art = dict(item.get('art') or {})
    if item.get('thumbnail'):
        art.setdefault('thumb', item['thumbnail'])
    record = {'id': str(ident), 'type': kind, 'source': 'library',
              'title': item.get('title') or item.get('label') or '', 'art': art}
    for key in ('artist', 'album', 'year', 'playcount', 'resume', 'season', 'episode', 'showtitle'):
        if key in item:
            record[key] = item[key]
    if kind == 'tvshow':
        watched, total = item.get('watchedepisodes', 0), item.get('episode', 0)
        record['inprogress'] = 0 < watched < total
    return record


def collect(query, spotify=False, plex=False, current=lambda: True):
    records, errors = [], set()
    if not words(query):
        return records, errors
    # Contains selects candidates. The ranker enforces word-start matches.
    term = query.strip().split()[0]
    title_filter = {'field': 'title', 'operator': 'contains', 'value': term}
    sources = (
        ('VideoLibrary.GetMovies', 'movies', 'movie', ['title', 'art', 'year', 'playcount', 'resume']),
        ('VideoLibrary.GetTVShows', 'tvshows', 'tvshow', ['title', 'art', 'year', 'playcount', 'episode', 'watchedepisodes']),
        ('VideoLibrary.GetEpisodes', 'episodes', 'episode', ['title', 'art', 'playcount', 'resume', 'season', 'episode', 'showtitle']),
        ('AudioLibrary.GetSongs', 'songs', 'song', ['title', 'art', 'artist', 'album', 'playcount']),
        ('AudioLibrary.GetAlbums', 'albums', 'album', ['title', 'art', 'artist', 'playcount']),
        ('AudioLibrary.GetArtists', 'artists', 'artist', ['art', 'thumbnail']),
        ('VideoLibrary.GetMovieSets', 'sets', 'set', ['title', 'art', 'playcount']),
    )
    for method, key, kind, fields in sources:
        if not current():
            return records, errors
        params = {'properties': fields}
        if kind != 'set':
            params['filter'] = ({'field': 'artist', 'operator': 'contains', 'value': term}
                                if kind == 'artist' else
                                {'field': 'album', 'operator': 'contains', 'value': term}
                                if kind == 'album' else title_filter)
        try:
            for item in pages(method, key, params, current):
                records.append(library_record(item, kind))
        except (RpcError, KeyError, TypeError, ValueError):
            errors.add(row_name({'type': kind, 'source': 'library'}))
    # Kodi exposes actor filters on movies and shows, rather than an actor RPC.
    for method, key in (('VideoLibrary.GetMovies', 'movies'), ('VideoLibrary.GetTVShows', 'tvshows')):
        try:
            params = {'filter': {'field': 'actor', 'operator': 'contains', 'value': term},
                      'properties': ['cast']}
            for item in pages(method, key, params, current):
                for actor in item.get('cast', []):
                    if matches(query, actor.get('name')):
                        records.append({'id': actor['name'], 'title': actor['name'],
                                        'type': 'person', 'source': 'library',
                                        'art': {'thumb': actor.get('thumbnail', '')}})
        except (RpcError, KeyError, TypeError, ValueError):
            errors.add('people')
    for media in ('movie', 'tvshow', 'music'):
        method = 'AudioLibrary.GetGenres' if media == 'music' else 'VideoLibrary.GetGenres'
        params = {} if media == 'music' else {'type': media}
        try:
            for item in pages(method, 'genres', params, current):
                record = library_record(item, 'genre')
                record['id'] = media + ':' + record['id']
                record['media'] = media
                records.append(record)
        except (RpcError, KeyError, TypeError, ValueError):
            errors.add('genres')
    more, failed = collect_providers(query, spotify, plex, current)
    return records + more, errors | failed


def provider_record(item, source, kind):
    path = item.get('file') or ''
    parsed = urlsplit(path)
    params = parse_qs(parsed.query)
    relay = source == 'spotify' and kind == 'song' and parsed.scheme == 'http'
    if (parsed.scheme != 'plugin' and not relay) or parsed.fragment or kind not in SHAPES:
        raise ValueError('Unsupported provider path.')
    if any(len(values) != 1 for values in params.values()):
        raise ValueError('Duplicate provider arguments are unsupported.')
    if relay:
        match = re.fullmatch(r'/track/([A-Za-z0-9]{22})/[1-9][0-9]*ms\.xwav', parsed.path)
        if (not match or parsed.query or parsed.port is None or not 1024 <= parsed.port <= 65535
                or parsed.netloc != '127.0.0.1:' + str(parsed.port)):
            raise ValueError('Unsupported Spotify relay.')
        ident = match[1]
    elif source == 'spotify':
        if parsed.netloc != 'plugin.audio.spotifykodiconnect' or parsed.path not in ('', '/'):
            raise ValueError('Unsupported Spotify provider.')
        action, key = {'song': (('play_in_queue',), 'track'), 'album': (('browse_album',), 'albumid'),
                       'artist': (ARTIST_ROUTES, 'artistid')}[kind]
        if params.get('action', [''])[0] not in action or len(params.get(key, [])) != 1:
            raise ValueError('Unsupported Spotify route.')
        ident = spotify_id(params[key][0])
        if kind == 'song':
            required = {'ms', 'position', 'list'}
            if not required.issubset(params):
                raise ValueError('The Spotify song lacks queue fields.')
            if (not re.fullmatch(r'[1-9][0-9]*', params['ms'][0])
                    or not re.fullmatch(r'[0-9]+', params['position'][0])):
                raise ValueError('Invalid Spotify queue position or duration.')
        path = 'plugin://plugin.audio.spotifykodiconnect/?' + urlencode(params, doseq=True)
    elif source == 'plex':
        if (parsed.netloc != 'plugin.video.plexkodiconnect' or parsed.path != '/starlight/item'
                or set(params) != {'rating_key'} or kind not in ('movie', 'tvshow', 'episode')):
            raise ValueError('Unsupported Plex info route.')
        ident = params['rating_key'][0]
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', ident):
            raise ValueError('Invalid Plex rating key.')
        path = PKC + 'item?' + urlencode({'rating_key': ident})
    else:
        raise ValueError('Unknown provider.')
    return {'id': ident, 'title': item.get('title') or item.get('label') or '',
            'source': source, 'type': kind, 'path': path, 'art': item.get('art') or {},
            'artist': item.get('artist') or [], 'year': item.get('year', 0)}


def collect_providers(query, spotify, plex, current):
    records, errors = [], set()
    sources = []
    if spotify and xbmc.getCondVisibility('System.HasAddon(plugin.audio.spotifykodiconnect)'):
        sources.extend(('spotify', kind) for kind in ('song', 'album', 'artist'))
    if plex and xbmc.getCondVisibility('System.HasAddon(plugin.video.plexkodiconnect)'):
        sources.append(('plex', None))
    for source, kind in sources:
        if not current():
            break
        try:
            items = (spotify_search(kind, query, current) if source == 'spotify' else
                     pages('Files.GetDirectory', 'files', {
                         'directory': PKC + 'search?' + urlencode({'query': query}),
                         'media': 'video', 'properties': ['title', 'artist', 'year', 'art']}, current))
            for item in items:
                try:
                    records.append(provider_record(item, source, kind or item.get('type', 'movie')))
                except (ValueError, KeyError, TypeError):
                    errors.add(source)
        except (RpcError, TypeError, ValueError):
            errors.add(source)
    return records, errors


def target(args):
    window = positive_id(args.get('window', '11101'))
    control = positive_id(args.get('control', '510'))
    if window > 99999 or control > 99999:
        raise ValueError('The window or control ID exceeds the supported range.')
    source = args.get('source', 'top')
    if source not in ROWS:
        raise ValueError('Unknown search source.')
    mapping = {source: control}
    if args.get('rows'):
        mapping = {}
        for value in args['rows'].split('|'):
            name, separator, number = value.partition(':')
            if not separator or name not in ROWS or name in mapping:
                raise ValueError('Use unique source:control pairs for rows.')
            mapping[name] = positive_id(number)
        if len(set(mapping.values())) != len(mapping) or any(value > 99999 for value in mapping.values()):
            raise ValueError('Search rows require unique control IDs below 100000.')
    return xbmcgui.Window(window), mapping


def list_item(record):
    kind = record['type']
    label = 'Spotify' if record['source'] == 'spotify' else 'Plex' if record['source'] == 'plex' else 'Library'
    label += ' · ' + kind.title()
    if record.get('artist'):
        label += ' · ' + join_names(record['artist'])
    item = xbmcgui.ListItem(record['title'], label, path=record.get('path', ''), offscreen=True)
    item.setArt(record.get('art') or {})
    item.setProperty(PREFIX + 'Record', json.dumps(record, ensure_ascii=False, sort_keys=True))
    for key, value in (('Type', kind), ('Source', record['source']), ('Shape', SHAPES[kind])):
        item.setProperty(PREFIX + key, value)
    # Native info dialogs need a database type and ID, even for script lists.
    if kind in VIDEO and record['source'] == 'library':
        tag = item.getVideoInfoTag()
        tag.setTitle(record['title'])
        tag.setMediaType(kind)
        tag.setDbId(positive_id(record['id']))
        tag.setYear(record.get('year', 0))
    return item


def render(rows, window, mapping):
    for name, number in mapping.items():
        control = window.getControl(number)
        control.reset()
        control.addItems([list_item(record) for record in rows[name][:20 if name == 'top' else 100]])


def store(rows, errors, window, mapping):
    win = home()
    limited = {name: records[:20 if name == 'top' else 100] for name, records in rows.items()}
    publish(win, PREFIX + 'Results', json.dumps(limited, ensure_ascii=False, sort_keys=True))
    publish(win, PREFIX + 'Errors', ','.join(sorted(errors)))
    for name, records in rows.items():
        publish(win, PREFIX + 'Count.' + name, len(records))
    render(rows, window, mapping)


def run(args):
    allowed = {'query', 'window', 'control', 'source', 'rows', 'spotify', 'plex'}
    if set(args) - allowed:
        raise ValueError('Unknown search argument.')
    window, mapping = target(args)
    query = args.get('query', home().getProperty(PREFIX + 'Query')).strip()
    if len(query) > 256:
        raise ValueError('Search queries must contain at most 256 characters.')
    for flag in ('spotify', 'plex'):
        if flag in args and args[flag] not in ('true', 'false'):
            raise ValueError('Search provider flags accept true or false.')
    token = uuid.uuid4().hex
    win = home()
    publish(win, PREFIX + 'Request', token)
    publish(win, PREFIX + 'Query', query)
    publish(win, PREFIX + 'Busy', 'true' if words(query) else '')
    current = lambda: (win.getProperty(PREFIX + 'Request') == token
                       and win.getProperty(PREFIX + 'Query') == query)
    try:
        if not words(query):
            store(rank('', []), set(), window, mapping)
            return
        # Keep visible results during rapid input and cancel superseded work.
        if xbmc.Monitor().waitForAbort(.15) or not current():
            return
        records, errors = collect(query, False, False, current)
        if not current():
            return
        store(rank(query, records), errors, window, mapping)
        more, failed = collect_providers(query, args.get('spotify', 'true') == 'true',
                                        args.get('plex', 'false') == 'true', current)
        if current() and (more or failed):
            store(rank(query, records + more), errors | failed, window, mapping)
    finally:
        if current():
            publish(win, PREFIX + 'Busy', '')


def render_saved(args):
    if set(args) - {'window', 'control', 'source', 'rows'}:
        raise ValueError('Unknown render argument.')
    window, mapping = target(args)
    rows = json.loads(home().getProperty(PREFIX + 'Results') or '{}')
    render({name: rows.get(name, []) for name in ROWS}, window, mapping)


def native_info(record):
    kind, ident = record['type'], positive_id(record['id'])
    fields = ['title', 'plot', 'runtime', 'genre', 'cast', 'playcount', 'file', 'art', 'rating']
    fields += (['resume', 'season', 'episode', 'showtitle', 'tvshowid'] if kind == 'episode' else
               ['year', 'episode', 'watchedepisodes'] if kind == 'tvshow' else ['year', 'resume', 'trailer'])
    method = {'movie': 'Movie', 'tvshow': 'TVShow', 'episode': 'Episode'}[kind]
    details = rpc('VideoLibrary.Get' + method + 'Details', **{kind + 'id': ident},
                  properties=fields)[kind + 'details']
    item = list_item(record)
    item.setArt(details.get('art') or {})
    if kind == 'movie':
        path = 'videodb://movies/titles/' + str(ident) + '/'
    elif kind == 'tvshow':
        path = 'videodb://tvshows/titles/' + str(ident) + '/'
    else:
        path = ('videodb://tvshows/titles/' + str(positive_id(details['tvshowid'])) + '/'
                + str(details['season']) + '/' + str(ident) + '/')
    item.setPath(path)
    tag = item.getVideoInfoTag()
    for setter, key in ((tag.setTitle, 'title'), (tag.setPlot, 'plot'),
                        (tag.setDuration, 'runtime'), (tag.setGenres, 'genre'),
                        (tag.setYear, 'year'), (tag.setRating, 'rating'),
                        (tag.setPlaycount, 'playcount'), (tag.setTrailer, 'trailer'),
                        (tag.setTvShowTitle, 'showtitle'), (tag.setSeason, 'season'),
                        (tag.setEpisode, 'episode'), (tag.setFilenameAndPath, 'file')):
        if key in details:
            setter(details[key])
    tag.setCast([xbmc.Actor(name=actor.get('name', ''), role=actor.get('role', ''),
                           order=actor.get('order', -1), thumbnail=actor.get('thumbnail', ''))
                 for actor in details.get('cast', [])])
    resume = details.get('resume') or {}
    if resume:
        tag.setResumePoint(resume.get('position', 0), resume.get('total', 0))
    xbmcgui.Dialog().info(item)


def activate(record):
    kind, source = record['type'], record['source']
    if source in ('spotify', 'plex'):
        safe = provider_record({'file': record['path'], 'title': record['title']}, source, kind)
        path = safe['path']
        if source == 'spotify' and kind == 'song':
            rpc('Player.Open', item={'file': path})
        elif source == 'plex':
            xbmc.executebuiltin('RunPlugin(' + path + ')')
        else:
            target_window = 'Music'
            # Provider validation restricts the host, scheme and action.
            if any(char in path for char in '\r\n"'):
                raise ValueError('Invalid provider path.')
            xbmc.executebuiltin('ActivateWindow(' + target_window + ',"' + path + '",return)')
        return
    if source != 'library':
        raise ValueError('Unknown result source.')
    if kind == 'person':
        # A native actor filter needs no remote metadata dependency.
        rule = {'rules': {'and': [{'field': 'actor', 'operator': 'is', 'value': [record['title']]}]}, 'type': 'movies'}
        path = 'videodb://movies/titles/?xsp=' + quote(json.dumps(rule), safe='')
        xbmc.executebuiltin('ActivateWindow(Videos,"' + path + '",return)')
    elif kind == 'genre':
        media, ident = record['id'].split(':', 1)
        ident = positive_id(ident)
        if media == 'music':
            music_window('musicdb://genres/' + str(ident) + '/')
        elif media in ('movie', 'tvshow'):
            xbmc.executebuiltin('ActivateWindow(Videos,"videodb://' +
                               ('movies' if media == 'movie' else 'tvshows') + '/genres/' + str(ident) + '/",return)')
        else:
            raise ValueError('Unknown genre source.')
    else:
        ident = positive_id(record['id'])
        if kind == 'song':
            rpc('Player.Open', item={'songid': ident})
        elif kind in ('album', 'artist'):
            music_window('musicdb://' + kind + 's/' + str(ident) + '/')
        elif kind == 'set':
            xbmc.executebuiltin('ActivateWindow(Videos,"videodb://movies/sets/' + str(ident) + '/",return)')
        elif kind in VIDEO:
            native_info(record)
        else:
            raise ValueError('Unknown result type.')


def select(args):
    if set(args) - {'window', 'control'}:
        raise ValueError('Unknown selection argument.')
    window, mapping = target(args)
    control = next(iter(mapping.values()))
    raw = xbmc.getInfoLabel(f'Container({control}).ListItem.Property({PREFIX}Record)')
    if not raw:
        return
    record = json.loads(raw)
    activate(record)
    query = home().getProperty(PREFIX + 'Query')
    if query:
        recent = json.loads(home().getProperty(PREFIX + 'Recent') or '[]')
        recent = [query] + [value for value in recent if value != query]
        publish(home(), PREFIX + 'Recent', json.dumps(recent[:10], ensure_ascii=False))
