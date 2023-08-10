import blessed
import mpd
from mpd import MPDClient
import pixcat
from pixcat.terminal import TERM

import array
import fcntl
import sys
import termios
import os
from functools import partial
import bisect

echo = partial(print, end='', flush=True)
debug = open('debug', 'w')


client = MPDClient()
client.connect('localhost', 6600)


def to_timestamp(seconds):
    seconds = int(seconds)
    if seconds // 3600 != 0:
        return f'{seconds//3600:02}:' + f'{seconds//3600//60:02}:{seconds%60:02}'
    return f'{seconds//60}:{seconds%60:02}'


class Lict():
    def __init__(self, d, l=None, sorted=False):
        self.dict = d
        self.list = l or list(self.dict)
        self.sorted = sorted
        if self.sorted:
            self.list.sort()

    def __getitem__(self, key):
        if isinstance(key, int):
            return self.dict[self.list[key]]
        else:
            return self.dict[key]

    def delete(self, key):
        if isinstance(key, int):
            value = self.list[key]
            del self.list[key]
            if value not in self.list: # no duplicates
                del self.dict[value]
        else:
            del self.list[self.list.index(key)]
            del self.dict[key]

    def update(self, other):
        if isinstance(other, Lict):
            self.dict.update(other.dict)
            self.list.extend(other.list)
        elif isinstance(other, dict):
            self.dict.update(other)
            self.list.extend(list(other))
        else:
            raise TypeError(f'Can\'t update with type {type(other)}')

    def insert(self, key, value):
        if self.sorted:
            bisect.insort(self.list, key)
        else:
            self.list.append(key)
        self.dict[key] = value

    def export(self):
        self.dict = {i: self.dict[i] for i in self.list}
        return self.dict

    def __len__(self):
        return len(self.list)

    def items(self):
        return self.dict.items()

    def __contains__(self, key):
        return key in self.dict


class Client(MPDClient):
    def __init__(self, port):
        super().__init__()
        self.port = port
        self.connect('localhost', self.port)
        self.timeout = 1
        self.state = {}

    def handle_timeout(func):
        def timeout_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except mpd.base.ConnectionError:
                client.reconnect()
                return timeout_wrapper(*args, **kwargs)
        return timeout_wrapper

    def reconnect(self):
        self.connect('localhost', self.port)

    @handle_timeout
    def get_all_playlists(self):
        p = self.listplaylists()
        playlists = {}
        for playlist in p:
            playlist_name = playlist['playlist']
            last_modified = playlist['last-modified']
            playlists[playlist_name] = {'name': playlist_name}
        return Lict(playlists)

    @handle_timeout
    def get_playlist(self, playlist_name):
        s = self.listplaylistinfo(playlist_name)
        songs = {}
        slist = []
        for song in s:
            songs[song['file']] = song
            slist.append(song['file'])
        return Lict(songs, slist)

    @handle_timeout
    def search_songs(self, search):
        s = self.search('any', search)
        songs = {}
        for song in s:
            songs[song['file']] = song
        return Lict(songs)

    @handle_timeout
    def get_queue(self):
        s = self.playlistinfo()
        songs = {}
        slist = []
        for song in s:
            songs[song['id']] = song
            slist.append(song['id'])
        return Lict(songs, slist)

    @handle_timeout
    def get_status(self):
        self.state = self.status()
        return self.state

    @handle_timeout
    def get_playing(self):
        return self.currentsong()

    @handle_timeout
    def delete_from_playlist(self, playlist, song):
        self.playlistdelete(playlist, song)

    @handle_timeout
    def add_to_playlist(self, playlist, song):
        self.playlistadd(playlist, song)

    @handle_timeout
    def delete_playlist(self, playlist):
        self.rm(playlist)

    @handle_timeout
    def create_playlist(self, playlist):
        self.save(playlist)
        self.playlistclear(playlist)

    @handle_timeout
    def clear_playlist(self, playlist):
        self.playlistclear(playlist)

    @handle_timeout
    def play_from_queue(self, id):
        self.playid(id)

    @handle_timeout
    def toggle_pause(self):
        self.pause()

    @handle_timeout
    def skip(self):
        self.next()

    @handle_timeout
    def queue_playlist(self, playlist):
        self.load(playlist)

    @handle_timeout
    def queue_song(self, song):
        self.add(song)

    @handle_timeout
    def dequeue(self, index):
        self.delete(index)

    @handle_timeout
    def clear_queue(self):
        self.clear()

    @handle_timeout
    def get_album_art(self, song):
        return self.albumart(song)

    @handle_timeout
    def toggle_repeat(self):
        if self.state.get('repeat') == '0':
            self.repeat(1)
        else:
            self.repeat(0)

    @handle_timeout
    def toggle_random(self):
        if self.state.get('random') == '0':
            self.random(1)
        else:
            self.random(0)


