"""Failure inventory, written before runtime code.

Queue: stale data after stop/seek/queue edits; unknown duration; pause drift;
repeat-one false promises; repeat-all wrap; sparse Spotify tags; stale hooks;
RPC faults; needless near-second queries outside the countdown; shutdown leaks.
Actions: missing current ID; incorrect playlist fallback; route injection;
ambiguous artist/album identity; track changes during lookup; local music routes.
Search: wrong word boundary; unstable ties; watched-first order; source mixing;
missing pagination; unavailable providers; stale requests; invalid playback IDs;
empty query; malformed input; unsupported commands; unbounded control IDs.
"""
import importlib
from pathlib import Path
import sys
import unittest

from fakes import Kodi

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class RuntimeCase(unittest.TestCase):
    def setUp(self):
        self.kodi = Kodi().install()
        for name in list(sys.modules):
            if name.startswith('resources.lib.') or name == 'default':
                del sys.modules[name]

    def module(self, name):
        return importlib.import_module('resources.lib.' + name)

    @property
    def home(self):
        return self.kodi.window(10000)


class QueueTests(RuntimeCase):
    def setUp(self):
        super().setUp()
        self.queue = [
            {'file': 'current.flac', 'title': 'Current'},
            {'file': 'next.flac', 'title': 'Next', 'artist': ['A', 'B'],
             'album': 'Album', 'art': {'thumb': 'cover.jpg'}},
        ]
        self.state = {'playlistid': 0, 'position': 0, 'repeat': 'off', 'speed': 1,
                      'time': {'minutes': 3, 'seconds': 46},
                      'totaltime': {'minutes': 4}}
        self.kodi.responses = {
            'Player.GetActivePlayers': {'result': [{'playerid': 0, 'type': 'audio'}]},
            'Player.GetProperties': lambda p: {'result': dict(self.state)},
            'Playlist.GetItems': self.items,
        }

    def items(self, params):
        start, end = params['limits']['start'], params['limits']['end']
        return {'result': {'items': self.queue[start:end], 'limits': {
            'start': start, 'end': min(end, len(self.queue)), 'total': len(self.queue)}}}

    def service(self):
        return self.module('nexttrack').NextTrackService()

    def test_last_fifteen_seconds_publish_metadata_and_real_countdown(self):
        service = self.service()
        self.assertEqual(service.refresh(), 1)
        expected = {'Title': 'Next', 'Artist': 'A / B', 'Album': 'Album',
                    'Art': 'cover.jpg', 'Remaining': '14', 'Active': 'true',
                    'Progress': '93', 'Available': 'true'}
        for name, value in expected.items():
            self.assertEqual(self.home.getProperty('Starlight.NextTrack.' + name), value)
        self.state['time']['seconds'] = 49
        service.refresh()
        self.assertEqual(self.home.getProperty('Starlight.NextTrack.Remaining'), '11')

    def test_away_from_end_uses_five_seconds_and_hides_pill(self):
        self.state['time'] = {'seconds': 10}
        self.assertEqual(self.service().refresh(), 5)
        self.assertEqual(self.home.getProperty('Starlight.NextTrack.Active'), '')
        self.assertEqual(self.home.getProperty('Starlight.NextTrack.Title'), 'Next')

    def test_schedule_lands_on_fifteen_second_boundary(self):
        self.state['time'] = {'minutes': 3, 'seconds': 44, 'milliseconds': 500}
        self.assertEqual(self.service().refresh(), .5)

    def test_pause_keeps_countdown_and_seek_rearms(self):
        service = self.service()
        service.refresh()
        self.state['speed'] = 0
        service.refresh()
        self.assertEqual(self.home.getProperty('Starlight.NextTrack.Remaining'), '14')
        self.state['time'] = {'minutes': 1}
        service.refresh()
        self.assertEqual(self.home.getProperty('Starlight.NextTrack.Active'), '')
        self.state['time'] = {'minutes': 3, 'seconds': 50}
        service.refresh()
        self.assertEqual(self.home.getProperty('Starlight.NextTrack.Active'), 'true')

    def test_stop_error_unknown_duration_and_wrong_skin_clear_properties(self):
        service = self.service()
        service.refresh()
        self.state['totaltime'] = {}
        service.refresh()
        self.assertEqual(self.home.getProperty('Starlight.NextTrack.Active'), '')
        self.state['totaltime'] = {'minutes': 4}
        service.refresh()
        self.kodi.responses['Player.GetActivePlayers'] = {'error': {'code': -1}}
        service.refresh()
        self.assertFalse(any(key.startswith('Starlight.NextTrack.') for key in self.home.props))
        self.kodi.skin = 'skin.other'
        self.assertEqual(service.refresh(), 5)
        self.assertFalse(self.home.props)

    def test_repeat_one_and_last_track_clear_stale_hooks(self):
        service = self.service()
        service.refresh()
        self.state['repeat'] = 'one'
        service.refresh()
        self.assertEqual(self.home.getProperty('Starlight.NextTrack.Title'), '')
        self.state.update(repeat='off', position=1)
        self.home.setProperty('Spotify.NextTrackTitle', 'Stale')
        self.home.setProperty('Spotify.CurrentTrackId', 'current')
        service.refresh()
        self.assertEqual(self.home.getProperty('Starlight.NextTrack.Title'), '')

    def test_repeat_all_wraps_and_queue_edits_replace_metadata(self):
        self.state.update(repeat='all', position=1)
        service = self.service()
        service.refresh()
        self.assertEqual(self.home.getProperty('Starlight.NextTrack.Title'), 'Current')
        self.state['position'] = 0
        self.queue[1]['title'] = 'Replacement'
        service.refresh()
        self.assertEqual(self.home.getProperty('Starlight.NextTrack.Title'), 'Replacement')

    def test_sparse_spotify_queue_uses_only_matching_hooks(self):
        self.queue[1] = {'file': 'http://127.0.0.1:8099/track/nextid/200000ms.xwav'}
        for name, value in {'CurrentTrackId': 'currentid', 'NextTrackFile': self.queue[1]['file'],
                            'NextTrackTitle': 'Hook title', 'NextTrackArtist': 'Hook artist',
                            'NextTrackThumb': 'hook.jpg'}.items():
            self.home.setProperty('Spotify.' + name, value)
        service = self.service()
        service.refresh()
        self.assertEqual(self.home.getProperty('Starlight.NextTrack.Title'), 'Hook title')
        self.home.setProperty('Spotify.NextTrackFile', 'different')
        service.refresh()
        self.assertEqual(self.home.getProperty('Starlight.NextTrack.Title'), '')

    def test_offset_fallback_and_shutdown_cleanup(self):
        self.kodi.responses['Player.GetProperties'] = {'error': {'code': -1}}
        self.kodi.labels.update({'MusicPlayer.offset(1).Title': 'Offset',
                                 'Player.TimeRemaining(ss)': '12'})
        service = self.service()
        service.refresh()
        self.assertEqual(self.home.getProperty('Starlight.NextTrack.Title'), 'Offset')
        service.clear()
        self.assertFalse(self.home.props)


