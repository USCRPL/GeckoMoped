#!/usr/bin/env python3

"""
GeckoMotion application.

Notes:
Want to use action groups, but this has bug when used with glade which makes it
unusable.  Hence all the calls to Handler.update() in order to correctly set
the sensitivity of various widgets.  Ugh.
"""

import gi

# import GTK
gi.require_version('Gtk', '3.0')

# import GTKSourceView
gi.require_version('GtkSource', '3.0')

from gi.repository import GObject, Gtk, Gdk, GtkSource, Pango, GLib, GdkPixbuf
import copy, pickle, os, os.path, sys, re, time
from threading import Thread, Lock
#from multiprocessing import Process
#import subprocess
from geckomoped import _gladedir, _app_fullname, _version, _imagedir, _icondir, __path__
from geckomoped.devices import Devices, RS485Devices

def _fulldir(d):
	return os.path.join(__path__[0], d)

class TabLabel(Gtk.Box):
	__gsignals__ = {
		"close-clicked": (GObject.SignalFlags.RUN_FIRST, GObject.TYPE_NONE, ()),
	}
	def __init__(self, label_text):
		Gtk.Box.__init__(self)
		self.set_orientation(Gtk.Orientation.HORIZONTAL)
		self.set_spacing(4) # spacing: [icon|5px|label|5px|close]  
		
		# icon
		normal_icon = Gtk.Image.new_from_stock(Gtk.STOCK_FILE, Gtk.IconSize.MENU)
		top_icon = Gtk.Image.new_from_stock(Gtk.STOCK_ABOUT, Gtk.IconSize.MENU)
		list_icon = Gtk.Image.new_from_stock(Gtk.STOCK_JUSTIFY_LEFT, Gtk.IconSize.MENU)
		self.iconlist = [normal_icon, top_icon, list_icon]
		self.pack_start(self.iconlist[0], False, False, 0)
		
		# label 
		self.label = Gtk.Label(label_text)
		self.pack_start(self.label, True, True, 0)
		
		# close button
		button = Gtk.Button()
		self.closebutton = button
		button.set_relief(Gtk.ReliefStyle.NONE)
		button.set_focus_on_click(False)
		button.add(Gtk.Image.new_from_stock(Gtk.STOCK_CLOSE, Gtk.IconSize.MENU))
		button.connect("clicked", self.button_clicked)
		data =  ".button {\n" \
				"-GtkButton-default-border : 0px;\n" \
				"-GtkButton-default-outside-border : 0px;\n" \
				"-GtkButton-inner-border: 0px;\n" \
				"-GtkWidget-focus-line-width : 0px;\n" \
				"-GtkWidget-focus-padding : 0px;\n" \
				"padding: 0px;\n" \
				"}"
		provider = Gtk.CssProvider()
		provider.load_from_data(data.encode('ASCII'))
		# 600 = GTK_STYLE_PROVIDER_PRIORITY_APPLICATION
		button.get_style_context().add_provider(provider, 600) 
		self.pack_start(button, False, False, 0)
		
		self.show_all()
	def set_icon(self, index):
		self.remove(self.get_children()[0])
		self.remove(self.get_children()[0])
		self.remove(self.get_children()[0])
		self.pack_start(self.iconlist[index], False, False, 0)
		self.pack_start(self.label, True, True, 0)
		self.pack_start(self.closebutton, False, False, 0)
		self.show_all()
	def set_label(self, txt):
		self.label.set_text(txt)
	def button_clicked(self, button, data=None):
		self.emit("close-clicked")
		
		
class Tab(object):
	"""Class to encapsulate GtkNotebook tabs.  In each tab, we contain a
	GtkSourceView widget and associated data.
	We derive from GObject simply to be able to be placed in a liststore.
	
	read_only may be set to a string to entitle a read-only buffer (e.g. for 
	assembly listings)
	"""
	def __init__(self, mgr, filename=None, is_top=False, existing_tab=None, \
					lang="geckomotion", placeholder=False, read_only=False):
		self.mgr = mgr
		self.mainwindow = mgr.ui.mainwindow
		self.placeholder = placeholder
		self.read_only = bool(read_only)
		self.ro_title = read_only if isinstance(read_only, str) else None
		self.exec_mark = None
		self.err_mark = None
		self.sw = None
		self.mtime = None
		if existing_tab is not None:
			self.filename = existing_tab.filename
			self.is_top = existing_tab.is_top
			self.buffer_gm = existing_tab.buffer_gm
		else:
			self.filename = filename
			self.is_top = is_top
			self.buffer_gm = GtkSource.Buffer()
			self.buffer_gm.set_language(mgr.lang_manager.get_language(lang))
			# These need to be 'connect_after' since the default callback has to run
			# before the state (in buf_notify) will be valid.
			if not read_only:
				self.buffer_gm.connect_after("modified-changed", mgr.buf_notify, self)
				self.buffer_gm.connect_after("changed", mgr.buf_notify, self)
				self.buffer_gm.connect_after("changed", mgr.mod_asm, self)
				self.buffer_gm.connect_after("mark-set", mgr.buf_notify, self)
				self.buffer_gm.connect_after("redo", mgr.buf_notify, self)
				self.buffer_gm.connect_after("undo", mgr.buf_notify, self)
		if not placeholder:
			self.show()
		
	def show(self):
		"""Convert a placeholder tab to a real tab in the notebook.  This is called when
		user selects e.g. an error message which refers to a file in a placeholder tab.
		Also, is called automatically for non-placeholder tabs.
		"""
		self.sw = Gtk.ScrolledWindow()
		self.sourceview_gm = GtkSource.View.new_with_buffer(self.buffer_gm)
		self.sw.add(self.sourceview_gm)
		if self.read_only:
			title = self.ro_title
		else:
			title = "<untitled>" if self.filename is None else os.path.basename(self.filename)
		self.tab_label = TabLabel(title)
		self.mgr.ui.notebook.append_page(self.sw, self.tab_label)
		self.tab_label.connect("close-clicked", self.mgr.tab_closed, self)
		self.sourceview_gm.set_tab_width(4)
		self.sourceview_gm.set_can_focus(True)
		if self.read_only:
			self.tab_label.set_icon(2)
			self.sourceview_gm.set_editable(False)
		else:
			self.sourceview_gm.set_show_line_numbers(True)
			self.sourceview_gm.set_show_line_marks(True)
			self.sourceview_gm.set_auto_indent(True)
			self.sourceview_gm.set_show_right_margin(True)
			self.sourceview_gm.set_right_margin_position(80)
		self.sourceview_gm.connect("key-press-event", self.mgr.ui.key_press)
		self.sourceview_gm.set_insert_spaces_instead_of_tabs(True)
		self.sourceview_gm.set_wrap_mode(Gtk.WrapMode.WORD)
		if self.mgr.font_desc:
			self.sourceview_gm.modify_font(self.mgr.font_desc)
		
		self.sourceview_gm.set_mark_attributes('exec', self.mgr.exec_attrs, 1)
		self.sourceview_gm.set_mark_attributes('err', self.mgr.err_attrs, 1)
		self.sourceview_gm.set_mark_attributes('bkpt', self.mgr.bkpt_attrs, 1)
		self.sw.show_all()
		self.placeholder = False
	def get_ident(self):
		return self.ro_title    
	def share_tab(self, other):
		self.buffer_gm = other.buffer_gm
		self.filename = other.filename
		self.is_top = other.is_top
	def buf(self):
		return self.buffer_gm
	def sv(self):
		return self.sourceview_gm
	def get_mgr(self):
		return self.mgr
	def set_top(self, yes):
		if yes != self.is_top:
			self.is_top = yes
			self.tab_label.set_icon(1 if yes else 0)
	def update_title(self):
		if not self.placeholder:
			self.tab_label.set_label(self.get_filename_condensed())
	def set_filename(self, filename):
		self.filename = filename
		self.update_title()
	def get_filename_str(self):
		return self.filename if self.filename is not None else "<untitled>"
	def get_filename_condensed(self):
		return os.path.basename(self.filename) if self.filename is not None else "<untitled>"
	def get_abs_filename(self):
		return os.path.abspath(self.filename) if self.filename is not None else None
		
	def load_file(self, buffer, path):
		buffer.begin_not_undoable_action()
		try:
			with open(path, "r") as f:
				self.mtime = os.path.getmtime(path)
				txt = f.read()
		except:
			return False
		buffer.set_text(txt)
		buffer.end_not_undoable_action()
		buffer.set_modified(False)
		buffer.place_cursor(buffer.get_start_iter())
		self.mgr.set_filename(self, path)
		return True
		
	def is_file_modified_externally(self):
		"""Test whether buffer contents are older than file on disc i.e. an external
		program has modified the file since we last loaded or saved it.
		"""
		if self.mtime is None or self.filename is None or self.read_only:
			return False
		return os.path.getmtime(self.get_abs_filename()) > self.mtime
		
	def reload_file(self):
		"""Re-load buffer from file on disc.
		Usually called when assembling, and imported file is newer on disc, but
		user hasn't modified in-storage buffer contents.
		FIXME: this will ruin any breakpoints in the file.
		"""
		if self.filename is None:
			return True
		return self.load_file(self.buf(), self.get_abs_filename())
				
	def store_file(self, buffer, filename):
		if os.path.isabs(filename):
			path = filename
		else:
			path = os.path.abspath(filename)
		txt = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
		try:
			with open(path, "w") as f:
				f.write(txt)
			self.mtime = os.path.getmtime(path)
		except:
			return False
		buffer.set_modified(False)
		self.mgr.set_filename(self, filename)
		return True
	
	def query_save(self, filename):
		# Called when closing tab, but current file is modified.
		# Pops up a question dialog and returns the response:
		#  YES = save the current file, 
		#  NO = discard the current modifications, 
		#  CANCEL = abort loading the new file
		dialog = Gtk.MessageDialog(self.mainwindow, 0, Gtk.MessageType.WARNING,
			Gtk.ButtonsType.NONE, "File modified")
		if filename is None:
			filename = "untitled file"
		dialog.format_secondary_text("Save " + filename + "?")
		dialog.add_buttons("YES", Gtk.ResponseType.YES,
						   "NO", Gtk.ResponseType.NO,
						   "CANCEL", Gtk.ResponseType.CANCEL,)
		response = dialog.run()
		dialog.destroy()
		return response
		
	def open_file(self, filename):
		buffer = self.buf()
		# get the new language for the file mimetype
		manager = self.mgr.lang_manager

		if os.path.isabs(filename):
			path = filename
		else:
			path = os.path.abspath(filename)

		language = manager.guess_language(filename, None)
		if language:
			buffer.set_highlight_syntax(True)
			buffer.set_language(language)
		else:
			print('No language found for file "%s"' % filename)
			buffer.set_highlight_syntax(False)

		#begin, end = buffer.get_bounds()
		#buffer.remove_source_marks(begin, end, None)
		self.load_file(buffer, path) # TODO: check return
		return True
		
	def discard_or_save_current_file(self, dialog):
		if self.read_only:
			return True
		buffer = self.buf()
		if buffer.get_modified():
			resp = self.query_save(self.filename)
			if resp == Gtk.ResponseType.CANCEL:
				return False
			elif resp == Gtk.ResponseType.YES:
				if self.filename is None:
					# untitled so far, so run save-as dialog
					if self.file_saveas(dialog) == Gtk.ResponseType.CANCEL:
						return False
				else:
					self.store_file(buffer, self.filename)
			else:
				# User discarding modifications
				buffer.set_modified(False)
		return True
		
	def file_saveas(self, dialog):
		if self.is_untitled():
			dialog.set_current_name("Untitled file")
			pf = self.mgr.ui.pp.get_project_folder()
			if pf: dialog.set_current_folder(pf)
		else:
			dialog.set_filename(self.filename)
		self.set_shortcut_folders(dialog)
		resp = dialog.run()
		try:
			if resp == Gtk.ResponseType.OK:
				# Check if the new saveas name already exists in another tab.
				newname = dialog.get_filename()
				fi = self.mgr.get_fileinfo(newname)
				tabs = fi.get_tabs() if fi is not None else []
				if len(tabs) > 1 or len(tabs) == 1 and tabs[0] != self:
					#TODO (message dialog)
					print("Conflicting tab")
				else:
					self.store_file(self.buf(), newname)
		finally:
			dialog.hide()
		return resp
		
	def set_shortcut_folders(self, dlg):
		cs = dlg.list_shortcut_folders()
		for c in cs:
			dlg.remove_shortcut_folder(c)
		opts = self.mgr.ui.pp
		pf = opts.get_project_folder()
		uf = opts.get_usrlib_folder()
		sf = opts.get_stdlib_folder()
		if pf: dlg.add_shortcut_folder(pf)
		if uf: dlg.add_shortcut_folder(uf)
		if sf: dlg.add_shortcut_folder(sf)
		
	def is_untitled(self):
		return self.filename is None
		
	def is_last_tab_of_file(self):
		return self.filename is None or len(self.mgr.files[self.filename].tabs) == 1
	def is_shared_buffer(self):
		return self.filename is not None and len(self.mgr.files[self.filename].tabs) > 1
	def get_page_index(self):
		if self.placeholder:
			return None
		return self.mgr.ui.notebook.page_num(self.sw)
	def close(self, dialog):
		if not self.placeholder and not self.read_only:
			maybe_save = self.is_last_tab_of_file()
			if maybe_save and not self.discard_or_save_current_file(dialog):
				return False
		self.mgr.tab_is_closed(self)
		return True

	def focus(self):
		if self.placeholder:
			self.show()
		self.mgr.focus(self)
	def move_exec_mark(self, iter, scroll=False):
		if iter is None:
			if self.exec_mark:
				self.buf().delete_mark(self.exec_mark)
				self.exec_mark = None
		else:
			if not self.exec_mark or self.exec_mark.get_deleted():
				self.exec_mark = self.buf().create_source_mark('exec', 'exec', iter)
			else:
				self.buf().move_mark(self.exec_mark, iter)
			self.exec_mark.set_visible(True)
			if scroll:
				#self.sourceview_gm.scroll_to_iter(iter, 0., False, 0., 0.)
				self.sourceview_gm.scroll_to_mark(self.exec_mark, 0., False, 0., 0.)
	def remove_exec_mark(self):
		if self.exec_mark:
			self.buf().delete_mark(self.exec_mark)
			self.exec_mark = None
	def move_err_mark(self, iter, scroll=False):
		if iter is None:
			if self.err_mark:
				self.buf().delete_mark(self.err_mark)
				self.err_mark = None
		else:
			if not self.err_mark or self.err_mark.get_deleted():
				self.err_mark = self.buf().create_source_mark('err', 'err', iter)
			else:
				self.buf().move_mark(self.err_mark, iter)
			self.err_mark.set_visible(True)
			if scroll:
				#self.sourceview_gm.scroll_to_iter(iter, 0., False, 0., 0.)
				self.sourceview_gm.scroll_to_mark(self.err_mark, 0., False, 0., 0.)
	def remove_err_mark(self):
		if self.err_mark:
			self.buf().delete_mark(self.err_mark)
			self.err_mark = None
	
	def get_search_from(self, frm, fwd):
		if frm=='cursor':
			return self.buffer_gm.get_iter_at_mark(self.buffer_gm.get_insert())
		elif frm=='start':
			return self.buffer_gm.get_start_iter()
		elif frm=='end':
			return self.buffer_gm.get_end_iter()
		else:
			x = self.buffer_gm.get_selection_bounds()
			if x == ():
				return self.buffer_gm.get_iter_at_mark(self.buffer_gm.get_insert())
			if fwd:
				return x[1]
			else:
				return x[0]
	def get_selection(self):
		x = self.buffer_gm.get_selection_bounds()
		if x == ():
			return None
		return self.buffer_gm.get_text(x[0], x[1], False)
		
	def show_found(self, result):
		if result is None:
			return False
		s, e = result
		self.buffer_gm.select_range(s, e)
		self.sourceview_gm.scroll_to_mark(self.buffer_gm.get_insert(), 0., False, 0., 0.)
		#FIXME: can't work out how to make editor focused.  Maybe WM quirk?
		#self.focus()
		return True
	def focus_viewer(self):
		self.focus()
		return False
	def search_next(self, text, cs, word, frm='anchor'):
		"""Find next occurrence of text, starting with frm.
		frm is 'cursor', 'start', 'end' or 'anchor' (default).
		Anchor means the current selection (if any) else the cursor.
		Iff found:
			Updates self.search_start, self.search_end.
			Sets the selection.
			Returns True.
		Else:
			Return False.
		"""
		#print "search next", text, "in", self.get_filename_str(), "from", frm
		iter = self.get_search_from(frm, True)
		flags = 0 if cs else Gtk.TextSearchFlags.CASE_INSENSITIVE
		return self.show_found(iter.forward_search(text, flags, None))
	def search_prev(self, text, cs, word, frm='anchor'):
		#print "search prev", text, "in", self.get_filename_str(), "from", frm
		iter = self.get_search_from(frm, False)
		flags = 0 if cs else Gtk.TextSearchFlags.CASE_INSENSITIVE
		return self.show_found(iter.backward_search(text, flags, None))
	def replace(self, text):
		"""Replace currently selected text with replacement text.
		"""
		if text is None:
			return
		x = self.buffer_gm.get_selection_bounds()
		if x == ():
			return
		#FIXME: seems inefficient, but there's no 'replace at' method.
		self.buffer_gm.delete(x[0], x[1])
		self.buffer_gm.insert_at_cursor(text)
		
		