class Widget():
    def __init__(self, position, size, bordered=True):
        self._position = position
        self._size = size
        self.bordered = bordered

        self.focused = False

        self.parent = None
        self.children = []

        self.hide = False

    def redraw(self):
        self.display()
        return False

    def update(self):
        pass

    def focus(self):
        self.focused = True
        if self.bordered:
            self.display_shell()
        self.display()
        self.update()

    def defocus(self):
        self.focused = False
        if self.bordered:
            self.display_shell()
        self.display()
        self.update()

    def interpret(self, v):
        w, h = v.split(';')
        if '-' in w:
            w = w.split('-')
            width = (float(w[0]), -int(w[1]))
        elif '+' in w:
            w = w.split('+')
            width = (float(w[0]), int(w[1]))
        if '-' in h:
            h = h.split('-')
            height = (float(h[0]), -int(h[1]))
        elif '+' in h:
            h = h.split('+')
            height = (float(h[0]), int(h[1]))
        return(width, height)
        
    def scale(self, values):
        # format: %width+width;%height+height
        # horribly messy but it works
        wi, he = values.split(';')
        if '-' in he:
            h = he.split('-')
        elif '+' in he:
            h = he.split('+')
        if '-' in wi:
            w = wi.split('-')
        elif '+' in wi:
            w = wi.split('+')
        if float(h[0]) == 1.0:
            h0 = term.height
            if term.height%2 == 1:
                h0 -= 1
            # very annoying to deal with even/odds, but most widgets here only deal with half of the screen. should fix this
        else:
            h0 = float(h[0])*term.height
        if float(w[0]) == 1.0:
            w0 = term.width
            if term.width%2 == 1:
                w0 -= 1
        else:
            w0 = float(w[0])*term.width
        # h0 = term.height*float(h[0])
        # w0 = term.width*float(w[0])
        if '-' in he:
            height = h0 - int(h[1])
        elif '+' in he:
            height = h0 + int(h[1])
        if '-' in wi:
            width = w0 - int(w[1])
        elif '+' in wi:
            width = w0 + int(w[1])
        return (int(width), int(height))

    def scaled_dimensions(self):
        # return (tuple(map(lambda x: x+1, self.scale(self._position))), tuple(map(lambda x: x-2, self.scale(self._size))))
        if self.bordered:
            return (tuple(map(lambda x: x+1, self.scale(self._position))), tuple(map(lambda x: x-2, self.scale(self._size))))
        else:
            return (self.scale(self._position), self.scale(self._size))

    def display_shell(self):
        if self.hide:
            return
        position, size = self.scaled_dimensions()
        position = (position[0] - 1, position[1] - 1)
        size = (size[0] + 2, size[1] + 2)
        echo(term.normal)
        if not self.focused:
            echo(term.white)
        with term.location(*position):
            echo('┏' + '━'*(size[0]-2) + '┓' + term.move_down(1) + term.move_x(position[0]))
            for i in range(size[1]-2):
                echo('┃' + term.move_right(size[0]-2) + '┃' + term.move_down(1) + term.move_x(position[0]))
            echo('┗' + '━'*(size[0]-2) + '┛')
        echo(term.normal)

    def add_child(self, child):
        self.children.append(child)
        child.parent = self
        return child

    def handle_input(self, inp):
        if inp.is_sequence:
            pass
        else:
            if inp == '`':
                if self.parent == None:
                    return True
                term.focus(self.parent)
                return False
        return None

    def couple(self, other):
        self.pair = other
        other.pair = self