class ActionTests(RuntimeCase):
    def setUp(self):
        super().setUp()
        self.kodi.conditions.add('System.HasAddon(plugin.audio.spotifykodiconnect)')
        self.kodi.conditions.add('Player.HasAudio')
        self.track = '0123456789ABCDEFGHIJKL'
        self.home.setProperty('Spotify.CurrentTrackId', self.track)

    def test_playlist_receives_current_track_explicitly(self):
        self.module('actions').run('spotify.playlist', {})
        self.assertEqual(self.kodi.builtins, [
            'RunPlugin(plugin://plugin.audio.spotifykodiconnect/?action=add_track_to_playlist&trackid=0123456789ABCDEFGHIJKL)'])

    def test_like_and_radio_routes(self):
        actions = self.module('actions')
        actions.run('spotify.like', {})
        actions.run('spotify.radio', {})
        self.assertEqual(self.kodi.builtins, [
            'NotifyAll(plugin.audio.spotifykodiconnect,Other.ToggleLike)',
            'ActivateWindow(Music,"plugin://plugin.audio.spotifykodiconnect/?action=browse_radio&trackid=0123456789ABCDEFGHIJKL",return)'])

    def test_artist_album_and_follow_accept_valid_ids(self):
        actions = self.module('actions')
        actions.run('music.artist', {'artistid': self.track})
        actions.run('music.album', {'albumid': self.track})
        actions.run('spotify.follow', {'artistid': self.track})
        self.assertEqual(self.kodi.builtins, [
            'ActivateWindow(Music,"plugin://plugin.audio.spotifykodiconnect/?action=browse_artist_everything&artistid=0123456789ABCDEFGHIJKL",return)',
            'ActivateWindow(Music,"plugin://plugin.audio.spotifykodiconnect/?action=browse_album&albumid=0123456789ABCDEFGHIJKL",return)',
            'RunPlugin(plugin://plugin.audio.spotifykodiconnect/?action=follow_artist&artistid=0123456789ABCDEFGHIJKL)'])

    def test_missing_or_malicious_id_never_executes(self):
        actions = self.module('actions')
        self.home.clearProperty('Spotify.CurrentTrackId')
        with self.assertRaises(ValueError):
            actions.run('spotify.playlist', {})
        self.home.setProperty('Spotify.CurrentTrackId', self.track)
        with self.assertRaises(ValueError):
            actions.run('spotify.follow', {'artistid': 'x),Quit()'})
        self.assertFalse(self.kodi.builtins)

    def test_artist_lookup_uses_exact_name_and_trusted_route(self):
        self.kodi.labels['MusicPlayer.Artist'] = 'Tycho'
        self.kodi.responses['Files.GetDirectory'] = {'result': {'files': [
            {'label': 'Tycho tribute', 'file': 'plugin://plugin.audio.spotifykodiconnect/?action=browse_artist_everything&artistid=1111111111111111111111'},
            {'label': 'Tycho', 'file': 'plugin://plugin.audio.spotifykodiconnect/?action=browse_artist_everything&artistid=2222222222222222222222'},
        ]}}
        self.module('actions').run('spotify.follow', {})
        self.assertEqual(self.kodi.builtins, ['RunPlugin(plugin://plugin.audio.spotifykodiconnect/?action=follow_artist&artistid=2222222222222222222222)'])

    def test_lookup_track_change_cancels_follow(self):
        self.kodi.labels['MusicPlayer.Artist'] = 'Tycho'
        def respond(params):
            self.home.setProperty('Spotify.CurrentTrackId', '3333333333333333333333')
            return {'result': {'files': [{'label': 'Tycho', 'file': 'plugin://plugin.audio.spotifykodiconnect/?action=browse_artist_everything&artistid=2222222222222222222222'}]}}
        self.kodi.responses['Files.GetDirectory'] = respond
        with self.assertRaises(ValueError):
            self.module('actions').run('spotify.follow', {})
        self.assertFalse(self.kodi.builtins)

    def test_local_artist_album_use_database_ids(self):
        self.home.clearProperty('Spotify.CurrentTrackId')
        self.kodi.labels['MusicPlayer.DBID'] = '7'
        self.kodi.responses['AudioLibrary.GetSongDetails'] = {'result': {'songdetails': {'artistid': [8], 'albumid': 9}}}
        actions = self.module('actions')
        actions.run('music.artist', {})
        actions.run('music.album', {})
        self.assertEqual(self.kodi.builtins, [
            'ActivateWindow(Music,"musicdb://artists/8/",return)',
            'ActivateWindow(Music,"musicdb://albums/9/",return)'])


