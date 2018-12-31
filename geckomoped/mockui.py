
import sys
import os
import pickle
import copy
import datetime

# Fake UI class to pass to GeckoMotion RS485Devices class so that paramters can be input without opening a GUI window
class MockUI:
	def get_trace(self):
		"""Whether to get detailed comms trace"""
		#return self.trace_checkbutton.get_active()
		return False
	
	def update_status(self, n, dev):
		if dev is not None:
			if self.get_verbose() and self.log_file is not None:
				self.log_file.write("Status update [%s] from axis %d: pos = %d, vel = %d, pc = %x\n" % (datetime.datetime.now(), n, dev.pos, dev.vel, dev.pc))
		
	def update(self):
		"""Work-around for inability to use action groups.
		Call after each application change of state which may change widget
		sensitivity.
		"""
		pass
	
	def show_error_list(self):
		pass
		
	def hide_error_list(self):
		pass
		
	def clear_error_list(self):
		pass
		
	def add_error_list(self, filename, line_num, message, err_index):
		print("GeckoMotion script syntax error! at %s:%s: %s" % (filename, line_num, message), file=sys.stderr) 
		
	def highlight_error(self, error_index):
		pass
		
	def unhighlight_error(self):
		pass
		
	def device_notify(self, msg):
		"""Called from Devices object when there is an unrecoverable error.
		msg is a short message to display.
		For now, pop up a modal dialog.
		
		"""
			
		print("""Communication problem detected:
%s

In some cases, you might need to power the 
devices off and on, then reconnect.
""" % \
					(msg,), file=sys.stderr)
	
	
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
		return 0.002
	def get_poll_rate(self):
		"""Polling rate in seconds"""
		#return self.poll_rate.get_value() * 0.001
		return 0.04
	
	def get_polling(self):
		"""Whether to poll"""
		return False
		
	def update_status_button(self, text):
		#print("Status update: " + text)
		pass
	
	def get_verbose(self):
		return True
		
class MockFileBuffer(object):
	def __init__(self, text):
		self.text = text
	
	def get_text(self, start, end, include_hidden_chars):
		# we don't implement GtkTextIter, so just return the full text
		return self.text
	
	# NOTE: these functions normally use the GtkTextIter class.  We just replace them our version:
	class MockTextIter:
		def __init__(self, line_number):
			self.line_number = line_number
		
		def get_line(self):
			return self.line_number
		
	def get_iter_at_line(self, line_number):
		return self.MockTextIter(line_number)
	
	def get_start_iter(self):
		return self.MockTextIter(0)
	def get_end_iter(self):
		return self.MockTextIter(self.text.count('\n'))
	
	# really simple mock of GtkTextMark
	class MockTextMark:
		def __init__(self, buffer, text_iter):
			self.text_iter = text_iter
			self.buffer = buffer
			
		def get_deleted(self):
			return False
		
		def get_buffer(self):
			return self.buffer
	
	def create_mark(self, mark_name, where, left_gravity):
		return self.MockTextMark(self, where)
		
	def create_source_mark(self, mark_name, category, where):
		return self.MockTextMark(self, where)
		
	def get_iter_at_mark(self, mark):
		return mark.text_iter
		
	def delete_mark(self, mark):
		pass


class MockTab(object):
	"""Class to encapsulate GtkNotebook tabs.  In each tab, we contain a
	GtkSourceView widget and associated data.
	We derive from GObject simply to be able to be placed in a liststore.
	
	read_only may be set to a string to entitle a read-only buffer (e.g. for 
	assembly listings)
	"""
	def __init__(self):
		self.mgr = None
		self.placeholder = True
		self.read_only = True
		self.ro_title = False
		self.exec_mark = None
		self.err_mark = None
		self.sw = None
		self.mtime = None
		
		self.filename = "<API-Injected Code>"
		self.is_top = True;
		self.buffer_gm = MockFileBuffer("")
		
	def show(self):
		pass
		
	def get_ident(self):
		return self.ro_title
		
	def share_tab(self, other):
		self.buffer_gm = other.buffer_gm
		self.filename = other.filename
		self.is_top = other.is_top

	def buf(self):
		return self.buffer_gm
		
	def set_text(self, text):
		self.buffer_gm.text = text
	
	def sv(self):
		return self.sourceview_gm
		
	def get_mgr(self):
		return self.mgr
		
	def set_top(self, yes):
		if yes != self.is_top:
			self.is_top = yes
			self.tab_label.set_icon(1 if yes else 0)
			
	def update_title(self):
		pass
			
	def set_filename(self, filename):
		self.filename = filename
		self.update_title()
		
	def get_filename_str(self):
		return self.filename
		
	def get_filename_condensed(self):
		return self.filename
		
	def get_abs_filename(self):
		# Return None because we do not represent a file on the disk
		return None
		
	def load_file(self, buffer, path):
		pass
		
	def is_file_modified_externally(self):
		return False
		
	def reload_file(self):
		pass
				
	def store_file(self, buffer, filename):
		pass
	
	#def query_save(self, filename):
		# Called when closing tab, but current file is modified.
		# Pops up a question dialog and returns the response:
		#  YES = save the current file, 
		#  NO = discard the current modifications, 
		#  CANCEL = abort loading the new file
		
	#	return Gtk.ResponseType.NO
		
	def open_file(self, filename):
		pass
		
	def discard_or_save_current_file(self, dialog):
		return False
		
	def set_shortcut_folders(self, dlg):
		pass
		
	def is_untitled(self):
		return True
		
	def close(self, dialog):
		return False

	def focus(self):
		pass
	
	def move_exec_mark(self, iter, scroll=False):
		self.exec_mark = iter;
		
	def remove_exec_mark(self):
		if self.exec_mark:
			self.exec_mark = None
			
	def move_err_mark(self, iter, scroll=False):
		self.err_mark = iter
		
	def remove_err_mark(self):
		if self.err_mark:
			self.err_mark = None

# classes copied from gmgui.py

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
		print("Saving persistent " + self.pfile)
		try:
			with open(self.pfile, "wb") as f:
				pickle.dump(self.d, f)
		except:
			print("Write persistent data to" + self.pfile + "failed")
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
		