class Selection(Widget):
    def __init__(self, lict=None, position='0.0+0;0.0+1', size='1.0+0;1.0-2', bordered=True):
        super().__init__(position, size, bordered)

        self.current = 0
        self.scroll = 0

        self.lict = lict
        self.update()
        # self.filtered = self.lict.list[:]
        # self.filter = ''

        self.selected = []

        self.formats = [
            [('{title}', 'red', 'l', 0.5), ('{artist}', 'blue', 'r', 0.5)],
            [('{title}', 'red_on_white', 'l', 0.5), ('{artist}', 'blue_on_white', 'r', 0.5)]
        ]


    def update(self):
        pass

    @property
    def lict(self):
        return self._lict

    @lict.setter
    def lict(self, value):
        self._lict = value
        self.current = self.current

    @property
    def current(self):
        return self._current

    @current.setter
    def current(self, value):
        self._current = value
        try:
            if self._current >= len(self.lict):
                self._current = len(self.lict)-1
            elif self._current < 0:
                self._current = 0
        except:
            pass
        try:
            _, size = self.scaled_dimensions()
            if self.scroll + size[1] <= self._current:
                self.scroll = self._current - size[1] + 1
            elif self.current < self.scroll:
                self.scroll = self._current
            if self.scroll < 0 and self.current >= 0:
                self.scroll = self._current
        except:
            pass

    # @property
    # def filter(self):
    #     return self._filter
    #
    # @filter.setter
    # def filter(self, value):
    #     self._filter = value
    #     if value == '':
    #         self.filtered = self.lict.list[:]
    #         return
    #     tagged = {}
    #     wild = ''
    #     for i in value.split(';'):
    #         if ':' in i:
    #             a, b = i.split(':')
    #             tagged[a.lower()] = b.lower()
    #         else:
    #             wild = i
    #     self.filtered = []
    #     for i, j in self.lict.items():
    #         if self.match(j.attributes, tagged, wild):
    #             self.filtered.append(i)
    #     self.current = 0
    #     self.scroll = 0

    def select(self):
        if self.current != -1 and len(self.lict) > 0:
            self.selected.append(self.current)

    def match(self, thing, tagged, wild):
        for i, j in tagged.items():
            if j not in thing[i].lower():
                return False
        if wild != '':
            wild_match = False
            for i, j in thing.items():
                if wild in j.lower():
                    wild_match = True
            return True and wild_match
        return True

    def next(self):
        try:
            self.current = (self.current + 1) % len(self.lict.list)
        except ZeroDivisionError:
            pass

    def prev(self):
        try:
            self.current = (self.current - 1) % len(self.lict.list)
        except ZeroDivisionError:
            pass

    def display(self):
        if self.hide:
            return
        self.update()
        position, size = self.scaled_dimensions()
        with term.location(*position):
            i = 0
            index = 0
            self.current = self.current
            for index in range(self.scroll, min(len(self.lict), self.scroll+size[1])):
                try:
                    thing = self.lict[index]
                except IndexError:
                    break
                playing = (thing.get('id') == term.current_song) and (thing.get('id') is not None)
                echo(term.ljust(term.draw(thing, size[0], self.formats, self.current == index, index in self.selected, playing), size[0]))
                echo(term.move_down(1) + term.move_x(position[0]))
                i += 1
            for j in range(i, size[1]):
                echo(' '*size[0])
                echo(term.move_down(1) + term.move_x(position[0]))

    def handle_input(self, inp):
        if inp.is_sequence:
            if inp.name == 'KEY_DOWN':
                self.next()
                return self.redraw()
            elif inp.name == 'KEY_UP':
                self.prev()
                return self.redraw()
            elif inp.name == 'KEY_SDOWN':
                self.select()
                self.next()
                return self.redraw()
        else:
            if inp == '`':
                if self.selected != []:
                    self.selected = []
                    return self.redraw()
        return super().handle_input(inp)