class SearchTests(RuntimeCase):
    def record(self, title, kind='movie', source='library', ident=1, **extra):
        return {'title': title, 'type': kind, 'source': source, 'id': str(ident), **extra}

    def test_word_start_ranking_status_and_separate_plex_row(self):
        search = self.module('search')
        records = [self.record('Dune', ident=3, playcount=2),
                   self.record('Dune Messiah', ident=2),
                   self.record('Dune Part Two', ident=1, resume={'position': 10}),
                   self.record('Dune actor', kind='person', ident=4),
                   self.record('Dune collection', kind='set', ident=5),
                   self.record('Redune', ident=6),
                   self.record('Dune online', source='plex', ident=7),
                   self.record('Dune song', kind='song', source='spotify', ident=8)]
        rows = search.rank('dun', records)
        self.assertEqual([r['id'] for r in rows['top']], ['1', '2', '8', '3', '4', '5'])
        self.assertEqual([r['id'] for r in rows['plex']], ['7'])
        self.assertEqual([r['id'] for r in rows['movies']], ['1', '2', '3'])

    def test_unicode_word_boundaries_and_deterministic_ties(self):
        search = self.module('search')
        records = [self.record('Étoile: Lune', ident=2), self.record('Étoile Lune', ident=1)]
        for values in (records, list(reversed(records))):
            self.assertEqual([r['id'] for r in search.rank('eto lu', values)['top']], ['1', '2'])
        self.assertFalse(search.rank('toile', records)['top'])
        self.assertFalse(search.rank('', records)['top'])

    def test_pagination_and_failure_preserve_other_local_sources(self):
        self.kodi.responses['VideoLibrary.GetMovies'] = lambda p: {'result': {'movies': [
            {'movieid': p['limits']['start'] + 1, 'title': 'Dune', 'resume': {'position': 0}, 'playcount': 0}],
            'limits': {'total': 2, 'start': p['limits']['start'], 'end': p['limits']['start'] + 1}}}
        self.kodi.responses['AudioLibrary.GetSongs'] = {'error': {'code': -1}}
        records, errors = self.module('search').collect('dune', False, False)
        self.assertEqual([r['id'] for r in records if r['type'] == 'movie'], ['1', '2'])
        self.assertIn('music', errors)
        self.assertFalse(any(c['method'] == 'Files.GetDirectory' for c in self.kodi.calls))

    def test_script_populates_controls_and_empty_query_clears(self):
        search = self.module('search')
        self.kodi.responses['VideoLibrary.GetMovies'] = {'result': {'movies': [
            {'movieid': 5, 'title': 'Dune', 'playcount': 0}], 'limits': {'total': 1, 'end': 1}}}
        search.run({'query': 'dune', 'window': '11101', 'control': '510', 'source': 'movies'})
        control = self.kodi.window(11101).getControl(510)
        self.assertEqual([row.label for row in control.items], ['Dune'])
        self.assertEqual(control.items[0].getProperty('Starlight.Search.Shape'), 'poster')
        self.assertEqual(self.home.getProperty('Starlight.Search.Count.movies'), '1')
        search.run({'query': '', 'window': '11101', 'control': '510'})
        self.assertFalse(control.items)
        self.assertEqual(self.home.getProperty('Starlight.Search.Busy'), '')

    def test_newer_request_prevents_stale_results(self):
        search = self.module('search')
        def respond(params):
            self.home.setProperty('Starlight.Search.Request', 'newer')
            return {'result': {'movies': [{'movieid': 1, 'title': 'Old'}]}}
        self.kodi.responses['VideoLibrary.GetMovies'] = respond
        search.run({'query': 'old', 'window': '11101', 'control': '510'})
        self.assertFalse(self.kodi.window(11101).getControl(510).items)
        self.assertEqual(self.home.getProperty('Starlight.Search.Request'), 'newer')

    def test_invalid_window_control_and_source_rejected_before_queries(self):
        search = self.module('search')
        for args in ({'window': '-1'}, {'control': '0'}, {'source': 'evil'}):
            with self.assertRaises(ValueError):
                search.run({'query': 'dune', **args})
        self.assertFalse(self.kodi.calls)

    def test_invalid_library_id_cannot_play(self):
        with self.assertRaises(ValueError):
            self.module('search').activate(self.record('Bad', kind='song', ident='-1'))
        self.assertFalse(self.kodi.builtins)
        self.assertFalse(self.kodi.calls)