class FileInfo(object):
	def __init__(self, filename, tabs=[], maintab=None):
		self.filename = filename
		self.tabs = tabs
		self.maintab = maintab
	def get_tabs(self):
		return self.tabs
	def get_maintab(self):
		return self.maintab
	def add_tab(self, tab):
		if tab not in self.tabs:
			self.tabs.append(tab)
	def remove_tab(self, tab):
		self.tabs = [t for t in self.tabs if t != tab]
		if self.maintab == tab:
			self.maintab = self.tabs[0] if len(self.tabs) else None
	def is_empty(self):
		return len(self.tabs) < 1
		
class TabManager(object):
	"""Class to manage the set of tabs (in the GtkNotebook) which contain
	all our open files.  Each tab is represented by a Tab object
	"""
	def __init__(self, ui):
		self.ui = ui
		self.tabs = []
		self.rotabs = {}    # mapped by identifier
		self.files = {}     # map filename to FileInfo (i.e. set of tabs)
		self.top_filename = None
		self.ct = None      # Current tab - None if project settings tab.
		
		self.lang_manager = GtkSource.LanguageManager()
		#lp = self.lang_manager.get_search_path()
		#lp.append("/home/steve/gm215/src")  #FIXME - temp for testing
		#self.lang_manager.set_search_path(lp)
		
		self.font_desc = Pango.FontDescription('Monospace 10')
		self.exec_attrs = GtkSource.MarkAttributes()
		self.exec_attrs.set_background(Gdk.RGBA(0.8,1.,0.8))
		self.err_attrs = GtkSource.MarkAttributes()
		self.err_attrs.set_background(Gdk.RGBA(1.,0.9,0.4))
		self.bkpt_attrs = GtkSource.MarkAttributes()
		pixbuf = Gtk.IconTheme.get_default().load_icon('gtk-stop', 16, 0)
		self.bkpt_attrs.set_pixbuf(pixbuf)
		
		self.add_filters(self.ui.filesave)
		self.add_filters(self.ui.fileopen)
	def get_tab_from_filename(self, filename):
		if filename not in self.files:
			return None
		return self.files[filename].get_maintab()
	def get_ro_tab(self, ident, create=False, lang="geckomotionlist"):
		if ident not in self.rotabs:
			if create:
				return self.new_tab(read_only=ident, lang=lang)
			return None
		return self.rotabs[ident][0]
	def new_tab(self, filename=None, placeholder=False, read_only=False, lang="geckomotion"):
		"""Create and return a new tab with untitled content, or load given file.
		If file is specified, we check if it is already in another tab.  If so,
		then a new view is created to the existing buffer.
		"""
		t = self.get_tab_from_filename(filename)
		if t is not None and t.placeholder:
			# Opening a file we already have as a placeholder tab, so just make the tab visible.
			t.show()
			nt = t
		else:
			nt = Tab(self, filename, False, t, placeholder=placeholder, read_only=read_only, lang=lang)
			if self.ct is None:
				self.ct = nt
			if read_only:
				self._add_ro_tab(nt)
			else:
				self._add_tab_to_fileinfo(nt)
			self.tabs.append(nt)
			if filename is not None and t is None:
				nt.open_file(filename)
		print("New tab file=%s shared=%s placeholder=%s ro=%s" % (filename if filename is not None else "<>", \
					"yes" if t is not None else "no", "yes" if placeholder else "no", \
					read_only if bool(read_only) else "no"))
		return nt
	def get_tab(self, filename, open=False):
		"""Return existing tab open for specified file name.  If not already open,
		create a placeholder and open the file in it.  If file cannot be opened,
		return None, else return the old or new tab.
		"""
		t = self.get_tab_from_filename(filename)
		if t is not None:
			return t
		if not open:
			return None
		nt = self.new_tab(filename, True)
		return nt
	def tab_is_closed(self, tab):
		"""Tab has been closed (it already saved data if necessary).
		Keep our structures in sync, and update notepad.
		"""
		self.ui.notebook.remove_page(tab.get_page_index())
		self.tabs = [t for t in self.tabs if t != tab]
		self._remove_tab_from_fileinfo(tab)
		self._remove_ro_tab(tab)
	def set_filename(self, tab, filename):
		# Filename changed (e.g. load or saveas)
		self._remove_tab_from_fileinfo(tab)
		tab.set_filename(filename)
		self._add_tab_to_fileinfo(tab)
	def get_fileinfo(self, filename):
		if filename is None:
			return None
		if filename in self.files:
			return self.files[filename]
		return None
	def _add_tab_to_fileinfo(self, tab):
		filename = tab.filename
		if filename is not None:
			if filename not in self.files:
				# first tab for this file
				self.files[filename] = FileInfo(filename, [tab], tab) 
			else:
				modeltab = self.files[filename].tabs[0]
				self.files[filename].add_tab(tab)
				tab.share_tab(modeltab) # Share buffer if not first
	def _remove_tab_from_fileinfo(self, tab):
		fi = self.get_fileinfo(tab.filename)
		if fi is not None:
			fi.remove_tab(tab)
			if fi.is_empty():
				del self.files[tab.filename]
	def _add_ro_tab(self, tab):
		ident = tab.get_ident()
		if ident is not None:
			if ident not in self.rotabs:
				self.rotabs[ident] = [tab]
			else:
				self.rotabs[ident].append(tab)
	def _remove_ro_tab(self, tab):
		ident = tab.get_ident()
		if ident is not None and ident in self.rotabs:
			self.rotabs[ident] = [t for t in self.rotabs[ident] if t != tab]
			if len(self.rotabs[ident]) == 0:
				del self.rotabs[ident]  
	def focus(self, tab):
		"""Bring specified tab to front"""
		self.ui.notebook.set_current_page(self.ui.notebook.page_num(tab.sw))
		self.ui.mainwindow.grab_focus()
	def remove_all_err_marks(self):
		for t in self.tabs:
			t.remove_err_mark()
	   
	def set_top_file(self, filename):
		"""Set all tabs (if any) editing filename to be the top-level file
		for this project.  This changes the icon in the tab to reflect the
		top-level status.
		"""
		for t in self.tabs:
			t.set_top(False)
		self.top_filename = filename
		name = "<none>"
		if filename in self.files:
			for t in self.files[filename].get_tabs():
				t.set_top(True)
				name = filename
		self.ui.builder.get_object("settings_top_level").set_label(name)
	def set_top_tab(self):
		# Set current tab to be top-level file
		if not self.ct:
			return
		if self.ct.filename in self.files:
			self.set_top_file(self.ct.filename)
			return
		# ct filename is None... (untitled)
		for t in self.tabs:
			t.set_top(False)
		self.top_filename = self.ct.filename
		self.ct.set_top(True)
	def get_top_tab(self):
		for t in self.tabs:
			if t.is_top:
				return t
		return None
	def get_top_file(self):
		tt = self.get_top_tab()
		if tt is not None:
			return tt.filename
		return None
	def buf(self):
		# Return current visible buffer
		return None if self.ct is None else self.ct.buf()
	def is_untitled(self):
		return None if self.ct is None else self.ct.filename is None
	def get_filename(self):
		# Return current tab filename
		return None if self.ct is None else self.ct.filename
	def get_file_list(self, include_placeholders=False):
		# Return list of open files (one instance only for multiple tabs with same file)
		if include_placeholders:
			return list(self.files.keys())
		return [fn for fn in list(self.files.keys()) if not self.get_tab_from_filename(fn).placeholder]
	def save_all_modified(self):
		for t in self.tabs:
			if t.get_ident() is not None:
				continue
			if t.filename is None or self.files[t.filename].get_maintab() == t:
				if not t.discard_or_save_current_file(self.ui.filesave):
					return False    # User cancelled
		return True
	def file_new(self):
		# Create a new untitled tab
		self.new_tab()
	def file_open(self):
		resp = self.ui.fileopen.run()
		try:
			if resp == Gtk.ResponseType.OK:
				filename = self.ui.fileopen.get_filename()
				if filename:
					self.new_tab(filename)
		finally:
			self.ui.fileopen.hide()
	def file_save(self):
		if self.ct is None:
			# Settings panel
			return
		if self.ct.is_untitled():
			return self.file_saveas()
		self.ct.store_file(self.buf(), self.ct.filename)
	def file_saveas(self):
		if self.ct is None:
			# Settings panel
			return
		self.ct.file_saveas(self.ui.filesave)

	def add_filters(self, dialog):
		filter_gm = Gtk.FileFilter()
		filter_gm.set_name("GeckoMotion files")
		filter_gm.add_pattern("*.gm")
		dialog.add_filter(filter_gm)

		filter_text = Gtk.FileFilter()
		filter_text.set_name("Text files")
		filter_text.add_mime_type("text/plain")
		dialog.add_filter(filter_text)

		filter_py = Gtk.FileFilter()
		filter_py.set_name("Python files")
		filter_py.add_mime_type("text/x-python")
		dialog.add_filter(filter_py)

		filter_any = Gtk.FileFilter()
		filter_any.set_name("Any files")
		filter_any.add_pattern("*")
		dialog.add_filter(filter_any)
		
	def close_all_tabs(self):
		for t in self.tabs:
			t.close(self.ui.filesave)
		self.ui.update()

	# Callbacks...
	def tab_closed(self, tab_label, tab):
		print("tab closed")
		tab.close(self.ui.filesave)
		self.ui.update()
	def buf_notify(self, buff, *args):
		self.ui.update()
	def mod_asm(self, buff, tab):
		#TODO: check that file is actually involved in assembly
		self.ui.mod_asm(True)
	def tab_switched(self, page):
		self.ct = None
		for t in self.tabs:
			#print t.sw
			if t.sw == page:
				self.ct = t
				self.ui.update()
				return
		#print "No tab found!"
		self.ui.update()
		# OK, selected project settings tab (ct is None)
	def get_settings_tab(self):
		return self.ui.notebook.get_children()[0]