class PlaylistSelection(Selection):
    def __init__(self, lict=None, position='0.0+0;0.0+0', size='0.5+0;1.0-2'):
        super().__init__(lict, position, size)
        self.formats = [
            [('{name}', 'red', 'l', 1.0)],
            [('{name}', 'bold_red', 'l', 1.0)]
        ]
        self.pes = self.add_child(PlaylistEditorSelection(Lict({})))
        if len(self.lict.list) > 0:
            term.current_playlist = self.lict.list[0]
        self.pes.update()

    def focus(self):
        self.pes._position = '0.5+0;0.0+0'
        super().focus()
        term.widgets = [self, self.pes]
        self.pes.display()
        self.pes.display_shell()

    def next(self):
        super().next()
        if len(self.lict.list) > 0:
            term.current_playlist = self.lict.list[self.current]
            self.pes.current = 0
        self.pes.display()

    def prev(self):
        super().prev()
        if len(self.lict.list) > 0:
            term.current_playlist = self.lict.list[self.current]
            self.pes.current = 0
        self.pes.display()

    def update(self):
        self.lict = client.get_all_playlists()

    def handle_input(self, inp):
        if inp.is_sequence:
            if inp.name == 'KEY_SDOWN':
                return None
            elif inp.name == 'KEY_RIGHT':
                if len(self.lict.list) == 0:
                    return None
                self.pes._position = '0.0+0;0.0+0'
                # term.focus(self.pes)
                self.pes.focus()
                term.current_widget = self.pes
                self.pes.field.display()
                self.pes.field.display_shell()
                self.pes.song_selection.display()
                self.pes.song_selection.display_shell()
                return False
            elif inp.name == 'KEY_DELETE':
                d = self.add_child(Dialogue(
                    f'Delete playlist {term.red}[{term.current_playlist}]{term.normal} from the library?',
                    options=['yes', 'no'], options_selected=1, callbacks=[self.delete_playlist, None]
                ))
                term.widgets.append(d)
                term.focus(d)
                return False
        else:
            if inp in ('a', 'A'):
                d = self.add_child(CreatePlaylistDialogue())
                term.widgets.append(d)
                term.focus(d)
                return False
        return super().handle_input(inp)

    def delete_playlist(self):
        client.delete_playlist(term.current_playlist)
        self.update()
        if len(self.lict.list) > 0:
            term.current_playlist = self.lict.list[self.current]
            self.pes.current = 0
        # self.pes.display()


class PlaylistEditorSelection(Selection):
    def __init__(self, lict=None, position='0.5+0;0.0+0', size='0.5+0;1.0-2'):
        super().__init__(lict, position, size)
        self.field = self.add_child(FilterField('', '0.5+0;0.0+0', '0.5+0;0.0+3'))
        self.song_selection = self.add_child(SongSelection('', '0.5+0;0.0+3', '0.5+0;1.0-5'))
        self.field.couple(self.song_selection)

    def focus(self):
        super().focus()
        term.widgets = [self, self.field, self.song_selection]

    def update(self):
        try:
            self.lict = client.get_playlist(term.current_playlist)
        except:
            self.lict = Lict({})

    def handle_input(self, inp):
        if inp.is_sequence:
            if inp.name == 'KEY_ENTER':
                if self.selected != []:
                    for index in self.selected:
                        client.queue_song(self.lict[index]['file'])
                    self.selected = []
                else:
                    if len(self.lict.list) > 0:
                        client.queue_song(self.lict[self.current]['file'])
            if inp.name == 'KEY_DELETE':
                if self.selected != []:
                    i = 0
                    self.selected.sort()
                    for index in self.selected:
                        client.delete_from_playlist(term.current_playlist, index-i)
                        self.lict.delete(index-i)
                        i += 1
                    self.selected = []
                else:
                    if len(self.lict.list) > 0:
                        client.delete_from_playlist(term.current_playlist, self.current)
                        self.lict.delete(self.current)
                        self.current = self.current
                return self.redraw()
            elif inp.name == 'KEY_LEFT':
                self._position = '0.5+0;0.0+0'
                term.focus(self.parent)
                # return self.redraw()
                return False
            elif inp.name == 'KEY_RIGHT':
                term.focus(self.field)
                return self.redraw()
        else:
            if inp == '`':
                if self.selected != []:
                    self.selected = []
                    return self.redraw()
                self._position = '0.5+0;0.0+0'
                term.focus(self.parent)
                return False
                # if (result := super().handle_input(inp)) is None:
                #     self._position = '0.5+0;0.0+1'
                #     self.parent.update()
                #     term.focus(self.parent)
                #     return self.redraw()
                # else:
                #     return result
                pass
        return super().handle_input(inp)


