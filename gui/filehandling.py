# This file is part of MyPaint.
# Copyright (C) 2007-2009 by Martin Renold <martinxyz@gmx.ch>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os, re
from glob import glob
import sys

import gtk
from gettext import gettext as _
from gettext import ngettext

from lib import document, helpers
import drawwindow

# Utility function to work around the fact that gtk FileChooser/FileFilter
# does not have an easy way to use case insensitive filters
def get_case_insensitive_glob(string):
    '''Ex: '*.ora' => '*.[oO][rR][aA]' '''
    ext = string.split('.')[1]
    globlist = ["[%s%s]" % (c.lower(), c.upper()) for c in ext]
    return '*.%s' % ''.join(globlist)


class FileHandler(object):
    def __init__(self, app):
        self.app = app
        #NOTE: filehandling and drawwindow are very tightly coupled
        self.save_dialog = None

        file_actions = [ \
        ('New',          gtk.STOCK_NEW, _('New'), '<control>N', None, self.new_cb),
        ('Open',         gtk.STOCK_OPEN, _('Open...'), '<control>O', None, self.open_cb),
        ('OpenLast',     None, _('Open Last'), 'F3', None, self.open_last_cb),
        ('Reload',       gtk.STOCK_REFRESH, _('Reload'), 'F5', None, self.reload_cb),
        ('Save',         gtk.STOCK_SAVE, _('Save'), '<control>S', None, self.save_cb),
        ('SaveAs',       gtk.STOCK_SAVE_AS, _('Save As...'), '<control><shift>S', None, self.save_as_cb),
        ('SaveScrap',    None, _('Save As Scrap'), 'F2', None, self.save_scrap_cb),
        ('PrevScrap',    None, _('Open Previous Scrap'), 'F5', None, self.open_scrap_cb),
        ('NextScrap',    None, _('Open Next Scrap'), 'F6', None, self.open_scrap_cb),
        ]
        ag = gtk.ActionGroup('FileActions')
        ag.add_actions(file_actions)
        self.app.ui_manager.insert_action_group(ag, -1)

        ra = gtk.RecentAction('OpenRecent', _('Open Recent'), _('Open Recent files'), None)
        ra.set_show_tips(True)
        ra.set_show_numbers(True)
        rf = gtk.RecentFilter()
        rf.add_application('mypaint')
        ra.add_filter(rf)
        ra.set_sort_type(gtk.RECENT_SORT_MRU)
        ra.connect('item-activated', self.open_recent_cb)
        ag.add_action(ra)

        for action in ag.list_actions():
            self.app.kbm.takeover_action(action)

        self._filename = None
        self.active_scrap_filename = None
        self.set_recent_items()

    def set_recent_items(self):
        # this list is consumed in open_last_cb
        self.recent_items = [
                i for i in gtk.recent_manager_get_default().get_items()
                if "mypaint" in i.get_applications() and i.exists()
        ]
        self.recent_items.reverse()

    def get_filename(self):
        return self._filename

    def set_filename(self, value):
        self._filename = value
        if self.filename:
            self.app.drawWindow.set_title("MyPaint - %s" % os.path.basename(self.filename))
            if self.filename.startswith(self.get_scrap_prefix()):
                self.active_scrap_filename = self.filename
        else:
            self.app.drawWindow.set_title("MyPaint")
    filename = property(get_filename, set_filename)

    def init_save_dialog(self):
        dialog = gtk.FileChooserDialog(_("Save.."), self.app.drawWindow,
                                       gtk.FILE_CHOOSER_ACTION_SAVE,
                                       (gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                                        gtk.STOCK_SAVE, gtk.RESPONSE_OK))
        self.save_dialog = dialog
        dialog.set_default_response(gtk.RESPONSE_OK)
        dialog.set_do_overwrite_confirmation(True)

        filter2info = {}
        self.filter2info = filter2info

        filters = [ #name, patterns, saveopts
        (_("Any format (prefer OpenRaster)"), ("*.ora", "*.png", "*.jpg", "*.jpeg"), ('.ora', {})),
        (_("OpenRaster (*.ora)"), ("*.ora", ), ('.ora', {})),
        ("PNG solid with background (*.png)", ("*.png", ), ('.png', {'alpha': False})),
        (_("PNG transparent (*.png)"), ("*.png", ), ('.png', {'alpha': True})),
        (_("Multiple PNG transparent (*.XXX.png)"), ("*.png", ), ('.png', {'multifile': True})),
        (_("JPEG 90% quality (*.jpg; *.jpeg)"), ("*.jpg", "*.jpeg"), ('.jpg', {'quality': 90})),
        ]
        for nr, filt in enumerate(filters):
            name, patterns, saveopts = filt
            f = gtk.FileFilter()
            filter2info[f] = saveopts
            f.set_name(name)
            for pat in patterns:
                f.add_pattern(get_case_insensitive_glob(pat))
            dialog.add_filter(f)
            if nr == 0:
                self.save_filter_default = f

    def confirm_destructive_action(self, title='Confirm', question='Really continue?'):
        t = self.doc.unsaved_painting_time
        if t < 30:
            # no need to ask
            return True

        if t > 120:
            t = int(round(t/60))
            t = ngettext('%d minute', '%d minutes', t) % t
        else:
            t = int(round(t))
            t = ngettext('%d second', '%d seconds', t) % t
        d = gtk.Dialog(title, self.app.drawWindow, gtk.DIALOG_MODAL)

        b = d.add_button(gtk.STOCK_DISCARD, gtk.RESPONSE_OK)
        b.set_image(gtk.image_new_from_stock(gtk.STOCK_DELETE, gtk.ICON_SIZE_BUTTON))
        d.add_button(gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL)
        b = d.add_button(_("_Save as Scrap"), gtk.RESPONSE_APPLY)
        b.set_image(gtk.image_new_from_stock(gtk.STOCK_SAVE, gtk.ICON_SIZE_BUTTON))

        d.set_has_separator(False)
        d.set_default_response(gtk.RESPONSE_CANCEL)
        l = gtk.Label()
        l.set_markup(_("<b>%s</b>\n\nThis will discard %s of unsaved painting.") % (question,t))
        l.set_padding(10, 10)
        l.show()
        d.vbox.pack_start(l)
        response = d.run()
        d.destroy()
        if response == gtk.RESPONSE_APPLY:
            self.save_scrap_cb(None)
            return True
        return response == gtk.RESPONSE_OK

    def new_cb(self, action):
        if not self.confirm_destructive_action():
            return
        bg = self.doc.background
        self.doc.clear()
        self.doc.set_background(bg)
        self.filename = None
        self.set_recent_items()
        self.app.drawWindow.reset_view_cb(None)

    @drawwindow.with_wait_cursor
    def open_file(self, filename):
        try:
            self.doc.load(filename)
        except document.SaveLoadError, e:
            self.app.message_dialog(str(e),type=gtk.MESSAGE_ERROR)
        else:
            self.filename = os.path.abspath(filename)
            print 'Loaded from', self.filename
            self.app.drawWindow.reset_view_cb(None)
            self.app.drawWindow.tdw.recenter_document()

    @drawwindow.with_wait_cursor
    def save_file(self, filename, **options):
        try:
            x, y, w, h =  self.doc.get_bbox()
            if w == 0 and h == 0:
                raise document.SaveLoadError, _('Did not save, the canvas is empty.')
            self.doc.save(filename, **options)
        except document.SaveLoadError, e:
            self.app.message_dialog(str(e),type=gtk.MESSAGE_ERROR)
        else:
            self.filename = os.path.abspath(filename)
            print 'Saved to', self.filename
            gtk.recent_manager_get_default().add_full("file://" + self.filename,
                    {
                        'app_name': 'mypaint',
                        'app_exec': sys.argv[0],
                        # todo: get mime_type
                        'mime_type': 'application/octet-stream'
                    }
            )

    def open_cb(self, action):
        if not self.confirm_destructive_action():
            return
        dialog = gtk.FileChooserDialog(_("Open..."), self.app.drawWindow,
                                       gtk.FILE_CHOOSER_ACTION_OPEN,
                                       (gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                                        gtk.STOCK_OPEN, gtk.RESPONSE_OK))
        dialog.set_default_response(gtk.RESPONSE_OK)

        filters = [ #name, patterns
        (_("All Recognized Formats"), ("*.ora", "*.png", "*.jpg", "*.jpeg")),
        (_("OpenRaster (*.ora)"), ("*.ora",)),
        (_("PNG (*.png)"), ("*.png",)),
        (_("JPEG (*.jpg; *.jpeg)"), ("*.jpg", "*.jpeg")),
        ]
        for name, patterns in filters:
            f = gtk.FileFilter()
            f.set_name(name)
            for p in patterns:
                f.add_pattern(get_case_insensitive_glob(p))
            dialog.add_filter(f)

        if self.filename:
            dialog.set_filename(self.filename)
        try:
            if dialog.run() == gtk.RESPONSE_OK:
                self.open_file(dialog.get_filename())
        finally:
            dialog.destroy()

    def save_cb(self, action):
        if not self.filename:
            self.save_as_cb(action)
        else:
            self.save_file(self.filename)

    def save_as_cb(self, action):
        if not self.save_dialog:
            self.init_save_dialog()
        dialog = self.save_dialog

        def dialog_set_filename(s):
            # According to pygtk docu we should use set_filename(),
            # however doing so removes the selected filefilter.
            path, name = os.path.split(s)
            dialog.set_current_folder(path)
            dialog.set_current_name(name)

        if self.filename:
            dialog_set_filename(self.filename)
        else:
            dialog_set_filename('')
            dialog.set_filter(self.save_filter_default)
            # choose the most recent save folder
            self.set_recent_items()
            for item in reversed(self.recent_items):
                uri = item.get_uri()
                fn = helpers.get_file_path_from_dnd_dropped_uri(uri)
                dn = os.path.dirname(fn)
                if os.path.isdir(dn):
                    dialog.set_current_folder(dn)
                    break

        try:
            while dialog.run() == gtk.RESPONSE_OK:

                filename = dialog.get_filename()
                name, ext = os.path.splitext(filename)
                ext_filter, options = self.filter2info.get(dialog.get_filter(), ('ora', {}))

                if ext:
                    if ext_filter != ext:
                        # Minor ugliness: if the user types '.png' but
                        # leaves the default .ora filter selected, we
                        # use the default options instead of those
                        # above. However, they are the same at the moment.
                        options = {}
                    assert(filename)
                    dialog.hide()
                    self.save_file(filename, **options)
                    break

                # add proper extension
                filename = name + ext_filter

                # trigger overwrite confirmation for the modified filename
                dialog_set_filename(filename)
                dialog.response(gtk.RESPONSE_OK)

        finally:
            dialog.hide()

    def save_scrap_cb(self, action):
        filename = self.filename
        prefix = self.get_scrap_prefix()

        number = None
        if filename:
            l = re.findall(re.escape(prefix) + '([0-9]+)', filename)
            if l:
                number = l[0]

        if number:
            # reuse the number, find the next character
            char = 'a'
            for filename in glob(prefix + number + '_*'):
                c = filename[len(prefix + number + '_')]
                if c >= 'a' and c <= 'z' and c >= char:
                    char = chr(ord(c)+1)
            if char > 'z':
                # out of characters, increase the number
                self.filename = None
                return self.save_scrap_cb(action)
            filename = '%s%s_%c' % (prefix, number, char)
        else:
            # we don't have a scrap filename yet, find the next number
            maximum = 0
            for filename in glob(prefix + '[0-9][0-9][0-9]*'):
                filename = filename[len(prefix):]
                res = re.findall(r'[0-9]*', filename)
                if not res: continue
                number = int(res[0])
                if number > maximum:
                    maximum = number
            filename = '%s%03d_a' % (prefix, maximum+1)

        #if self.doc.is_layered():
        #    filename += '.ora'
        #else:
        #    filename += '.png'
        filename += '.ora'

        assert not os.path.exists(filename)
        self.save_file(filename)

    def get_scrap_prefix(self):
        prefix = self.app.settingsWindow.save_scrap_prefix
        prefix = os.path.abspath(prefix)
        if os.path.isdir(prefix):
            if not prefix.endswith(os.path.sep):
                prefix += os.path.sep
        return prefix

    def list_scraps(self):
        prefix = self.get_scrap_prefix()
        filenames = []
        for ext in ['png', 'ora', 'jpg', 'jpeg']:
            filenames += glob(prefix + '[0-9]*.' + ext)
            filenames += glob(prefix + '[0-9]*.' + ext.upper())
        filenames.sort()
        return filenames

    def list_scraps_grouped(self):
        """return scraps grouped by their major number"""
        def scrap_id(filename):
            s = os.path.basename(filename)
            return re.findall('([0-9]+)', s)[0]
        filenames = self.list_scraps()
        groups = []
        while filenames:
            group = []
            sid = scrap_id(filenames[0])
            while filenames and scrap_id(filenames[0]) == sid:
                group.append(filenames.pop(0))
            groups.append(group)
        return groups

    def open_recent_cb(self, action):
        """Callback for RecentAction"""
        if not self.confirm_destructive_action():
            return
        uri = action.get_current_uri()
        fn = helpers.get_file_path_from_dnd_dropped_uri(uri)
        self.open_file(fn)

    def open_last_cb(self, action):
        """Callback to open the last file"""
        if not self.recent_items:
            return
        if not self.confirm_destructive_action():
            return
        uri = self.recent_items.pop().get_uri()
        fn = helpers.get_file_path_from_dnd_dropped_uri(uri)
        self.open_file(fn)

    def open_scrap_cb(self, action):
        groups = self.list_scraps_grouped()
        if not groups:
            self.app.message_dialog(_('There are no scrap files named "%s" yet.' % (self.get_scrap_prefix() + '[0-9]*')), gtk.MESSAGE_WARNING)
            return
        if not self.confirm_destructive_action():
            return
        next = action.get_name() == 'NextScrap'

        if next: idx = 0
        else:    idx = -1
        for i, group in enumerate(groups):
            if self.active_scrap_filename in group:
                if next: idx = i + 1
                else:    idx = i - 1
        filename = groups[idx%len(groups)][-1]
        self.open_file(filename)

    def reload_cb(self, action):
        if self.filename and self.confirm_destructive_action():
            self.open_file(self.filename)