class UI:
	def __init__(self, persist=None, devices=None, gladefile=None, console=False):
		if not console:
			self.redirect_log()
		self.p = Persistent() if persist is None else persist
		# Default set of target devices.  (All must subclass Devices class)
		self.devices = [RS485Devices()] if devices is None else [devices]
		gladefile = os.path.join(_gladedir, "gm.glade") if gladefile is None else gladefile
		print("gladefile: " + gladefile)
		self.pp = None      # Project-specific settings
		self.devs_id = 0
		self.devs = self.devices[self.devs_id]
		builder = Gtk.Builder()
		self.builder = builder
		builder.add_from_file(gladefile)
		self._init_buttons()
		self.mainwindow = builder.get_object("window1")
		self.pane = builder.get_object("paned1")
		self.about = builder.get_object("aboutdialog1")
		self.fileopen = builder.get_object("filechooserdialog1")
		self.filesave = builder.get_object("filechooserdialog2")
		self.libsearch = builder.get_object("filechooserdialog3")
		self.blsel = builder.get_object("filechooserdialog4")
		self.projbase = builder.get_object("dialog1")
		self.log_window = builder.get_object("log_window")
		self.debug_stuff = builder.get_object("debug_stuff")
		self.status_window = builder.get_object("status_window")
		self.input_window = builder.get_object("input_window")
		self.search_window = builder.get_object("search_window")
		self.flash_dialog = builder.get_object("flash_dialog")
		self.dipsw_dialog = builder.get_object("dipsw_dialog")
		self.firmware_dialog = builder.get_object("firmware_dialog")
		# Make dialogs hide instead of close when X button hit...
		hideit = lambda w, e: w.hide() or True
		self.about.connect('delete-event', hideit)
		self.fileopen.connect('delete-event', hideit)
		self.filesave.connect('delete-event', hideit)
		self.libsearch.connect('delete-event', hideit)
		self.blsel.connect('delete-event', hideit)
		self.projbase.connect('delete-event', hideit)
		self.log_window.connect('delete-event', hideit)
		self.status_window.connect('delete-event', hideit)
		self.input_window.connect('delete-event', hideit)
		self.search_window.connect('delete-event', hideit)
		self.flash_dialog.connect('delete-event', hideit)
		self.dipsw_dialog.connect('delete-event', hideit)
		self.firmware_dialog.connect('delete-event', hideit)

		filter_gm = Gtk.FileFilter()
		filter_gm.set_name("EXE files")
		filter_gm.add_pattern("*.exe")
		self.blsel.add_filter(filter_gm)
		filter_py = Gtk.FileFilter()
		filter_py.set_name("Python files")
		filter_py.add_mime_type("text/x-python")
		self.blsel.add_filter(filter_py)
		filter_any = Gtk.FileFilter()
		filter_any.set_name("Any files")
		filter_any.add_pattern("*")
		self.blsel.add_filter(filter_any)

		self.serial_control_lock = Lock()
		
		self.about.set_version(_version)
		self.about.set_title("About GeckoMotion version "+_version)
		
		self.pb_chooser = builder.get_object("filechooserwidget1")
		self.cproj = builder.get_object("settings_current_project")
		self.cprod_h_id = None

		self.pause_toggle = builder.get_object("tool_device_pause")
		self.notebook = builder.get_object("notebook")
		pixbuf = GdkPixbuf.Pixbuf.new_from_file(os.path.join(_fulldir(_imagedir), "gecko.jpg"))
		self.about.set_logo(pixbuf)
		pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(os.path.join(_fulldir(_icondir), "Solo_Gecko.svg"), 48, 48, False)
		self.mainwindow.set_icon(pixbuf)
		self.led_dict = {}
		for n, clr in enumerate(['black', 'blue', 'green', 'orange', 'purple', 'red', 'yellow']):
			pixbuf = GdkPixbuf.Pixbuf.new_from_file(os.path.join(_fulldir(_icondir), "ls_%s_24x24.png"%clr))
			if n==0:
				offled = pixbuf # Off state is always black
			self.led_dict[clr] = (offled, pixbuf)
		#self.dipsw = [GdkPixbuf.Pixbuf.new_from_file(os.path.join(_fulldir(_icondir), "dipsw-off.png")),
		#              GdkPixbuf.Pixbuf.new_from_file(os.path.join(_fulldir(_icondir), "dipsw-on.png")),
		#              GdkPixbuf.Pixbuf.new_from_file(os.path.join(_fulldir(_icondir), "dipsw-either.png"))]
		self.dipsw = [GdkPixbuf.Pixbuf.new_from_file_at_size(os.path.join(_fulldir(_icondir), "dip-off.svg"), 120,120),
					  GdkPixbuf.Pixbuf.new_from_file_at_size(os.path.join(_fulldir(_icondir), "dip-on.svg"), 120,120),
					  GdkPixbuf.Pixbuf.new_from_file_at_size(os.path.join(_fulldir(_icondir), "dip-either.svg"), 120,120)]
		self.dip = []
		for n in range(10):
			img = builder.get_object("dip%d" % n)
			self.dip.append(img)
			img.set_from_pixbuf(self.dipsw[0])
		self.dipsw_notebook = builder.get_object("dipsw_notebook")
		self.phase_current_adjustment = builder.get_object("phase_current_adjustment")
		self.dipsw_self_test = builder.get_object("dipsw_self_test")
		self.dipsw_self_test.set_active(0)
		self.dipsw_step_resolution = builder.get_object("dipsw_step_resolution")
		self.dipsw_step_resolution.set_active(0)
		self.dipsw_enab_standby = builder.get_object("dipsw_enab_standby")
		self.dipsw_frame_size = builder.get_object("dipsw_frame_size")
		self.dipsw_frame_size.set_active(0)
		self.dipsw_axis = builder.get_object("dipsw_axis")
		self.dipsw_axis.set_active(0)
		self.dipsw_program = builder.get_object("dipsw_program")
		
		self.firmware_entry = builder.get_object("bootloader_entry")
		
		self.err_list_scroller = builder.get_object("scrolledwindow1")
		self.err_list_view = builder.get_object("treeview2")
		self.err_list = builder.get_object("liststore1")
		renderer = Gtk.CellRendererText()
		column = Gtk.TreeViewColumn("File", renderer, text=0)
		self.err_list_view.append_column(column)
		renderer = Gtk.CellRendererText()
		column = Gtk.TreeViewColumn("Line", renderer, text=1)
		self.err_list_view.append_column(column)
		renderer = Gtk.CellRendererText()
		column = Gtk.TreeViewColumn("Message", renderer, text=2)
		self.err_list_view.append_column(column)
		self.err_mark = None
		self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

		self.status_view = builder.get_object("status_treeview")
		self.status_list = builder.get_object("status_liststore")
		renderer = Gtk.CellRendererText()
		column = Gtk.TreeViewColumn("Axis", renderer, text=1)
		self.status_view.append_column(column)
		for n, col in enumerate(['i1','i2','i3','o1','o2','o3','fe','pe']):
			renderer = Gtk.CellRendererPixbuf()
			column = Gtk.TreeViewColumn(col, renderer, pixbuf=2+n)
			self.status_view.append_column(column)
		renderer = Gtk.CellRendererText()
		column = Gtk.TreeViewColumn("Position", renderer, text=10)
		column.set_min_width(100)
		self.status_view.append_column(column)
		renderer = Gtk.CellRendererText()
		column = Gtk.TreeViewColumn("Velocity", renderer, text=11)
		self.status_view.append_column(column)
		renderer = Gtk.CellRendererPixbuf()
		column = Gtk.TreeViewColumn("Bsy", renderer, pixbuf=12)
		self.status_view.append_column(column)
		for n, axis in enumerate(['X','Y','Z','W']):
			r = [n, axis] + [self.led_dict['green'][0]]*3 + [self.led_dict['yellow'][0]]*3 + \
					[self.led_dict['red'][0]]*2 + [100000, -1000, self.led_dict['orange'][0]]
			self.status_list.append(r)
			
		isg = self.input_sim_grid = builder.get_object("input_sim_grid")
		header_row = 0
		toggle_row = 1
		momentary_row = 2
		result_row = 4
		self.insim_map = {}
		self.mom_state = {}
		for axis in range(4):
			# Axis header
			isg.attach(Gtk.Label("XYZW"[axis]), axis*4+2, header_row, 3, 1)
			for i in range(3):
				col = axis*4 + i + 2
				lab = str(i+1)
				key = (axis, i)
				# Toggle
				tb = Gtk.ToggleButton(lab)
				isg.attach(tb, col, toggle_row, 1, 1)
				tb.connect('toggled', self.input_sim_toggled)
				tb.sim_input = key
				# Momentary
				b = Gtk.Button(lab)
				isg.attach(b, col, momentary_row, 1, 1)
				b.connect('pressed', self.input_sim_pressed)
				b.connect('released', self.input_sim_released)
				b.sim_input = key
				# Result
				im = Gtk.Image.new_from_pixbuf(self.led_dict['green'][0])
				isg.attach(im, col, result_row, 1, 1)
				# Allow us to look up widgets
				self.insim_map[key] = (tb, b, im)
				self.mom_state[key] = False
		

		self.replace_container = builder.get_object("replace_container")
		self.enable_replace = builder.get_object("enable_replace")
		self.search_text = builder.get_object("search_text")
		self.replace_text = builder.get_object("replace_text")
		self.search_ignorecase = builder.get_object("search_ignorecase")
		self.search_word = builder.get_object("search_word")

		self.flash_label = builder.get_object("flash_label")
		self.flash_progress = builder.get_object("flash_progress")
		self.flash_ok_button = builder.get_object("flash_ok_button")
		self.flash_cancel_button = builder.get_object("flash_cancel_button")
		self.dev_status_button = builder.get_object("tool_device_status")

		builder.connect_signals(self)

		self.tab_mgr = TabManager(self)
		self.settings = self.tab_mgr.get_settings_tab()
		self.notebook.set_tab_label_text(self.settings, "Settings")

		self.libsearch_list_view = builder.get_object("treeview3")
		self.libsearch_list = builder.get_object("liststore2")
		renderer = Gtk.CellRendererText()
		renderer.connect('edited', self.libsearch_edited)
		column = Gtk.TreeViewColumn("Path", renderer, text=0, editable=1)
		self.libsearch_list_view.append_column(column)
		
		#self.settings_logging = builder.get_object("settings_logging")
		#self.settings_logging.connect('notify::active', self.settings_changed)
		self.settings_verbose = builder.get_object("settings_verbose")
		self.settings_verbose.connect('notify::active', self.settings_changed)
		self.settings_target = builder.get_object("settings_target")
		self.settings_serport = builder.get_object("settings_serport")
		self.settings_serport.connect('changed', self.serport_changed)
		self.settings_serport.connect('notify::popup-shown', self.serport_popup)
		self.settings_serport.get_child().connect('activate', self.serport_add)
		self.log_time = time.time()
		self.log_crlf = True
		self.log_text = builder.get_object("log_text")
		self.log_auto_scroll = builder.get_object("log_auto_scroll")
		self.log_scroller = builder.get_object("log_scrolledwindow")
		self.log_view = builder.get_object("log_textview")
		self.log_view.modify_font(Pango.FontDescription('Monospace 8'))
		self.log_mark = self.log_text.create_mark("end", self.log_text.get_end_iter(), False)
		self.poll_rate = builder.get_object("poll_rate")    # adjustment
		self.char_delay = builder.get_object("char_delay")    # adjustment
		self.cmd_delay = builder.get_object("cmd_delay")    # adjustment
		self.poll_checkbutton = builder.get_object("poll_checkbutton")
		#self.trace_checkbutton = builder.get_object("trace_checkbutton")
		for d in self.devices:
			d.set_ui(self)
		
		mdl = self.settings_target.get_model()
		mdl.clear()
		for d in self.devices:
			self.settings_target.append_text(d.target_name())
		
		print(_app_fullname, "starting.")
		
		self.dipsw_update()
		self.load_persistent()

		# state variables
		self._connected = False

		# create thread
		self.geckomotion_serial_thread = Thread(target=self.internal_serial_thread)
		self.geckomotion_serial_thread.daemon = False
		
		self.serial_thread_shutdown_signal = False
		
		self.geckomotion_serial_thread.start()



	def internal_serial_thread(self):
		""" Internal function which ticks the motor controller comms code.  Updates status, and sends the next command if applicable."""
		while not self.serial_thread_shutdown_signal:
			
			# if the serial cable is unplugged, then the GM library will set its internal serial port object to None
			self._connected = hasattr(self.devs, 'f') and (self.devs.f != None)
			
			if self._connected:
				
				self.serial_control_lock.acquire() 
				
				try:
					# force query of all devices' state (not calling this is why the old GM GUI tends to freeze up)
					self.devs._send_qlong()
					
					# send queued serial data if needed
					self.devs.idle_func()
				except KeyboardInterrupt:
					raise
				except Exception as ex:
					traceback.print_exc()
					pass
				
				self.serial_control_lock.release()
				
			# update every 20ms
			time.sleep(.02)
		
	def buf(self):
		return self.tab_mgr.buf()
	def buf_modified(self):
		if self.tab_mgr.ct is None:
			# TODO - could use this to check setting modified
			return False
		return self.buf().get_modified()
	def mod_asm(self, yes):
		self.serial_control_lock.acquire()
		self.devs.mod_asm(yes)
		self.serial_control_lock.release()
	def run(self):
		self.mainwindow.show_all()
		self.hide_error_list()
		if "--debug" in sys.argv:
			self.debug_stuff.show()
		else:
			self.debug_stuff.hide()
		Gtk.main()
		
	def show_error_list(self):
		self.err_list_scroller.set_visible(True)
	def hide_error_list(self):
		self.err_list_scroller.set_visible(False)
	def clear_error_list(self):
		self.err_list.clear()
	def add_error_list(self, filename, line_num, message, err_index):
		# filename may be "" for top-level unnamed tab. 
		if filename is None:
			filename = ""
		return self.err_list.append([filename, line_num, message, err_index])
	def highlight_error(self, error_index):
		code = self.devs.code
		i = code.get_error_iter(error_index)
		tab = code.get_error_tab(error_index)
		if i is not None:
			tab.focus()
			tab.move_err_mark(i, True)
	def unhighlight_error(self):
		# Turn off error in all tabs
		self.tab_mgr.remove_all_err_marks()

	def add_libsearch_list(self, dirname, editable=False):
		if dirname is None:
			dirname = ""
		# returns iter
		return self.libsearch_list.append([dirname, editable])
		
		
	def _init_buttons(self):
		"""List of names of menu buttons (i.e. the
		identifier in the glade XML file, without the menu_ or tool_ prefix)
		"""
		blist = [
			'file_new',
			'file_open',
			'file_save',
			'file_saveas',
			'edit_cut',
			'edit_copy',
			'edit_paste',
			'edit_undo',
			'edit_redo',
			'source_compile',
			'control_breakpoint',
			'control_clear',
			'control_start',
			'control_go_csr',
			'control_step',
			'control_next',
			'control_run',
			'control_stop',
			'device_pause', # toggle
			'device_cancel',
			'device_estop',
			'device_flash',
		]
		prefixes = [ 'menu_', 'tool_' ]
		self.uinfo = {}

		#import pdb; pdb.set_trace();
		
		for btn in blist:
			sens = getattr(self, 'sens_'+btn, None)
			if sens is not None: 
				self.uinfo[btn] = {
					'sens' : sens,
					'objs' : [self.builder.get_object(p+btn) for p in prefixes]
					}
	def sens_file_save(self):
		return self.tab_mgr.ct is not None and self.buf_modified()
	def sens_file_saveas(self):
		return self.tab_mgr.ct is not None
	def sens_edit_cut(self):
		#print self.buf().get_selection_bounds()
		return False if self.tab_mgr.ct is None else self.buf().get_has_selection()
	def sens_edit_copy(self):
		return False if self.tab_mgr.ct is None else self.buf().get_has_selection()
	def sens_edit_paste(self):
		return self.tab_mgr.ct is not None
	def sens_edit_undo(self):
		return False if self.tab_mgr.ct is None else self.buf().can_undo()
	def sens_edit_redo(self):
		return False if self.tab_mgr.ct is None else self.buf().can_redo()
	def sens_control_breakpoint(self):
		return False if self.tab_mgr.ct is None else self.devs.can_breakpoint()
	def sens_control_start(self):
		return self.devs.is_ready()
	def sens_control_go_csr(self):
		return self.devs.is_ready()
	def sens_control_step(self):
		return self.devs.can_step()
	def sens_control_next(self):
		return self.devs.can_step()
	def sens_control_run(self):
		return self.devs.can_step()
	def sens_control_stop(self):
		return self.devs.is_connected() and not self.devs.is_ready()
	def sens_device_pause(self):
		paused = self.pause_toggle.get_active()
		if paused:
			# toggle is for 'resume' action
			return self.devs.is_connected() and self.devs.is_paused()
		else:
			# toggle is for 'pause'
			return self.devs.is_connected() and not self.devs.is_paused()
	def sens_device_cancel(self):
		return self.devs.is_paused()
	def sens_device_estop(self):
		return self.devs.is_connected()
	def sens_device_flash(self):
		return self.devs.can_flash()
	def sens_source_compile(self):
		return self.devs.can_assemble()
	def update(self):
		"""Work-around for inability to use action groups.
		Call after each application change of state which may change widget
		sensitivity.
		"""
		for k, v in list(self.uinfo.items()):
			# k is basic button name, v is dict with keys 'sens' etc.
			sens = True if v['sens'] is None else v['sens']()
			for btn in v['objs']:
				if btn:
					btn.set_sensitive(sens)

	def update_title(self):
		filename = self.tab_mgr.get_filename()
		if filename is None:
			filename = "<untitled>"
		self.mainwindow.set_title(filename + " - GeckoMotion")
	

	def win_del(self, *args):
		if not self.tab_mgr.save_all_modified():
			# User cancelled quit
			return True
		self.save_persistent()

		self.serial_control_lock.acquire()
		self.devs.disconnect()
		self.serial_control_lock.release()

		# shut down andjoin background thread
		self.serial_thread_shutdown_signal = True
		self.geckomotion_serial_thread.join()

		self.unredirect_log()
		Gtk.main_quit(*args)
		
	def get_project_settings(self, p):
		# Get current project settings (from widgets) and put in given PersistentProject 'p'.
		p.filename = None if self.tab_mgr.ct is None else self.tab_mgr.ct.filename     # Current filename
		p.openfiles = self.tab_mgr.get_file_list()  # All tabs
		if not p.filename:
			p.filename = p.openfiles[0] if len(p.openfiles) else None
		p.top_file = self.tab_mgr.get_top_file()
		p.libsearch = [row[0] for row in self.libsearch_list]
		p.logging = self.get_logging()
		p.verbose = self.get_verbose()
		p.target = self.get_target()
		p.devname = self.get_serport_port()
		p.devtext = self.get_serport_text()
		
		
	def save_project_persistent(self):
		# Get current settings and put in persistent object, then save it.
		self.get_project_settings(self.pp)
		self.pp.save()
		
	def save_persistent(self):
		# Called once at end to get and save all persistent data
		self.save_project_persistent()
		p = self.p
		#w, h = self.mainwindow.get_size()
		#p.window1['width'] = w
		#p.window1['height'] = h
		p.pane_position = self.pane.get_position()
		p.log_auto_scroll = self.log_auto_scroll.get_active()
		p.inter_char_delay = self.get_resp_timeout()
		p.cmd_delay = self.get_cmd_delay()
		p.enable_poll = self.get_polling()
		#p.enable_trace = self.get_trace()
		p.poll_rate = self.get_poll_rate()
		p.enable_replace = self.enable_replace.get_active()
		p.bootloader = self.firmware_entry.get_text()
		for dlg in ['mainwindow', 'log_window', 'status_window', 'search_window', 'input_window']:
			widget = getattr(self, dlg)
			w, h = widget.get_size()
			x, y = widget.get_position()
			vis = False
			state = 0
			try:
				vis = int(widget.get_window().is_visible())
				state = int(widget.get_window().get_state())
			except:
				pass
			m = bool(state & Gdk.WindowState.MAXIMIZED)
			i = bool(state & Gdk.WindowState.ICONIFIED)
			try:
				d = getattr(p, dlg, {})
			except KeyError:
				d = {}
			d['state'] = state
			d['maximized'] = m
			d['iconified'] = i
			d['visible'] = vis
			if not m and not i:
				d['width'] = w
				d['height'] = h
				d['x'] = x
				d['y'] = y
			setattr(p, dlg, d)
		p.save()
	
	def load_project_persistent(self, folder, prompt=False):
		# Action project settings for specified project folder name.  May be None
		# to use the default project.
		# First, save existing...
		if prompt:
			if not self.tab_mgr.save_all_modified():
				# User cancelled project change
				return False
		if self.pp is not None:
			self.save_project_persistent()
		self.tab_mgr.close_all_tabs()
		if folder is None:
			pp = self.p.pp
		else:
			pp = PersistentProject(folder, self.p)
		self.pp = pp
		if pp.load():
			# There was a backing file
			for f in self.pp.openfiles:
				tab = self.tab_mgr.new_tab(f)
				if f == self.pp.filename:
					tab.focus()
			self.tab_mgr.set_top_file(self.pp.top_file)
			self.libsearch_list.clear()
			for ls in self.pp.libsearch:
				self.add_libsearch_list(ls)
			self.set_logging(self.pp.logging)
			self.set_verbose(self.pp.verbose)
			self.set_target(self.pp.target)
		else:
			# Loaded new project (no pickle available).  Keep current
			# project state.
			pass
		self.p.project = folder
		# Remember all unique projects in main persistent.  Dict item value is last access time.
		self.p.all_projects[folder] = time.time()
		self.populate_cproj()
		try:
			self.set_device(self.pp.target, connect=False)
		except KeyError:
			pass
		try:
			self.set_serport(self.pp.devname, self.pp.devtext)
		except KeyError:
			pass
		self.mainwindow.set_title("%s - GeckoMotion" % (os.path.basename(folder),) if folder else "GeckoMotion")
		return True
	
	def populate_cproj(self):
		# Fill in the project selection combo box.  Each time, the list is ordered
		# from most recent (i.e. current) to oldest, according to the remembered projects
		# in self.p.all_projects.
		#NOTE: need to turn off the 'changed' signal since our callback would otherwise recurse.
		a = sorted(list(self.p.all_projects.items()), key=lambda x:x[1], reverse=True)
		if self.cprod_h_id is not None:
			self.cproj.disconnect(self.cprod_h_id)
		self.cproj.remove_all()
		cur = 0
		for n, aa in enumerate(a):
			folder, timestamp = aa
			self.cproj.append(str(n), folder if folder else "<none>")
			if folder == self.p.project:
				cur = n
		self.cproj.set_active_id(str(cur))
		self.cprod_h_id = self.cproj.connect('changed', self.sel_recent_project)
			
	def load_persistent(self):
		# Called once at start-up to action all persistent data
		self.p.load()
		if self.p.pp is None:
			self.p.pp = PersistentProject(None, self.p) # Create a default project.
		#print "Setting w,h", self.p.window1['width'], self.p.window1['height']
		#self.mainwindow.set_default_size(self.p.window1['width'], self.p.window1['height'])
		p = self.p
		for dlg in ['mainwindow', 'log_window', 'status_window', 'search_window', 'input_window']:
			try:
				d = getattr(p, dlg, {})
			except KeyError:
				d = {}
			w = d.get('width', 1000)
			h = d.get('height', 600)
			x = d.get('x', 1000)
			y = d.get('y', 1000)
			max = d.get('maximized', False)
			vis = d.get('visible', dlg == "mainwindow")
			widget = getattr(self, dlg)
			widget.set_default_size(w, h)
			widget.move(x, y)
			if max:
				widget.maximize()
			if vis and dlg not in ['mainwindow']:
				widget.show_all()
		self.pane.set_position(self.p.pane_position)
		self.log_auto_scroll.set_active(self.p.log_auto_scroll)
		self.poll_checkbutton.set_active(self.p.enable_poll)
		#self.trace_checkbutton.set_active(self.p.enable_trace)
		self.poll_rate.set_value(self.p.poll_rate * 1000.)
		self.char_delay.set_value(self.p.inter_char_delay * 1000.)
		self.cmd_delay.set_value(self.p.cmd_delay * 1000.)
		self.enable_replace.set_active(self.p.enable_replace)
		self.firmware_entry.set_text(self.p.bootloader)
		self.set_show_replace()
		self.load_project_persistent(self.p.project)
		
	def tab_removed(self, notebook, page, pagenum):
		#print "Tab removed", pagenum
		pagenum = notebook.get_current_page()
		if pagenum >= 0:
			self.tab_mgr.tab_switched(notebook.get_children()[pagenum])
	def tab_switched(self, notebook, page, pagenum):
		#print "Tab switch", pagenum
		self.tab_mgr.tab_switched(page)

	def top_clicked(self, button):
		print("Set Top")
		self.tab_mgr.set_top_tab()

	def clear_clicked(self, button):
		print("Clear Bkpt")
		self.serial_control_lock.acquire()
		self.devs.clear_all_breakpoints()
		self.serial_control_lock.release()
		self.update()

	def breakpoint_clicked(self, button):
		print("Bkpt")
		if self.tab_mgr.ct is None:
			return
		self.serial_control_lock.acquire()
		self.devs.toggle_breakpoint_at_cursor(self.tab_mgr.ct)
		self.serial_control_lock.release()
		self.update()

	def start_clicked(self, button):
		print("Start")
		self.serial_control_lock.acquire()
		self.devs.restart_program()
		self.serial_control_lock.release()
		self.update()

	def go_csr_clicked(self, button):
		print("GoCsr")
		if self.tab_mgr.ct is None:
			return
		self.serial_control_lock.acquire()
		self.devs.set_exec_at_cursor(self.tab_mgr.ct)
		self.serial_control_lock.release()
		self.update()

	def step_clicked(self, button):
		print("Step")
		self.serial_control_lock.acquire()
		self.devs.single_step()
		self.serial_control_lock.release()
		self.update()

	def next_clicked(self, button):
		print("Next")
		self.serial_control_lock.acquire()
		self.devs.step_to_next()
		self.serial_control_lock.release()
		self.update()

	def run_clicked(self, button):
		print("Run")
		self.serial_control_lock.acquire()
		self.devs.run_until_break()
		self.serial_control_lock.release()
		self.update()
		
	def stop_clicked(self, button):
		print("Stop")
		self.serial_control_lock.acquire()
		self.devs.stop()
		self.serial_control_lock.release()
		self.update()

	def pause_toggled(self, button):
		print("Pause", button.get_active())
		self.serial_control_lock.acquire()
		if button.get_active():
			self.devs.pause()
		else:
			self.devs.resume()
		self.serial_control_lock.release()
		self.update()

	def cancel_clicked(self, button):
		print("Cancel")
		self.serial_control_lock.acquire()
		self.devs.cancel()
		self.serial_control_lock.release()
		# Force pause toggle to be inactive
		self.pause_toggle.set_active(False)
		self.update()

	def estop_clicked(self, button):
		print("Estop")
		self.serial_control_lock.acquire()
		self.devs.estop()
		self.serial_control_lock.release()
		self.pause_toggle.set_active(False)
		self.update()

	def flash_clicked(self, button):
		print("Flash")
		self.serial_control_lock.acquire()
		self.flash_progress.set_fraction(0.)
		self.flash_label.set_text("Programming...")
		self.flash_dialog.show_all()
		self.flash_ok_button.set_sensitive(False)
		self.flash_cancel_button.set_sensitive(True)
		self.devs.flash()
		self.serial_control_lock.release()
		self.update()
	def flash_cancel_clicked(self, *args):
		self.serial_control_lock.acquire()
		self.devs.cancel_flash()
		self.serial_control_lock.release()
		self.flash_dialog.hide()
		self.update()
	def flash_ok_clicked(self, *args):
		self.flash_dialog.hide()
		self.update()
	def flash_done(self, completion_status_str):
		# Swap enabling of cancel and ok, so user can explicitly dismiss dialog.
		# Called from Devices object
		self.flash_label.set_text(completion_status_str)
		self.flash_ok_button.set_sensitive(True)
		self.flash_cancel_button.set_sensitive(False)
		self.flash_progress.set_fraction(1.)
		self.update()
	def set_flash_progress(self, addr, n):
		block = addr/64
		nblock = (n+63)/64
		if nblock:
			self.flash_progress.set_fraction(float(block)/nblock)
			self.flash_label.set_text("Programming block %d/%d" % (block, nblock))
		
	def connect_clicked(self, button):
		print("Connect")
		self.serial_control_lock.acquire()
		self.devs.reconnect(self.get_serport_port())
		self.serial_control_lock.release()
		self.update()
		
	def compile_clicked(self, button):
		print("Compile")
		top_tab = self.tab_mgr.get_top_tab()
		if not top_tab:
			top_tab = self.tab_mgr.ct   # current if no top specified
		if not top_tab:
			return
		self.get_project_settings(self.pp)
		self.serial_control_lock.acquire()
		self.devs.assemble(top_tab, self.pp)
		listing = self.tab_mgr.get_ro_tab("listing", True)
		if listing:
			print("Listing")
			self.devs.make_listing(listing)
		self.serial_control_lock.release()
		self.update()


	def file_new(self, *args):
		print("File->New")
		self.tab_mgr.file_new()
		
	def file_open(self, *args):
		print("File->Open")
		self.tab_mgr.file_open()
		
	def file_save(self, *args):
		print("File->Save")
		self.tab_mgr.file_save()
		
	def file_saveas(self, *args):
		print("File->SaveAs")
		self.tab_mgr.file_saveas()
		
	def file_firmware(self, *args):
		print("File->Firmware")
		# Invoke DS30 boot loader to program new GM215 firmware via RS485 connection.
		# Currently, this requires all devices other than the target device to be
		# physically disconnected.  The DS30 boot loader expects a pt-pt connection.
		self.firmware_dialog.show()
			
	def firmware_cancel_clicked(self, *args):
		print("BB Can")
		self.firmware_dialog.hide()
	def firmware_ok_clicked(self, *args):
		run = self.firmware_entry.get_text()
		print("BB OK", run)
		self.serial_control_lock.acquire()
		self.devs.disconnect()
		self.serial_control_lock.release()
		if sys.platform == 'win32':
			os.startfile(run)
		else:
			print("*** Not implemented on this platform ***")
		self.firmware_dialog.hide()
	def bootloader_browse_clicked(self, *args):
		print("BB")
		resp = self.blsel.run()
		try:
			if resp == Gtk.ResponseType.OK:
				filename = self.blsel.get_filename()
				if filename:
					self.firmware_entry.set_text(filename)
		finally:
			self.blsel.hide()
		
			
	def undo(self, *args):
		print("Edit->Undo")
		buffer = self.buf()
		if buffer.can_undo():
			buffer.undo()
	def redo(self, *args):
		print("Edit->Redo")
		buffer = self.buf()
		if buffer.can_redo():
			buffer.redo()
	
	   
	def cut(self, *args):
		print("Edit->Cut")
		self.buf().cut_clipboard(self.clipboard, True)
	def copy(self, *args):
		print("Edit->Copy")
		self.buf().copy_clipboard(self.clipboard)
	def paste(self, *args):
		print("Edit->Paste")
		self.buf().paste_clipboard(self.clipboard, None, True)

	def help_about(self, *args):
		self.about.run()

	def about_close(self, dlg):
		# This invoked if ESC key pressed
		pass

	def about_response(self, dlg, resp_id):
		dlg.hide()
		
	def err_sel(self, treesel, *args):
		#print "Err sel", treesel.get_selected()
		model, iter = treesel.get_selected()
		if iter:
			err_index = model[iter][3]
			self.highlight_error(err_index)
		else:
			self.unhighlight_error()
			
	def get_project_folder(self):
		#label = self.builder.get_object("settings_current_project").get_label()
		#if label == '' or label == '<none>':
		#    return None
		#return label
		return self.p.project
	
	def set_project_folder(self, folder):
		self.load_project_persistent(folder, prompt=True)
		
	def query_project_base(self):
		# Called when starting up without a persistent project_base.  Typically, only runs
		# the first time after new installation (unless cancelled from previous time).
		if sys.platform == 'win32':
			base = os.getcwd()   # should be {appdir}/projects
			if base is None:
				print("No cwd, falling back to user home")
				base = os.path.expanduser("~")
				name = os.path.join("geckomotion", "projects")
			else:
				base = os.path.dirname(base)
				name = "projects"
		else:
			base = os.path.expanduser("~")    # Linux doesn't normally define {appdir}
			name = os.path.join("geckomotion", "projects")
		print("Trying set_current_name:", name)
		self.pb_chooser.set_current_name(name)
		print("Suggested project base:", base)
		self.pb_chooser.set_current_folder(base)
		resp = self.projbase.run()
		try:
			if resp == 0:
				newname = self.pb_chooser.get_filename()
				print("Selecting", newname, "as default project base...")
				if newname is None or not os.access(newname, os.W_OK):
					if self.query_nonwritable_folder(newname, \
							exists = newname and os.access(newname, os.F_OK), \
							ftype = "project base") != Gtk.ResponseType.YES:
						print("...Failed")
						return False    # Should always do this
				print("...OK")
				self.p.project_base = newname
		finally:
			self.projbase.hide()
		return True
	
	def query_nonwritable_folder(self, folder, exists=False, ftype="project folder"):
		# Called when attempting to create new project in unwritable or non-existent folder.
		dialog = Gtk.MessageDialog(self.mainwindow, 0, Gtk.MessageType.ERROR,
			Gtk.ButtonsType.NONE, "New %s selection" % ftype)
		dialog.format_secondary_text("The selected %s\n%s\n%s." % \
					(ftype, folder or "<none selected>", "is not currently writable" if exists else "does not exist"))
		dialog.add_buttons("CANCEL", Gtk.ResponseType.CANCEL,)
		response = dialog.run()
		dialog.destroy()
		return response
				
	def get_project_root(self):
		"""Return a suitable root folder for application to store project (folders).
		If persistent project_base is defined, then uses this.  Otherwise returns
		the following defaults (as a suggestion):
		On windows, this is the CWD of the application when it is started.  On Unix,
		it is a fixed default.
		"""
		if self.p.project_base:
			return self.p.project_base
		# No project base defined.  Ask user for one.
		self.query_project_base()
		return self.p.project_base  # May still be None
		
	def get_project_parent(self):
		"""Return parent directory of current project folder (if any, else project root)
		May return None.
		"""
		if self.get_project_folder():
			return os.path.dirname(self.get_project_folder())
		return self.get_project_root()

	def settings_change_project(self, action=Gtk.FileChooserAction.SELECT_FOLDER):
		par = self.get_project_parent()
		print("Change project: parent=", par)
		self.libsearch.set_action(action)
		if par is not None:
			self.libsearch.set_current_folder(par)
		if action != Gtk.FileChooserAction.SELECT_FOLDER:
			self.libsearch.set_current_name("")
		resp = self.libsearch.run()
		try:
			if resp == Gtk.ResponseType.OK:
				newname = self.libsearch.get_filename()
				if not os.access(newname, os.W_OK):
					if self.query_nonwritable_folder(newname, os.access(newname, os.F_OK)) != Gtk.ResponseType.YES:
						return False    # Should always do this
				self.set_project_folder(newname)
		finally:
			self.libsearch.hide()
		return True
		
	def sel_recent_project(self, combobox, *args):
		newname = combobox.get_active_text()
		if newname == "<none>":
			self.project_close()
		else:
			self.set_project_folder(newname)
		
	def project_new(self, *args):
		self.libsearch.set_title("Create new project folder")
		self.settings_change_project(action=Gtk.FileChooserAction.CREATE_FOLDER)
		
	def project_open(self, *args):
		self.libsearch.set_title("Open project folder")
		self.settings_change_project(action=Gtk.FileChooserAction.SELECT_FOLDER)
		
	def project_save(self, *args):
		# NOTE: save, saveas have no UI button.  Projects are always saved, and
		# the effect of saveas is the same as creating a new project (it will copy
		# current settings).  Project deletion and overwrite must be done using
		# ordinary filesystem manipulation.
		self.save_project_persistent()
		
	def project_saveas(self, *args):
		pass
		
	def project_close(self, *args):
		self.set_project_folder(None)
		
	def libsearch_add(self, *args):
		resp = self.libsearch.run()
		try:
			if resp == Gtk.ResponseType.OK:
				newname = self.libsearch.get_filename()
				iter = self.add_libsearch_list(newname)
				sel = self.libsearch_list_view.get_selection()
				sel.select_iter(iter)
		finally:
			self.libsearch.hide()
	
	def libsearch_remove(self, *args):
			sel = self.libsearch_list_view.get_selection()
			model, iter = sel.get_selected()
			if iter:
				del model[iter]
		
	def libsearch_defaults(self, *args):
		self.libsearch_list.clear()
		self.add_libsearch_list("{project}")
		self.add_libsearch_list("{userlib}")
		self.add_libsearch_list("{stdlib}")
		
	def libsearch_key_pressed(self, treeview, event):
		# Event is a Gdk.EventKey.  Return False if handled
		if event.keyval == Gdk.KEY_Delete:
			self.libsearch_remove()
			return True
		elif event.keyval == Gdk.KEY_Insert:
			self.libsearch_add()
			return True
		#print event.keyval
		return False
		
	def libsearch_row_activated(self, treeview, path, column):
		# Double-click row, popup folder selector
		print("Row activated")
		self.libsearch.set_title("Set library search folder")
		iter = self.libsearch_list.get_iter(path)
		self.libsearch.set_filename(self.libsearch_list[iter][0])
		resp = self.libsearch.run()
		try:
			if resp == Gtk.ResponseType.OK:
				newname = self.libsearch.get_filename()
				self.libsearch_list[iter][0] = newname
		finally:
			self.libsearch.hide()
		
		
	def libsearch_edited(self, renderer, path, newtext):
		print("Edited", path, newtext)
		iter = self.libsearch_list.get_iter(path)
		self.libsearch_list[iter] = [newtext, False]
		
	def settings_changed(self, *args):
		new = self.get_target()
		if new != self.devs_id:
			# Target device changed
			self.set_device(new)
		self.set_verbose(self.get_verbose())
			
	def set_device(self, index, connect=True):
		self.serial_control_lock.acquire()
		self.devs.disconnect()
		self.serial_control_lock.release()

		self.devs_id = index
		self.devs = self.devices[index]
		if connect:
			print("Set target device:", self.devs.target_name())
			self.set_serport(self.get_serport_port(), self.get_serport_text())
		
		
	def get_logging(self):
		#return self.settings_logging.get_active()
		return True

	def get_verbose(self):
		return self.settings_verbose.get_active()

	def get_target(self):
		return self.settings_target.get_active()

	def get_target_text(self):
		return self.settings_target.get_active_text()

	def get_serport(self):
		# Index
		return self.settings_serport.get_active()

	def get_serport_text(self):
		# Display text
		return self.settings_serport.get_active_text()

	def get_serport_port(self):
		# Actual serial port node
		return self.settings_serport.get_active_id()

	def set_logging(self, active):
		#self.settings_logging.set_active(active)
		pass

	def set_verbose(self, active):
		self.settings_verbose.set_active(active)
		#if active:
		#    self.debug_stuff.show()
		#else:
		#    self.debug_stuff.hide()

	def set_target(self, target):
		if target is None:
			target = 0
		if target >= len(self.devices):
			target = 0
		self.settings_target.set_active(target)

	def set_serport(self, serport, text):
		if not text:
			text = serport
		elif not serport:
			# This is case for user entry (not dropdown) since we don't yet have an id
			serport = text
		if serport == self.devs.get_serport():
			return
		print("set_serport", serport)

		# the driver is not thread-safe, so we have to ensure that the background thread is not running when calls to it are made.
		# If we did not use the lock here, it would try to initialize the devices twice, and the binary responses would get
		# all smooshed together.
		self.serial_control_lock.acquire()
		
		self._connected = self.devs.connect(serport)
		
		self.serial_control_lock.release()

		if not self._connected:
			dialog = Gtk.MessageDialog(self.mainwindow, 0, Gtk.MessageType.ERROR,
				Gtk.ButtonsType.NONE, "Serial port selection error")
			dialog.format_secondary_text("The selected serial device node could not be opened:\n%s." % \
						(self.devs.conn_error,))
			dialog.add_buttons("CANCEL", Gtk.ResponseType.CANCEL,)
			response = dialog.run()
			dialog.destroy()
			self.update()
			return
			
		# Add to combobox list if not already
		m = self.settings_serport.get_model()
		act = -1
		for n, iter in enumerate(m):
			#print iter[0], iter[1]
			if iter[1] == serport or iter[0] == serport:
				act = n
		if act == -1:
			self.settings_serport.prepend(serport, text)
			act = 0
		#self.settings_serport.get_child().set_text(serport)
		self.settings_serport.set_active(act)
		self.update()
		
	def serport_changed(self, *args):
		iter = self.settings_serport.get_active_iter()
		if iter is None:
			pass    # Ignore text edits until 'enter'
		else:
			e = self.settings_serport.get_model()[iter]
			self.set_serport(e[1], e[0])

	def serport_add(self, *args):
		self.set_serport(self.get_serport_port(), self.get_serport_text())
	def serport_popup(self, widget, shown):
		# Called when combobox list posted.  Fill in available serial ports
		shown = widget.get_property('popup-shown')
		if shown:
			m = self.settings_serport.get_model()
			ss = set()
			for iter in m:
				ss.add(iter[1])
			spl = self.devs.get_serport_list()
			for port, text in spl:
				if port not in ss:
					self.settings_serport.append(port, text)

	def test_qshort(self, *args):
		print("TQS")
		self.serial_control_lock.acquire()
		self.devs._send_qshort()
		self.serial_control_lock.release()
	def test_qlong(self, *args):
		print("TQL")
		self.serial_control_lock.acquire()
		self.devs._send_qlong()
		self.serial_control_lock.release()
	def test_pgm_ctr(self, *args):
		pc = int(self.builder.get_object("test_pc_entry").get_text(), base=16)
		print("PC", "%04X" % pc)
		self.serial_control_lock.acquire()
		self.devs._send_pgm_ctr(pc & 0xFFFF)
		self.serial_control_lock.release()
	def test_run(self, *args):
		tde = self.builder.get_object("test_run_data_entry")
		t = tde.get_text()
		t = t.split(' ')
		d = [int(x,base=16) & 0xFFFFFFFF for x in t if x.strip()]
		print("RUN", ["0x%08X" % x for x in d])
		self.serial_control_lock.acquire()
		self.devs._send_run(d)
		self.serial_control_lock.release()
	def test_poll(self, *args):
		print("POLL")
		self.serial_control_lock.acquire()
		self.devs._poll()
		self.serial_control_lock.release()
	def test_readback(self, *args):
		print("READBACK X")
		self.serial_control_lock.acquire()
		self.devs._send_readback(0)
		self.serial_control_lock.release()
	def test_readback_y(self, *args):
		print("READBACK Y")
		self.serial_control_lock.acquire()
		self.devs._send_readback(1)
		self.serial_control_lock.release()
	def test_readback_z(self, *args):
		print("READBACK Z")
		self.serial_control_lock.acquire()
		self.devs._send_readback(2)
		self.serial_control_lock.release()
	def test_readback_w(self, *args):
		print("READBACK W")
		self.serial_control_lock.acquire()
		self.devs._send_readback(3)
		self.serial_control_lock.release()
	def test_erase(self, *args):
		print("ERASE")
		self.serial_control_lock.acquire()
		self.devs._send_erase()
		self.serial_control_lock.release()
		
	def view_status_clicked(self, *args):
		self.status_window.show_all()
	def update_status(self, n, dev):
		if dev is None:
			r = [n, '-', 0,0,0,0,0,0,0,0,0,0,0]
		else:
			r = dev.get_status()
		mdl = self.status_list
		iter = mdl.get_iter(Gtk.TreePath(r[0]))
		for n, clr in zip((2,3,4,5,6,7,8,9,12),('green','green','green','yellow','yellow','yellow','red','red','orange')):
			r[n] = self.led_dict[clr][int(bool(r[n]))]
		mdl[iter] = r
	def update_status_button(self, text):
		self.dev_status_button.set_label(text)

	def view_inputs_clicked(self, *args):
		self.input_window.show_all()
	def input_sim_toggled(self, tb, *args):

		self.input_sim_update()
	def input_sim_pressed(self, b, *args):
		self.mom_state[b.sim_input] = True
		self.input_sim_update()
	def input_sim_released(self, b, *args):
		self.mom_state[b.sim_input] = False
		self.input_sim_update()
	def input_sim_clear(self, *args):
		for tb, _, _ in list(self.insim_map.values()):
			tb.set_active(False)
	def input_sim_update(self):
		mask = 0    #FIXME: better to do the bit-level detail in the devs object, clean this up
					# if ever support other than GM215.
		for key, (tb, _, im) in list(self.insim_map.items()):
			on = tb.get_active() or self.mom_state[key]
			im.set_from_pixbuf(self.led_dict['green'][int(on)])
			if on:
				axis, i = key
				mask |= 1 << (3 + axis*4 - i)
		self.serial_control_lock.acquire()
		self.devs.input_sim_update(mask)
		self.serial_control_lock.release()
		
	def show_log_clicked(self, *args):
		# Show Log button clicked
		self.log_window.show_all()
	def log_close_clicked(self, *args):
		self.log_window.hide()
	def log_clear_clicked(self, *args):
		self.log_text.set_text("")
	def log_auto_scroll_toggled(self, *args):
		pass
		
	def get_char_delay(self):
		"""Inter-character (actually, char pair) transmit delay in seconds.
		This control has been re-purposed for overall timeout.  Return 0 here."""
		return 0.0
	def get_resp_timeout(self):
		"""Overall timeout (seconds) waiting for response to commands."""
		#return self.char_delay.get_value() * 0.001
		return 0.05
	def get_cmd_delay(self):
		"""Post-command word transmit delay in seconds"""
		#return self.cmd_delay.get_value() * 0.001
		return 0.01
	def get_poll_rate(self):
		"""Polling rate in seconds"""
		#return self.poll_rate.get_value() * 0.001
		return 0.04
	def get_polling(self):
		"""Whether to poll"""
		return self.poll_checkbutton.get_active()
	def get_trace(self):
		"""Whether to get detailed comms trace"""
		#return self.trace_checkbutton.get_active()
		return self.get_verbose()
		
	def search_clicked(self, *args):
		if self.tab_mgr.ct is not None:
			ht = self.tab_mgr.ct.get_selection()
			if ht is not None and len(ht) < 100:
				self.search_text.set_text(ht)
		self.search_window.show()
	def search_close_clicked(self, *args):
		self.search_window.hide()
		
	def search_text_changed(self, *args):
		#text = self.search_text.get_text()
		pass
	def search_opt_toggled(self, *args):
		pass
	def get_search(self):
		text = self.search_text.get_text()
		cs = not self.search_ignorecase.get_active()
		word = self.search_word.get_active()
		return (text, cs, word)
	def get_replace(self):
		if self.enable_replace.get_active():
			return self.replace_text.get_text()
		return None
	def replace_toggled(self, *args):
		self.set_show_replace()
	def search_first_clicked(self, *args):
		if self.tab_mgr.ct is None:
			return
		self.tab_mgr.ct.search_next(*self.get_search(), frm='start')
	def search_last_clicked(self, *args):
		if self.tab_mgr.ct is None:
			return
		self.tab_mgr.ct.search_prev(*self.get_search(), frm='end')
	def search_prev_clicked(self, *args):
		if self.tab_mgr.ct is None:
			return
		self.tab_mgr.ct.search_prev(*self.get_search())
	def search_next_clicked(self, *args):
		if self.tab_mgr.ct is None:
			return
		self.tab_mgr.ct.search_next(*self.get_search())
	def replace_clicked(self, *args):
		if self.tab_mgr.ct is None:
			return
		self.tab_mgr.ct.replace(self.get_replace())
	def replace_next_clicked(self, *args):
		self.replace_clicked(*args)
		self.search_next_clicked(*args)
	def replace_all_clicked(self, *args):
		tab = self.tab_mgr.ct
		if tab is None:
			return
		whence = 'start'
		while tab.search_next(*self.get_search(), frm=whence):
			tab.replace(self.get_replace())
			whence = 'anchor'
			
	def set_show_replace(self):
		enab = self.enable_replace.get_active()
		if enab:
			self.replace_container.show()
		else:
			self.replace_container.hide()
			self.search_window.resize(1,1)  # Shrink wrap it
			
	def key_press(self, widget, event):
		# Event is a Gdk.EventKey.  Return False if handled
		# Widget is the source text viewer.  Basically, we wish to handle the 'find next' keys etc.
		# F3 = find next, Ctrl-F3 = find prev, Ctrl-Shift-F3 = find first
		# Ctrl-R = replace, next; Ctrl-Shift-R = replace only (only if enable replace)
		#print event.keyval, event.string
		if event.state & (Gdk.ModifierType.MODIFIER_MASK & ~(Gdk.ModifierType.CONTROL_MASK|Gdk.ModifierType.SHIFT_MASK)):
			# Ignore if any modifier except CONTROL, SHIFT
			return False
		ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
		shift = event.state & Gdk.ModifierType.SHIFT_MASK
		if ctrl and shift:
			if event.keyval == Gdk.KEY_r:
				if self.enable_replace.get_active():
					self.replace_clicked()
				return True
			if event.keyval == Gdk.KEY_F3:
				self.search_first_clicked()
				return True
			return False
		if shift:
			return False
		if ctrl:
			if event.keyval == Gdk.KEY_r:
				if self.enable_replace.get_active():
					self.replace_next_clicked()
				return True
			if event.keyval == Gdk.KEY_F3:
				self.search_prev_clicked()
				return True
			return False
		# No modifier...
		if event.keyval == Gdk.KEY_F3:
			self.search_next_clicked()
			return True
		return False

			
	def help_dipsw(self, *args):
		self.dipsw_dialog.show()
	def dipsw_ok_clicked(self, *args):
		self.dipsw_dialog.hide()
	def dipsw_update(self, *args):
		"""Update DIP switch setting display
		"""
		page = self.dipsw_notebook.get_current_page()
		if page == 0:   # motion control mode
			sw = self.get_mcm_sw(self.dipsw_axis.get_active(), 
									self.dipsw_program.get_active(),
									self.dipsw_frame_size.get_active())
		else:           # driver mode
			sw = self.get_drive_sw(self.phase_current_adjustment.get_value(),
									self.dipsw_self_test.get_active(),
									self.dipsw_step_resolution.get_active(),
									self.dipsw_enab_standby.get_active(),
									self.dipsw_frame_size.get_active())
			sens = not self.dipsw_self_test.get_active()
			self.dipsw_step_resolution.set_sensitive(sens)
			self.dipsw_enab_standby.set_sensitive(sens)
		for n, x in enumerate(sw):
			self.dip[n].set_from_pixbuf(self.dipsw[x])
	def get_mcm_sw(self, axis_num, prog_mode, frame_size):
		x = [0]*5 + [2]*5
		x[4] = not frame_size
		x[2] = axis_num in (0,1)
		x[3] = axis_num in (0,2)
		x[1] = not prog_mode
		return x
	def get_drive_sw(self, phase_current, self_test, step_res, enab_standby, frame_size):
		x = [0]*10
		x[0] = 1
		x[1] = not enab_standby
		x[2] = step_res in (0,1)
		x[3] = step_res in (0,2)
		if self_test == 1:
			x[1] = 0
			x[2] = 0
			x[3] = 0
		elif self_test == 2:
			x[1] = 1
			x[2] = 0
			x[3] = 0
		x[4] = not frame_size
		pc = self.phase_current_sw(phase_current)
		for n in range(9,4,-1):
			x[n] = pc & 1
			pc >>= 1
		return x
	def phase_current_sw(self, n):
		return int(0.5+31*(n/7.))
						   
	def help_insns(self, *args):
		pass
			
	LOG_DEBUG='d'
	LOG_INFO='i'
	LOG_WARNING='W'
	LOG_ERROR='E'
	LOG_FATAL='F'
	def log(self, text, level='d'):
		"""Write log entry.
		text is either a string or an iterable of strings.  In the latter case, a multi-line
		message is generated.
		"""
		t = time.time()
		dt = int((t - self.log_time)*1000)
		if type(text)==str:
			if self.log_crlf:
				self.log_text.insert(self.log_text.get_end_iter(), "+%6d %s: %s" % (dt, level, text))
			else:
				self.log_text.insert(self.log_text.get_end_iter(), text)
			self.log_crlf = text.endswith('\n')
		else:
			if not self.log_crlf:
				self.log_text.insert(self.log_text.get_end_iter(), '\n')
				self.log_crlf = True
			self.log_text.insert(self.log_text.get_end_iter(), "+%6d %s:\n" % (dt, level))
			for x in text:
				self.log_text.insert(self.log_text.get_end_iter(), " %s" % (x,))
				self.log_crlf = x.endswith('\n')
			if not self.log_crlf:
				self.log_text.insert(self.log_text.get_end_iter(), '\n')
				self.log_crlf = True
		if self.log_auto_scroll.get_active():
			self.log_view.scroll_to_mark(self.log_mark, 0., False, 0., 0.)
		self.log_time = t
	def write(self, text):
		"""Replacement for sys.stdout.write so that print statements can redirect to log.
		"""
		self.log(text)
	def redirect_log(self):
		class stderr_writer(object):
			def __init__(self, ui):
				self.ui = ui
			def write(self, text):
				self.ui.log(text, level='E')
		self.oldstdout = sys.stdout
		self.oldstderr = sys.stderr
		#sys.stdout = self
		#sys.stderr = stderr_writer(self)
	def unredirect_log(self):
		try:
			sys.stdout = self.oldstdout
			sys.stderr = self.oldstderr
		except AttributeError:
			pass
			
	def device_notify(self, msg):
		"""Called from Devices object when there is an unrecoverable error.
		msg is a short message to display.
		For now, pop up a modal dialog.
		"""
		dialog = Gtk.MessageDialog(self.mainwindow, 0, Gtk.MessageType.ERROR,
			Gtk.ButtonsType.NONE, "Device error")
		dialog.format_secondary_text("""Communication problem detected:
%s

In some cases, you might need to power the 
devices off and on, then reconnect.
""" % \
					(msg,))
		dialog.add_buttons("EStop", Gtk.ResponseType.CANCEL,)
		dialog.add_buttons("Continue", Gtk.ResponseType.OK,)
		dialog.add_buttons("Disconnect", Gtk.ResponseType.YES,)
		response = dialog.run()
		dialog.destroy()
		if response == Gtk.ResponseType.CANCEL:
			self.estop_clicked(None)
		elif response == Gtk.ResponseType.YES:
			self.serial_control_lock.acquire()
			self.devs.disconnect()
			self.serial_control_lock.release()

		