class SongSelection(Selection):
    def __init__(self, search, position='0.5+0;0.0+0', size='0.5+0;1.0-1'):
        self.search = search
        super().__init__(None, position, size)

    def update(self):
        self.lict = client.search_songs(self.search)

    def handle_input(self, inp):
        if inp.is_sequence:
            if inp.name == 'KEY_TAB':
                term.focus(self.pair)
                return
            elif inp.name == 'KEY_ENTER':
                if self.selected != []:
                    for index in self.selected:
                        client.queue_song(self.lict[index]['file'])
                    self.selected = []
                else:
                    if len(self.lict.list) > 0:
                        client.queue_song(self.lict[self.current]['file'])
            elif inp.name == 'KEY_LEFT':
                term.focus(self.parent)
                return self.redraw()
        else:
            if inp in ('=', '+'):
                tba = []
                if self.selected != []:
                    for i in self.selected:
                        tba.append(self.lict[i])
                else:
                    if len(self.lict) > 0:
                        tba = [self.lict[self.current]]
                for song in tba:
                    client.add_to_playlist(term.current_playlist, song['file'])
                self.parent.display()
                # self.parent.filter = self.parent.filter
                return False
        return super().handle_input(inp)


class TextField(Widget):
    def __init__(self, text, position, size):
        super().__init__(position, size)
        self.text = text
        self.scroll = 0

    def handle_input(self, inp):
        if inp.is_sequence:
            if inp.name == 'KEY_BACKSPACE':
                self.text = self.text[:-1]
                if self.scroll > 0:
                    self.scroll -= 1
                return self.redraw()
        else:
            if inp == '`':
                return super().handle_input(inp)
            if inp == '':
                return None
            position, size = self.scaled_dimensions()
            self.text += inp
            if len(self.text) >= size[0]:
                self.scroll += 1
            return self.redraw()
        return super().handle_input(inp)

    def draw(self):
        position, size = self.scaled_dimensions()
        if self.focused:
            return term.ljust(term.on_white + self.text[self.scroll:] + '|', size[0]) + term.normal
        else:
            return term.ljust(term.normal + self.text[self.scroll:], size[0])

    def display(self):
        if self.hide:
            return
        position, size = self.scaled_dimensions()
        with term.location(*position):
            # echo(self.text + term.on_white + ' '*(size[0]-len(self.text)))
            echo(self.draw())


class FilterField(TextField):
    def __init__(self, text, position, size):
        super().__init__(text, position, size)

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        self._text = value
        try:
            self.pair.search = value
            self.pair.display()
        except AttributeError:
            pass

    def handle_input(self, inp):
        if inp.is_sequence:
            if inp.name == 'KEY_TAB':
                try:
                    term.focus(self.pair)
                except AttributeError:
                    pass
                return False
            elif inp.name == 'KEY_LEFT':
                term.focus(self.parent)
                return self.redraw()
        else:
            pass
        return super().handle_input(inp)


class Radio(Widget):
    def __init__(self, options=None, selected=0, position='0.0+0;0.0+0', size='0.5-2;0.0+1'):
        super().__init__(position, size, bordered=False)
        self.current = selected
        self.options = options or ['ok']

    def next(self):
        self.current = (self.current+1)%len(self.options)

    def prev(self):
        self.current = (self.current-1)%len(self.options)

    def draw(self):
        position, size = self.scaled_dimensions()
        # with term.location(*position):
        if self.focused:
            return term.rjust('/'.join([f'{term.red}{j}{term.normal}' if i == self.current else f'{j}' for i, j in enumerate(self.options)]), size[0])
        else:
            return term.rjust('/'.join(self.options), size[0])

    def handle_input(self, inp):
        if inp.is_sequence:
            if inp.name == 'KEY_RIGHT':
                self.next()
                return False
            elif inp.name == 'KEY_LEFT':
                self.prev()
                return False