class ProviderContractTests(RuntimeCase):
    def test_real_spotify_artist_directory_route_resolves(self):
        self.kodi.conditions.update({'Player.HasAudio', 'System.HasAddon(plugin.audio.spotifykodiconnect)'})
        self.home.setProperty('Spotify.CurrentTrackId', '0123456789ABCDEFGHIJKL')
        self.kodi.labels['MusicPlayer.Artist'] = 'Tycho'
        self.kodi.responses['Files.GetDirectory'] = {'result': {'files': [{
            'label': 'Tycho', 'file': 'plugin://plugin.audio.spotifykodiconnect/?action=browse_artist_just_albums_and_singles&artistid=2222222222222222222222'}]}}
        self.module('actions').run('spotify.follow', {})
        self.assertEqual(self.kodi.builtins, ['RunPlugin(plugin://plugin.audio.spotifykodiconnect/?action=follow_artist&artistid=2222222222222222222222)'])

    def test_album_name_duplicates_require_user_selection(self):
        self.kodi.conditions.update({'Player.HasAudio', 'System.HasAddon(plugin.audio.spotifykodiconnect)'})
        self.home.setProperty('Spotify.CurrentTrackId', '0123456789ABCDEFGHIJKL')
        self.kodi.labels['MusicPlayer.Album'] = 'Awake'
        self.kodi.responses['Files.GetDirectory'] = {'result': {'files': [
            {'label': 'Awake', 'artist': ['Tycho'], 'file': 'plugin://plugin.audio.spotifykodiconnect/?action=browse_album&albumid=1111111111111111111111'},
            {'label': 'Awake', 'artist': ['Other'], 'file': 'plugin://plugin.audio.spotifykodiconnect/?action=browse_album&albumid=2222222222222222222222'}]}}
        self.module('actions').run('music.album', {})
        self.assertFalse(self.kodi.builtins)
        self.kodi.selection = 0
        self.module('actions').run('music.album', {})
        self.assertEqual(self.kodi.builtins, ['ActivateWindow(Music,"plugin://plugin.audio.spotifykodiconnect/?action=browse_album&albumid=1111111111111111111111",return)'])

    def test_plex_contract_search_and_item_action_stay_separate(self):
        self.kodi.conditions.add('System.HasAddon(plugin.video.plexkodiconnect)')
        def response(params):
            self.assertEqual(params['directory'], 'plugin://plugin.video.plexkodiconnect/starlight/search?query=dune')
            return {'result': {'files': [{'label': 'Dune', 'type': 'movie',
                'file': 'plugin://plugin.video.plexkodiconnect/starlight/item?rating_key=5d776824999c64001ec2d059'}]}}
        self.kodi.responses['Files.GetDirectory'] = response
        search = self.module('search')
        records, errors = search.collect_providers('dune', False, True, lambda: True)
        self.assertFalse(errors)
        rows = search.rank('dune', records)
        self.assertFalse(rows['top'])
        self.assertEqual(len(rows['plex']), 1)
        search.activate(rows['plex'][0])
        self.assertEqual(self.kodi.builtins, ['RunPlugin(plugin://plugin.video.plexkodiconnect/starlight/item?rating_key=5d776824999c64001ec2d059)'])

    def test_spotify_search_routes_and_artist_record(self):
        self.kodi.conditions.add('System.HasAddon(plugin.audio.spotifykodiconnect)')
        def response(params):
            directory = params['directory']
            if 'search_artists' in directory:
                return {'result': {'files': [{'label': 'Tycho', 'file': 'plugin://plugin.audio.spotifykodiconnect/?action=browse_artist_just_albums_and_singles&artistid=2222222222222222222222'}]}}
            return {'result': {'files': []}}
        self.kodi.responses['Files.GetDirectory'] = response
        records, errors = self.module('search').collect_providers('Tycho', True, False, lambda: True)
        self.assertFalse(errors)
        self.assertEqual(records[0]['id'], '2222222222222222222222')
        directories = [call['params']['directory'] for call in self.kodi.calls]
        self.assertIn('plugin://plugin.audio.spotifykodiconnect/?action=search_tracks&trackid=Tycho', directories)

    def test_provider_rejects_credentials_and_foreign_hosts(self):
        search = self.module('search')
        paths = ['plugin://evil/starlight/item?rating_key=abc',
                 'plugin://plugin.video.plexkodiconnect/starlight/item?rating_key=abc&X-Plex-Token=secret',
                 'plugin://plugin.video.plexkodiconnect/starlight/../../item?rating_key=abc']
        for path in paths:
            with self.assertRaises(ValueError):
                search.provider_record({'file': path, 'label': 'Dune'}, 'plex', 'movie')

    def test_multi_row_render_and_selection_use_real_ranked_records(self):
        search = self.module('search')
        self.kodi.responses['AudioLibrary.GetSongs'] = {'result': {'songs': [
            {'songid': 5, 'title': 'Dune song', 'artist': ['Artist']}], 'limits': {'end': 1, 'total': 1}}}
        search.run({'query': 'dune', 'rows': 'top:510|music:511'})
        self.assertEqual(self.kodi.window(11101).getControl(510).items[0].label, 'Dune song')
        self.kodi.responses['Player.Open'] = {'result': 'OK'}
        search.select({'window': '11101', 'control': '511'})
        self.assertEqual(self.kodi.calls[-1]['method'], 'Player.Open')
        self.assertEqual(self.kodi.calls[-1]['params'], {'item': {'songid': 5}})
        self.assertEqual(self.home.getProperty('Starlight.Search.Recent'), '["dune"]')