class Persistent(object):
	def __init__(self):
		""" Persistent data.  All data must be picklable.
		
		This base class is used to save application-wide data in a single per-user location.
		
		On Linux:
			Saved in ~/.geckomotion (regardless of CWD at startup)
		On Windows:
			Saved in CWD of the application at start.  This is typically selected by the
			installer, and the working directory is set when the user starts the
			app using the start folder short-cut.
			
		Subclass ProjectPersistent is used for project-specific data. (c.f.)
		"""
		self.__dict__['d'] = {}
		if sys.platform == 'win32':
			self.__dict__['pfile'] = ".geckomotion" # rel to CWD
		else:
			self.__dict__['pfile'] = os.path.expanduser("~/.geckomotion") # rel to $HOME
		self.__dict__['defaults'] = {
			'project' : None,       # Last-used project folder (None if use following default)
			'pp' : None,            # PersistentProject object for the unnamed default i.e. non-project.
			'all_projects' : {},    # Remember all project folders (key, with value as last access time)
			'project_base' : None,  # Default project base directory (e.g. ~/geckomotion/Projects on Linux)
									# This is used to shorten filenames by converting to {projects}
			'error_threshold' : 100,# Max asm errors before giving up
			'mainwindow' : {
				'width' : 1000,
				'height' : 600,
				},
			'log_window' : {
				'width' : 800,
				'height' : 300,
				},
			'status_window' : {
				'width' : 400,
				'height' : 200,
				},
			'search_window' : {
				'width' : 0,
				'height' : 0,
				},
			'pane_position' : -1,
			'log_auto_scroll' : True,
			'inter_char_delay' : 0.0,
			'cmd_delay' : 0.002,
			'enable_poll' : False,
			#'enable_trace' : False,
			'poll_rate' : 0.1,
			'enable_replace' : False,
			'bootloader' : '',
			}
	def save(self):
		if self.pfile is None:
			# This settings is nested inside another, so don't save
			return
		print("Saving persistent", self.pfile)
		try:
			with open(self.pfile, "wb") as f:
				pickle.dump(self.d, f)
		except:
			print("Write persistent data to", self.pfile, "failed")
	def load(self):
		if self.pfile is None:
			# This settings is nested inside another, so don't load
			return
		self.d.update(copy.deepcopy(self.defaults))
		try:
			with open(self.pfile, "rb") as f:
				d2 = pickle.load(f)
			self.d.update(d2)
			return True
		except:
			return False # no file, probably

	def __getattr__(self, name):
		if name[0] == '_':
			raise AttributeError(name) 
		return self.d[name]
	def __setattr__(self, name, value):
		self.d[name] = value