class Dialogue(Widget):
    def __init__(self, text, options=None, options_selected=0, callbacks=None, position='0.25+0;0.5-3', size='0.5+0;0.0+6'):
        super().__init__(position, size)
        self.text = text
        sx, sy = self.interpret(size)
        self.field_size = f'{sx[0]}{sx[1]-2:+};0.0+1'

        self.fields = [Radio(options, options_selected, size=self.field_size)]

        self.current = 0
        self.callbacks = callbacks

    def next(self):
        self.fields[self.current].focused = False
        self.current = (self.current+1)%len(self.fields)
        self.fields[self.current].focused = True

    def prev(self):
        self.fields[self.current].focused = False
        self.current = (self.current-1)%len(self.fields)
        self.fields[self.current].focused = True

    def display(self):
        if self.hide:
            return
        position, size = self.scaled_dimensions()
        with term.location(*position):
            i = 0
            for line in term.wrap(self.text, size[0]):
                i += 1
                echo(term.ljust(line, size[0]) + term.move_down(1) + term.move_x(position[0]))
            for k, field in enumerate(self.fields[:-1]):
                if k == self.current:
                    field.focused = True
                echo(field.draw() + term.move_down(1) + term.move_x(position[0]))
            for j in range(i, size[1]-len(self.fields)):
                echo(' '*size[0] + term.move_down(1) + term.move_x(position[0]))
            if self.current == len(self.fields)-1:
                self.fields[-1].focused = True
            echo(self.fields[-1].draw()) # draw radio at bottom

    def handle(self):
        current = self.fields[-1].current
        if self.callbacks is not None and self.callbacks[self.current] is not None:
            self.callbacks[self.current]()
        term.focus(self.parent)

    def handle_input(self, inp):
        if inp.is_sequence:
            if inp.name == 'KEY_ENTER':
                if self.current != len(self.fields)-1:
                    return None
                self.handle()
                for i in range(len(term.widgets)):
                    if self == term.widgets[i]:
                        term.widgets.pop(i)
                        break
                return False
            elif inp.name in ('KEY_TAB', 'KEY_DOWN'):
                self.next()
                return self.redraw()
            elif inp.name == 'KEY_UP':
                self.prev()
                return self.redraw()
        else:
            pass
        if (status := self.fields[self.current].handle_input(inp)) == False:
            return self.redraw()
        elif status == True:
            return status
        return super().handle_input(inp)


class CreatePlaylistDialogue(Dialogue):
    def __init__(self, position='0.25+0;0.5-3', size='0.5+0;0.0+6'):
        super().__init__('New playlist name: ', ['ok', 'cancel'], 0, position=position, size=size)
        self.fields = [TextField('new playlist', position=position, size=self.field_size)] + self.fields

    def handle(self):
        if self.fields[-1].current == 1:
            return
        try:
            client.create_playlist(self.fields[0].text)
            term.focus(self.parent)
        except:
            # term.focus(self.parent)
            # focusing parent adds a flicker with the redraw, unfortunately not preventable.
            # just using standard size dialogue means this dialogue is completely overwritten so it's OK
            d = self.parent.add_child(Dialogue('Duplicate playlist name!', ['ok']))
            term.widgets.append(d)
            term.focus(d)


