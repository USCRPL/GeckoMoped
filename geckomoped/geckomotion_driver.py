from geckomotion.devices import RS485Devices
from mockui import MockUI, MockTab, PersistentProject, Persistent
from threading import Thread, Lock
import time
import traceback

class GMCompileException(Exception): pass

# thrown when state-controlling functions are called at invalid times
class GMInvalidStateException(Exception): pass

class GeckoMotionDriver(object):
	
	# log_file is the file to send the driver's debug output to.  If it is None, no output will be printed.
	# serial_update_callback should be a no-argument function, and is called immediately after the serial tick function.
	# Use it to address new motor controller state info in your function.
	def __init__(self, log_file, serial_update_callback):
		
		# create mock objects
		self.mockui = MockUI()
		self.mockui.log_file = log_file
		self.mocktab = MockTab()
		self.gm_global_prefs = Persistent()
		self.gm_global_prefs.load()
		self.gm_project_prefs = PersistentProject(None, self.gm_global_prefs)
		
		# create GeckoMotion devices object
		self.devices = RS485Devices()
		self.devices.set_ui(self.mockui)
		
		# create thread
		self.geckomotion_serial_thread = Thread(target=self.internal_serial_thread)
		self.geckomotion_serial_thread.daemon = False
		
		self.serial_control_lock = Lock()
		
		self.serial_thread_shutdown_signal = False
		self.serial_update_callback = serial_update_callback
		
		self.geckomotion_serial_thread.start()
		
		# state variables
		self._connected = False
		
		
	def get_serialports(self):
		""" Returns a list of serial port names that exist on the system.  
		Pass one of these to serial_connect()."""
		
		return (port[0] for port in self.devices.get_serport_list())
	
	def connect(self, serialport):
		"""Connects to motor controllers on a serial port, and starts the comms thread.  Returns true if connection was
		successful, false if not."""
		
		# the driver is not thread-safe, so we have to ensure that the background thread is not running when calls to it are made.
		# If we did not use the lock here, it would try to initialize the devices twice, and the binary responses would get
		# all smooshed together.
		self.serial_control_lock.acquire()
		
		self._connected = self.devices.connect(serialport)
		
		self.serial_control_lock.release()
		
		return self._connected
		
	def shutdown(self):
		"""Shuts down internal serial comms thread.  Devices will finish the current command, then stop.
		
		It's a good idea to call this before the Python interpreter is shut down.
		NOTE: this call may block for 10-20 ms."""
		
		self.serial_thread_shutdown_signal = True
		self.geckomotion_serial_thread.join()
		
	def load_program(self, program):
		""" Readies the GeckoMotion code contained in "program" to be sent to the controllers.
		
		If there are compile errors, it will throw a GMCompileException containing the error message."""
		
		# tell the driver that the source has changed
		self.devices.mod_asm(True)
		
		# there is a GeckoMotion bug where the program must end with a newline, or the last line of it is not compiled.
		# Interestingly, this affects the GeckoMotion IDE as well.
		if not program.endswith("\n"):
			program = program + "\n"
		
		self.mocktab.set_text(program)
		
		self.serial_control_lock.acquire()
		
		self.devices.assemble(self.mocktab, self.gm_project_prefs)
		
		self.serial_control_lock.release()
		
	def run(self):
		""" Runs the current program from the start.  Throws an exception if not all devices are ready, or if there is no code."""
		
		self.serial_control_lock.acquire()
		
		if not self.devices.is_ready():
			self.serial_control_lock.release()
			raise GMInvalidStateException("Cannot start program, not all devices are in ready state.")
		
		if not self.devices.assembly_valid():
			self.serial_control_lock.release()
			raise GMInvalidStateException("Cannot start program, no code has been compiled.")
		
		self.devices.restart_program()
		self.devices.run_until_break()
		
		self.serial_control_lock.release()
		

	def pause(self):
		""" Pauses the program at its current point.  Will interrupt long-running commands like MOVE.
		
		Throws an InvalidStateException if the controllers are already paused."""
		
		self.serial_control_lock.acquire()
		
		if self.is_paused():
			self.serial_control_lock.release()
			raise GMInvalidStateException("Cannot pause, already paused!")
		
		if not self.is_running():
			self.serial_control_lock.release()
			raise GMInvalidStateException("Cannot pause, a program is not running")
		
		self.devices.pause()
		
		self.serial_control_lock.release()
	
	def resume(self):
		""" Resumes the program if it was paused.  Throws an InvalidStateException otherwise."""
		
		self.serial_control_lock.acquire()
		
		if self.is_paused():
			self.serial_control_lock.release()
			raise GMInvalidStateException("Cannot resume, not paused!")
		
		self.devices.resume()
		
		self.serial_control_lock.release()
	
	def stop(self):
		""" Causes execution to end after the current instruction finishes. """
		
		self.serial_control_lock.acquire()
		
		if not self.is_running():
			self.serial_control_lock.release()
			raise GMInvalidStateException("Cannot stop, a program is not running")
		
		self.devices.stop()
		
		self.serial_control_lock.release()
		
	def estop(self):
		""" Emergency-stops the motors in the middle of the current instruction. 
		
		Also sets the program back to the start."""
		
		# only wait for the mutex for 100 ms, in case the background thread has gotten stuck or something
		is_locked = self.serial_control_lock.acquire(True, .1) 
		
		self.devices.estop()
		
		if is_locked:
			self.serial_control_lock.release()
	
	def is_connected(self):
		""" Returns true if the serial connection is connected """
		
		return self._connected
	
	def get_num_devices(self):
		
		""" Returns the number of motor controllers that are attached"""
		
		return self.devices.n_devs
	
	def is_running(self):
		""" Returns true if a command is currently executing."""
		
		return self.devices.stepping == self.devices.RUN_UNTIL_BREAK
	
	def is_paused(self):
		""" Returns true if the motors are paused and resume() can be legally called """
		
		return self.devices.state == self.devices.PAUSED
	
	def wait_for_program(self):
		""" Blocks the current thread until the current program has finished running. """
		
		if not self.is_running():
			raise GMInvalidStateException("Cannot wait for program, a program is not running")
		
		try:
			while self.is_running():
				time.sleep(.1)
		except GMInvalidStateException as e:
			raise e     
		except KeyboardInterrupt as e:
			self.estop()
			raise e
		
	def internal_serial_thread(self):
		""" Internal function which ticks the motor controller comms code.  Updates status, and sends the next command if applicable."""
		while not self.serial_thread_shutdown_signal:
			
			# if the serial cable is unplugged, then the GM library will set its internal serial port object to None
			self._connected = (self.devices.f != None)
			
			if self._connected:
				
				self.serial_control_lock.acquire() 
				
				try:
					# force query of all devices' state (not calling this is why the GM GUI tends to freeze up)
					self.devices._send_qlong()
					
					# send queued serial data if needed
					self.devices.idle_func()
				except KeyboardInterrupt:
					raise
				except Exception as ex:
					traceback.print_exc()
					pass
				
				self.serial_control_lock.release()
					
			if not self.serial_update_callback is None:
				self.serial_update_callback()
				
			# update every 20ms
			time.sleep(.02)
			
			
			
	# NOTE: axis ordering is X-W correspond to indices 0-3
	
	def get_axis_position(self, axis_index):
		""" Returns the number of steps away from the zero point of the given axis, as of the most recent serial tick."""
		
		if axis_index > self.devices.n_devs:
			raise ValueError("Axis out of range!")
			
		return self.devices.devs[axis_index].pos
	
	def get_axis_velocity(self, axis_index):
		""" Returns the velocity of the given axis in steps per second, as of the most recent serial tick."""
		
		if axis_index > self.devices.n_devs:
			raise ValueError("Axis out of range!")
		
		return self.devices.devs[axis_index].vel
		