class BoundaryTests(RuntimeCase):
    def test_command_specific_arguments_are_rejected(self):
        self.kodi.conditions.update({'Player.HasAudio', 'System.HasAddon(plugin.audio.spotifykodiconnect)'})
        self.home.setProperty('Spotify.CurrentTrackId', '0123456789ABCDEFGHIJKL')
        actions = self.module('actions')
        for command, args in [('spotify.like', {'artistid': '0123456789ABCDEFGHIJKL'}),
                              ('music.album', {'artistid': '0123456789ABCDEFGHIJKL'})]:
            with self.assertRaises(ValueError):
                actions.run(command, args)
        self.assertFalse(self.kodi.builtins)

    def test_existing_results_remain_visible_until_new_native_results_arrive(self):
        search = self.module('search')
        item = search.list_item({'id': '8', 'type': 'song', 'source': 'library', 'title': 'Old result'})
        self.kodi.window(11101).getControl(510).addItems([item])
        def respond(params):
            self.assertEqual(self.kodi.window(11101).getControl(510).items[0].label, 'Old result')
            return {'result': {'movies': [], 'limits': {'end': 0, 'total': 0}}}
        self.kodi.responses['VideoLibrary.GetMovies'] = respond
        search.run({'query': 'new'})
        self.assertFalse(self.kodi.window(11101).getControl(510).items)

    def test_query_property_change_rejects_old_publication(self):
        search = self.module('search')
        def respond(params):
            self.home.setProperty('Starlight.Search.Query', 'newer text')
            return {'result': {'movies': [{'movieid': 1, 'title': 'Old'}]}}
        self.kodi.responses['VideoLibrary.GetMovies'] = respond
        search.run({'query': 'old'})
        self.assertFalse(self.kodi.window(11101).getControl(510).items)
        self.assertEqual(self.home.getProperty('Starlight.Search.Query'), 'newer text')

    def test_spotify_song_route_requires_valid_queue_fields(self):
        search = self.module('search')
        for tail in ('', '&ms=bad&position=0&list=x', '&ms=1000&position=-1&list=x'):
            with self.assertRaises(ValueError):
                search.provider_record({'label': 'Song', 'file': 'plugin://plugin.audio.spotifykodiconnect/?action=play_in_queue&track=0123456789ABCDEFGHIJKL' + tail}, 'spotify', 'song')