class StatusWidget(Widget):
    def __init__(self, position='0.0+0;1.0-2', size='1.0+0;0.0+2'):
        super().__init__(position, size, bordered=False)
        self.image = pixcat.Image('./placeholder.jpg')

    def update(self):
        self.info = client.get_status()
        self.song = client.get_playing()
        
        if term.current_song != self.info.get('songid'):
            term.current_song = self.info.get('songid')
            if term.mode == 'queue':
                term.queue.display()
            elif term.mode == 'pretty_print':
                self.update_image()

    def update_image(self):
        if self.info.get('state') in ('stop', None):
            self.image = pixcat.Image('./placeholder.jpg')
        else:
            try:
                self.imagelink = os.popen('songinfo').read()
                self.image = pixcat.Image(self.imagelink)
                # self.image = pixcat.Image('./placeholder.jpg')
            except:
                self.image = pixcat.Image('./placeholder.jpg')
        self.display_image()

    def display_image(self):
        if term.mode != 'pretty_print':
            return
        position, size = self.scaled_dimensions()

        w, h = TERM.cell_px_width, TERM.cell_px_height
        i_cell_size = size[1]//2
        i_size = h*i_cell_size
        i_other_cell_size = i_size//w
        # with term.location(position[0]-i, position[1]-i):
        # self.image = self.image.resize(size[0], size[0])
        # self.image.thumbnail(i_size, stretch=True).show((position[0]+size[0])//2, position[1]-size[0]//2)
        self.image.thumbnail(i_size, stretch=True).show(position[0]+size[0]//2-i_other_cell_size//2, position[1]-i_cell_size-1)
        # self.image.show(position[0]-size[0], position[1]-size[0])
        # self.image.show()

    def focus(self):
        self.update_image()
        super().focus()
        term.widgets = []

    def defocus(self):
        self.image.hide()
        super().defocus()

    def display(self):
        self.update()
        if term.mode != 'pretty_print':
            self.display_regular()
        else:
            self.display_fancy()

    def get_bar(self, twidth):
        if (elapsed := self.info.get('elapsed')) is not None and (duration := self.info.get('duration')) is not None:
            width = int(float(elapsed)/float(duration)*twidth)
            bar = term.red + '─'*width + term.white + '─'*(twidth-width)
        else:
            return term.white + '─'*twidth + term.normal
        return bar + term.normal

    def display_fancy(self):
        position, size = self.scaled_dimensions()
        with term.location(*position):
            echo(term.center(self.get_bar(size[0]), size[0]) + term.move_down(1) + term.move_x(position[0]))
            line = ''
            if (state := self.info.get('state')) == 'stop':
                echo(term.center(f'{term.bold}Stopped{term.normal}', size[0]) + term.move_down(1) + term.move_x(position[0]))
                echo(' '*size[0])
                return
            elif state == 'pause':
                line += f'{term.bold}Paused ─ {term.normal}'
            if (title := self.song.get('title')) is None:
                title = self.song['file']
            line += f'{term.red} {title}{term.normal}'
            if term.length(line) > size[0]:
                line = term.truncate(line, size[0]-3) + '...'
            echo(term.center(line, size[0]) + term.move_down(1) + term.move_x(position[0]))
            line = ''
            if (artist := self.song.get('artist')) is not None:
                line += f'{term.yellow} {artist}{term.normal}'
            if (album := self.song.get('album')) is not None:
                if line != '':
                    line += ' ─ '
                line += f'{term.magenta} {album}{term.normal}'
                if (track := self.song.get('track')) is not None:
                    line += f' {term.white}(#{track}){term.normal}'
            if term.length(line) > size[0]:
                line = term.truncate(line, size[0]-3) + '...'
            echo(term.center(line, size[0]))

    def display_regular(self):
        position, size = self.scaled_dimensions()
        with term.location(*position):
            echo(self.get_bar(size[0]) + term.move_down(1) + term.move_x(position[0]))
            now_playing = ''
            if (state := self.info.get('state')) == 'stop':
                now_playing = f'{term.bold}Stopped{term.normal}'
            else:
                if state == 'pause':
                    now_playing += f'{term.bold}Paused: {term.normal}'
                if (title := self.song.get('title')) is None:
                    title = self.song['file']
                now_playing += term.red + title + term.normal
                if (artist := self.song.get('artist')) is not None:
                    now_playing += f' by {term.yellow}{artist}{term.normal}'
                stamps = ''
                if self.info.get('random') == '1':
                    stamps += ''
                if self.info.get('repeat') == '1':
                    stamps += ''
                stamps += f'{term.bold}{to_timestamp(float(self.info.get("elapsed")))}/{to_timestamp(float(self.info.get("duration")))}{term.normal}'
                spaces = ' '*(size[0] - term.length(stamps) - term.length(now_playing))
                now_playing = now_playing + spaces + stamps
            echo(term.ljust(now_playing, size[0]))



class Queue(Selection):
    def __init__(self, position='0.0+0;0.0+0', size='1.0+0;1.0-2'):
        super().__init__(None, position, size, bordered=False)
        self.formats = [
            [('{title}', 'white', 'r', 0.5), ('{artist}', 'white', 'l', 0.5)],
            [('{title}', 'red', 'r', 0.5), ('{artist}', 'blue', 'l', 0.5)]
        ]

    def update(self):
        self.lict = client.get_queue()

    def focus(self):
        term.widgets = [self]
        super().focus()

    def handle_input(self, inp):
        if inp.is_sequence:
            if inp.name == 'KEY_SDOWN':
                self.select()
                self.next()
                return self.redraw()
            if inp.name == 'KEY_ENTER':
                if self.selected != []:
                    return None
                else:
                    if len(self.lict.list) > 0:
                        client.play_from_queue(self.lict[self.current]['id'])
                        term.status.update()
            elif inp.name == 'KEY_DELETE':
                if self.selected != []:
                    i = 0
                    self.selected.sort()
                    for index in self.selected:
                        client.dequeue(index-i)
                        self.lict.delete(index-i)
                        i += 1
                    self.selected = []
                else:
                    if len(self.lict.list) > 0:
                        client.dequeue(self.current)
                        self.lict.delete(self.current)
                        self.current = self.current
                return self.redraw()
        return super().handle_input(inp)


class PlayerTerminal(blessed.Terminal):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mode = ''
        self.current_playlist = None
        self.current_song = None

        self.current_widget = None
        self.widgets = []

    def launch(self):
        self.playlist_selection = PlaylistSelection()
        self.status = StatusWidget()
        self.queue = Queue()
        self.set_mode('queue')
        self.status.display()

    def draw(self, d, twidth, formats=None, hovered=False, selected=False, playing=False):
        if isinstance(d, Lict):
            d = Lict.dict
        string = ''
        rwidth = 0 # running total of width
        format = formats[0]
        if playing:
            format = formats[1]
        for i, (section, style, alignment, frac) in enumerate(format):
            if hovered:
                string += (self.bold)
            if selected:
                string += (self.reverse)
            text = section.format(**d)
            if i == len(format)-1:
                width = twidth - rwidth
            else:
                width = int(frac*twidth)
                rwidth += (width + 1)
            if self.length(text) > width:
                text = self.truncate(text, width-3) + '...'
            if alignment == 'l':
                string += getattr(self, style) + (self.ljust(text, width))
            elif alignment == 'c':
                string += getattr(self, style) + (self.center(text, width))
            elif alignment == 'r':
                string += getattr(self, style) + (self.rjust(text, width))
            if i != len(format)-1:
                string += ' '
            string += (self.normal)
        return string

    def handle_input(self, inp):
        status = self.current_widget.handle_input(inp)
        if status is None:
            if inp.is_sequence:
                if inp.name == 'KEY_RIGHT':
                    client.skip()
            else:
                if inp == '1':
                    self.set_mode('queue')
                elif inp == '2':
                    self.set_mode('pretty_print')
                elif inp == '3':
                    self.set_mode('playlists')
                elif inp == 'p':
                    client.toggle_pause()
                elif inp == 's':
                    client.toggle_random()
                elif inp == 'r':
                    client.toggle_repeat()
        return status

    def set_mode(self, mode):
        if self.mode == mode:
            return
        self.mode = mode
        if mode == 'queue':
            self.focus(self.queue)
            self.status._position = '0.0+0;1.0-2'
            self.status._size = '1.0+0;0.0+2'
            self.status.display()
        elif mode == 'pretty_print':
            self.status._position = '0.1+0;0.7+0'
            self.status._size = '0.8+0;1.0+0'
            self.status.display_info = True
            print(self.clear)
            self.current_widget = self.status
            self.status.focus()
            self.status.display()
        elif mode == 'playlists':
            self.focus(self.playlist_selection)
            self.status._position = '0.0+0;1.0-2'
            self.status._size = '1.0+0;0.0+2'
            self.status.display()
        # elif mode == 'queue':
        #     self.focus(self.queue)

    def focus(self, widget):
        try:
            self.current_widget.defocus()
        except AttributeError:
            pass
        widget.focus()
        self.current_widget = widget

    def display(self):
        for widget in self.widgets:
            if widget.bordered:
                widget.display_shell()
            widget.display()
        if self.status.bordered:
            self.status.display_shell()
        self.status.display()
        if self.mode == 'pretty_print':
            self.status.update_image()


term = PlayerTerminal()
client = Client(6600)

key_codes = term.get_keyboard_codes()

inp = None
sizing = (term.height, term.width)
with term.hidden_cursor(), term.fullscreen(), term.cbreak():
    term.launch()
    status = False
    while status != True:
        inp = term.inkey(timeout=0.5)
        term.status.display()
        if sizing != (term.height, term.width):
            print(term.clear)
            term.display()
        sizing = (term.height, term.width)
        status = term.handle_input(inp)