class PersistentProject(Persistent):
	def __init__(self, projectfolder, global_persist):
		""" Persistent data.  All data must be picklable.
		
		This subclass is used to save project-specific information in the project folder.
		
		Note that we do NOT call the base class __init__().
		"""
		self.__dict__['d'] = {}
		self.__dict__['p'] = global_persist
		pfile = os.path.join(projectfolder, ".gm_project") if projectfolder is not None else None
		self.__dict__['pfolder'] = projectfolder
		self.__dict__['pfile'] = pfile
		self.__dict__['defaults'] = {
			'filename' : None,
			'openfiles' : [],
			'top_file' : None,
			'libsearch' : [ '{project}', '{usrlib}', '{stdlib}'],
			'target' : 0,
			'logging' : False,
			'verbose' : False,
			'devname' : 'com1' if sys.platform == 'win32' else '/dev/ttyUSB0',  # Actual device node for serial.Serial()
			'devtext' : 'Communications Port (COM1)' if sys.platform == 'win32' else '/dev/ttyUSB0',  # Display in combobox
			}
		if pfile is None:
			self.__dict__['d'] = copy.deepcopy(self.defaults)
	def get_project_folder(self):
		return self.pfolder # May be None if no project
	def get_usrlib_folder(self):
		if self.p.project_base is None:
			return None
		# Sibling folder to project base, called 'Lib'
		return os.path.join(self.p.project_base, os.pardir, "Lib")
	def get_stdlib_folder(self):
		return _fulldir("Lib")


if __name__ == "__main__":
	
	# Create user interface
	ui = UI(Persistent(), Devices())
	ui.run()