class SpotifyPageTests(RuntimeCase):
    def test_next_page_is_consumed_automatically_and_never_becomes_a_card(self):
        self.kodi.conditions.add('System.HasAddon(plugin.audio.spotifykodiconnect)')
        def response(params):
            path = params['directory']
            if 'search_artists' not in path:
                return {'result': {'files': []}}
            if 'offset=50' in path:
                return {'result': {'files': [{'title': 'Tycho', 'file': 'plugin://plugin.audio.spotifykodiconnect/?action=browse_artist_just_albums_and_singles&artistid=2222222222222222222222'}]}}
            return {'result': {'files': [
                {'title': 'Tycho tribute', 'file': 'plugin://plugin.audio.spotifykodiconnect/?action=browse_artist_just_albums_and_singles&artistid=1111111111111111111111'},
                {'label': 'Next page', 'file': 'plugin://plugin.audio.spotifykodiconnect/?action=search_artists&artistid=Tycho&offset=50&limit=50'}]}}
        self.kodi.responses['Files.GetDirectory'] = response
        records, errors = self.module('search').collect_providers('Tycho', True, False, lambda: True)
        self.assertFalse(errors)
        self.assertEqual([record['id'] for record in records], ['1111111111111111111111', '2222222222222222222222'])
        self.assertTrue(any('offset=50' in call['params']['directory'] for call in self.kodi.calls))


class MultiArtistTests(RuntimeCase):
    def test_collaboration_artist_choice_resolves_one_identity(self):
        self.kodi.conditions.update({'Player.HasAudio', 'System.HasAddon(plugin.audio.spotifykodiconnect)'})
        self.home.setProperty('Spotify.CurrentTrackId', '0123456789ABCDEFGHIJKL')
        self.kodi.labels['MusicPlayer.Artist'] = 'Tycho / Bonobo'
        self.kodi.selection = 0
        def response(params):
            self.assertEqual(params['directory'], 'plugin://plugin.audio.spotifykodiconnect/?action=search_artists&artistid=Tycho')
            return {'result': {'files': [{'title': 'Tycho', 'file': 'plugin://plugin.audio.spotifykodiconnect/?action=browse_artist_just_albums_and_singles&artistid=2222222222222222222222'}]}}
        self.kodi.responses['Files.GetDirectory'] = response
        self.module('actions').run('spotify.follow', {})
        self.assertEqual(self.kodi.builtins, ['RunPlugin(plugin://plugin.audio.spotifykodiconnect/?action=follow_artist&artistid=2222222222222222222222)'])


class NativeInfoTests(RuntimeCase):
    def test_native_info_receives_full_metadata_and_playback_identity(self):
        self.kodi.responses['VideoLibrary.GetMovieDetails'] = {'result': {'moviedetails': {
            'movieid': 7, 'title': 'Dune', 'file': '/library/dune.mkv', 'plot': 'A desert world.',
            'year': 2021, 'runtime': 9300, 'playcount': 0, 'genre': ['Science Fiction'],
            'art': {'poster': 'poster.jpg'}, 'cast': [{'name': 'Actor', 'role': 'Paul', 'order': 0}],
            'resume': {'position': 120, 'total': 9300}}}}
        search = self.module('search')
        search.activate({'id': '7', 'type': 'movie', 'source': 'library', 'title': 'Dune'})
        self.assertEqual(self.kodi.calls[-1]['method'], 'VideoLibrary.GetMovieDetails')
        item = self.kodi.infos[0]
        self.assertEqual(item.info['dbid'], 7)
        self.assertEqual(item.info['plot'], 'A desert world.')
        self.assertEqual(item.info['filenameandpath'], '/library/dune.mkv')
        self.assertEqual(item.info['resume'], (120, 9300))
        self.assertEqual(item.art['poster'], 'poster.jpg')
        self.assertEqual(item.info['cast'][0].name, 'Actor')


class EntryTests(RuntimeCase):
    def test_unknown_command_and_duplicate_arguments_fail(self):
        default = importlib.import_module('default')
        with self.assertRaises(ValueError):
            default.dispatch(['unknown'])
        with self.assertRaises(ValueError):
            default.dispatch(['search', 'query=a', 'query=b'])
        self.assertFalse(self.kodi.builtins)
