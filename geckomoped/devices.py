from .assemble import *
import serial, struct, sys, time
import traceback

#from multiprocessing import Process, Pipe


try:
    import serial.tools.list_ports as lports
    have_lports = True
except ImportError:
    have_lports = False

if sys.platform == 'win32':
    import winreg, itertools, re
    from multiprocessing import Process, Pipe


# Following used only when win32 platform (and no serial.tools)...
def win32_enumerate_serial_ports():
    """ Uses the Win32 registry to return an
        iterator of serial (COM) ports
        existing on this computer.
    """
    path = 'HARDWARE\\DEVICEMAP\\SERIALCOMM'
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
    except WindowsError:
        raise IterationError
    for i in itertools.count():
        try:
            val = winreg.EnumValue(key, i)
            yield str(val[1])
        except EnvironmentError:
            break
def win32_full_port_name(portname):
    """ Given a port-name (of the form COM7,
        COM12, CNCA0, etc.) returns a full
        name suitable for opening with the
        Serial class.
    """
    m = re.match('^COM(\d+)$', portname)
    if m and int(m.group(1)) < 10:
        return portname
    return '\\\\.\\' + portname

class GUIData():
    def __init__(self):
        self.actions = []



class Device(object):
    """Base class for single target device on serial bus.
    """
    MASK_AXISNUM = 0x03
    FLG_BUSY = 0x04
    FLG_PIC_ERR = 0x08
    FLG_FPGA_ERR = 0x10
    MASK_ERROR = FLG_FPGA_ERR | FLG_PIC_ERR
    FLG_IN1 = 0x80
    FLG_IN2 = 0x40
    FLG_IN3 = 0x20
    MASK_INPUTS = FLG_IN1|FLG_IN2|FLG_IN3
    MASK_GROUP = 0x0300
    FLG_OUT1 = 0x1000
    FLG_OUT2 = 0x2000
    FLG_OUT3 = 0x4000
    MASK_OUTPUTS = FLG_OUT1|FLG_OUT2|FLG_OUT3

    def __init__(self, axisname, axisnum):
        self.axisname = axisname
        self.axisnum = axisnum
        self.pc = 0
        self.flags = 0
        self.pos = 0
        self.vel = 0
        self.vin = 0
        self.noqresp = 0
        self.reset_offset()
        self.pos_valid = True   # Whether position is meaningful
        self.vel_valid = True   # Whether velocity is meaningful

    def is_busy(self):
        return (self.flags & Device.FLG_BUSY) != 0
    def error_state(self):
        return (self.flags & Device.MASK_ERROR) >> 3
    def input_state(self):
        return (self.flags & Device.MASK_INPUTS) >> 5 ^ 0x07
    def output_state(self):
        return (self.flags & Device.MASK_OUTPUTS) >> 12
    def reset_offset(self):
        self.offset = -388608  # Default device position offset (added to reported pos before displaying to user)
    def set_offset(self, o):
        self.offset = o
    def set_pos_valid(self, valid):
        self.pos_valid = valid
    def set_vel_valid(self, valid):
        self.vel_valid = valid
    def get_status(self):
        """Return list sitable for insertion in row of status display liststore.
        [axisnum, axisname, i1, i2, i3, o1, o2, o3, ferr, perr, pos, vel, bsy]
        Note: i1..3,o1..3,errs,bsy should be stored as int values 0 or non-zero, or bool.  These will be
        translated to LED image pixbufs.
        Note: input states are inverted.
        """
        return [self.axisnum, self.axisname, \
                not(self.flags & self.FLG_IN1), not(self.flags & self.FLG_IN2), not(self.flags & self.FLG_IN3), \
                self.flags & self.FLG_OUT1, self.flags & self.FLG_OUT2, self.flags & self.FLG_OUT3, \
                self.flags & self.FLG_FPGA_ERR, self.flags & self.FLG_PIC_ERR, \
                int(self.pos) + self.offset if self.pos_valid else 0, \
                self.vel if self.vel_valid else 0, \
                self.flags & self.FLG_BUSY ]
    def executing_insns(self, insnlist):
        """Called when device is executing the provided instruction chain.
        For now, look at the 1st insn in the chain, and use it to determine the position
        and velocity offsets and validity flags.
        """
        insn = insnlist[0]
        self.set_pos_valid(insn.is_pos_valid())
        self.set_vel_valid(insn.is_vel_valid())
        if insn.is_reset_offset() and insn.get_command_data() & 1<<self.axisnum:
            self.set_offset(-insn.get_reset_offset())

class GM215Device(Device):
    """GM215 device class
    """
    pass

class Devices(object):
    """Base class for device 'chain'.
    The base class itself implements a dummy chain for testing.
    """
    # Program run state machine states
    DISCONNECTED = 0
    READY = 1
    RUNNING = 2
    HOLD = 3
    PAUSED = 4
    states = ["DISCONNECTED", "READY", "RUNNING", "HOLD", "PAUSED"]
    states_short = ["DISC", "READY", "RUN", "HOLD", "PAUSE"]

    # Stepping mode states
    STOPPED = 0
    RUN_UNTIL_BREAK = 1
    STEP_INSN = 2
    RUN_UNTIL_BREAK_OR_ADDRMATCH = 3
    STEP_RETURN = 4
    STEP_CURSOR = 5

    def __init__(self):
        self.devname = None     # Serial port device name
        self._state = Devices.READY   # dummy devices start 'ready'
        self.stepping = Devices.STOPPED
        self.addr = 0         # Current execution point ('PC')
        self.bkpts = {}       # dict of Bkpt, indexed by addr
        self.code = Code()    # Code object, mapping source text to object code
        self.deferred_done = False
        self.exec_mark = None
        self.devs = [None]*4  # Array of Device() objects (indexed by axisnum 0..3)
        self._n_devs = 0
        self.insim_state = 0
        self.insn_len = 1
        self.send_next_command = False
        
        # Stores the data from the device that will be passed to the GUI
        self.gui_data=GUIData()

        self.trace = False;

    def set_ui(self, ui):
        self.ui = ui

    def target_name(self):
        return "GM215 Simulator"
    def get_serport_list(self):
        return [("/dev/testserial","testserial")]
    def get_serport(self):
        return self.devname
    def set_timeout(self, ms, cb, data):
        return GLib.timeout_add(ms, cb, data)
    def remove_timeout(self, tag):
        Glib.source_remove(tag)

    def set_trace(self, enable_trace):
        """ Set whether to enable trace logging"""
        self.trace = enable_trace;


    def timeout(self, data):
        #print "TMO", data
        # True to continue, False to stop timer.
        return True

    def _connect(self, devname):
        self.state = Devices.READY
        return True
    def _disconnect(self):
        self.state = Devices.DISCONNECTED

    def connect(self, devname):
        if self.state != Devices.DISCONNECTED:
            self.disconnect()
        if self._connect(devname):
            self.devname = devname
            return True
        return False
    def disconnect(self):
        self._disconnect()
        self.devname = None
    def reconnect(self, devname=None):
        if devname is None:
            devname = self.devname
        if self.is_connected():
            self.disconnect()
        return self.connect(devname)

    def set_code(self, c):
        self.code = c
    def get_code(self):
        return self.code
    def mod_asm(self, yes):
        self.code.mod_asm = yes

    def update_status_button(self):

        self.gui_data.actions.append(lambda gui: gui.update_status_button("%d [%s]" % (self.n_devs, Devices.states_short[self.state])))

    @property
    def n_devs(self): return self._n_devs

    @n_devs.setter
    def n_devs(self, n):
        self._n_devs = n
        self.update_status_button()

    @property
    def state(self): return self._state

    @state.setter
    def state(self, newstate):
        if newstate == self._state:
            return
        if self.ui.get_trace():
            print("set_state:", Devices.states[self._state], "->", Devices.states[newstate])
        self._state = newstate
        if self.deferred_done and newstate == Devices.RUNNING:
            self.deferred_done = False
            self._done()
        self.update_status_button()

    def is_connected(self):
        # True if RS485 link open and have communication
        return self.state != Devices.DISCONNECTED
    def is_ready(self):
        # True if devices are not paused, and all in RDY state.
        return self.state == Devices.READY
    def is_paused(self):
        # True if devices are paused or in hold.
        return self.state == Devices.HOLD or self.state == Devices.PAUSED
    def insn_address(self):
        # Return instruction address (in READY state) else None
        if self.is_ready():
            return self.addr
        return None

    def _done(self):
        """Called from I/O processing when instruction completed and all
        devices are RDY.  This sets the state appropriately, and (if
        required) automatically issues the next command.  I/O processing
        has already set self.addr (next address) based on feedback from the
        devices.  Assumed to be in RUNNING or PAUSED state for this to be called.
        """
        self.gui_data.actions.append(lambda gui: self.update_exec_pointer())
        if self.state == Devices.PAUSED:
            # This would not normally happen, however there is a possibility
            # the pause command was not actually received by the device before
            # it completed the last running command.  In this case, a flag is
            # set so that this function is called when the state moves out of
            # PAUSED.
            self.deferred_done = True
            return
        err = None
        if not self.hit_breakpoint() and \
                    (self.stepping == Devices.RUN_UNTIL_BREAK or \
                     self.stepping == Devices.RUN_UNTIL_BREAK_OR_ADDRMATCH and self.addrmatch != self.addr):
            self.send_next_command = True
            return
            #err = self.send_command(False)     <-- can't do this, builds function calls on stack
            #if err is None:                    <-- until recursion level exceeded.  So defer until
            #    return  # remain in RUNNING state  <-- idle processing.
        self.stepping = Devices.STOPPED
        self.state = Devices.READY

    def _write_insn(self, binlist, fast, instant, nxtaddr):
        """binlist is list of 1-4 32-bit words, which is the next instruction
        to send.

        Since this is asynchronous, the commands are queued up for I/O, then
        on completion the state will be set appropriately
        """
        # Base class just works instantly... (do what I/O would normally do)
        self.next_addr = self.addr + (nxtaddr if instant else len(binlist))
        #self._done()   <-- not so fast: this recurses for each insn in RUN mode, so caller needs to do this
    def _send_pause(self):
        pass
    def _send_resume(self):
        pass
    def _send_stop(self):
        pass
    def _send_estop(self):
        pass

    def update_exec_pointer(self, scroll=True):
        """Move the text buffer 'next instruction' indicator to self.addr line.
        """
        if self.addr < len(self.code.obj):
            insn = self.code.obj[self.addr]
            tab = insn.get_tab()
            tab.focus()
            i = insn.get_iter()
            tab.move_exec_mark(i, scroll)

    def hit_breakpoint(self):
        return self.addr in self.bkpts
    def clear_breakpoint(self, bkpt):
        bkpt.delete_mark()
        if bkpt.get_addr() in self.bkpts:
            del self.bkpts[bkpt.get_addr()]
    def clear_all_breakpoints(self):
        for addr, bkpt in list(self.bkpts.items()):
            self.clear_breakpoint(bkpt)
    def toggle_breakpoint_at_cursor(self, tab):
        iter = tab.buf().get_iter_at_mark(tab.buf().get_insert())
        line = iter.get_line()
        addr = self.code.address_from_line(line, tab)
        if addr is not None:
            if addr not in self.bkpts:
            #print "set bkpt at addr", addr, "line", line
                self.bkpts[addr] = Bkpt(line, tab, addr)
            else:
                #print "clear bkpt at addr", addr, "line", self.bkpts[addr].get_line()
                self.clear_breakpoint(self.bkpts[addr])
    def adjust_breakpoints(self):
        """Called after assembly, since addresses may have changed.
        Edits may cause several breakpoints to be on the same line; duplicates
        are deleted.  Then, the address->Bkpt dict is reconstructed with new
        addresses.
        """
        bkpts = {}
        for addr, bkpt in list(self.bkpts.items()):
            line = bkpt.get_line()
            tab = bkpt.get_tab()
            newaddr = self.code.address_from_line(line, tab)
            if newaddr is None:
                # not valid insn line any more
                self.clear_breakpoint(bkpt)
            else:
                if newaddr in bkpts:
                    # duplicate
                    self.clear_breakpoint(bkpt)
                else:
                    bkpts[newaddr] = bkpt
                    bkpt.set_addr(newaddr)
        self.bkpts = bkpts

    def send_command(self, print_err=True):
        """Send command at address self.addr to devices.
        State assumed to be READY, and moves to RUNNING.
        Since this is a long-running process, this function
        returns immediately and it is the responsibility of the
        I/O processor to manage communication.
        """
        bincode, fast, instant, nxtaddr, insnlist = self.code.binary_from_address(self.addr)
        #print "send_command", self.addr, fast, instant, nxtaddr
        if bincode is None:
            self.state = Devices.READY
            err = self.code.err
            self.code.err = None
            if print_err:
                print(err)
            return err
        else:
            self.state = Devices.RUNNING
            if nxtaddr < 0:
                nxtaddr = self.addr + len(bincode)
            #instant = False
            self._write_insn(bincode, fast, instant, nxtaddr)
            # Inform Device object(s) of the instruction that is being executed.  This
            # permits certain state adjustments like position offsets (which are insn-
            # dependent).
            for d in self.devs:
                if d is not None:
                    d.executing_insns(insnlist)
            return None
    def send_pause(self):
        if self.state == Devices.RUNNING:
            self._send_pause()
            self.state = Devices.PAUSED
        elif self.state == Devices.READY:
            self.state = Devices.HOLD
    def send_resume(self):
        if self.state == Devices.HOLD:
            self.state = Devices.READY
        elif self.state == Devices.PAUSED:
            self._send_resume()
            self.state = Devices.RUNNING
    def send_stop(self):
        self._send_stop()
        self.stepping = Devices.STOPPED
        self.state = Devices.READY
    def send_estop(self):
        self._send_estop()
        self.stepping = Devices.STOPPED
        self.state = Devices.READY
        self._send_pgm_ctr(0)

    def assembly_valid(self):
        """Check for object code valid and matching text buffer.
        """
        if self.code.mod_asm:
            return False
        return self.code.assembly_done()

    def can_flash(self):
        return self.assembly_valid() and self.is_ready()
    def can_assemble(self):
        return self.code.mod_asm
    def can_step(self):
        return self.is_ready() and self.assembly_valid()
    def can_breakpoint(self):
        return self.assembly_valid()

    def _send_pgm_ctr(self, addr):
        self.addr = addr
        self.update_exec_pointer()
    def restart_program(self, newaddr=0):
        if self.is_ready():
            self.stepping = Devices.STOPPED
            self._send_pgm_ctr(newaddr)
    def set_exec_at_cursor(self, tab):
        if self.is_ready():
            iter = tab.buf().get_iter_at_mark(tab.buf().get_insert())
            line = iter.get_line()
            addr = self.code.address_from_line(line, tab)
            if addr is not None:
                self.restart_program(addr)
    def _dummy_done(self):
        # Base class only: instant done
        self.addr = self.next_addr
        self._done()
    def single_step(self):
        if self.can_step():
            self.stepping = Devices.STEP_INSN
            self.send_command()
            self._dummy_done()
    def step_to_next(self):
        if self.addr < len(self.code.obj):
            insn = self.code.obj[self.addr]
            if not insn.is_nextable():
                # 'next' does not make sense, so step instead.
                self.single_step()
                return
        self.addrmatch = self.addr + 1
        self.run_until_break(Devices.RUN_UNTIL_BREAK_OR_ADDRMATCH)
    def run_until_break(self, typ=None):
        if self.can_step():
            self.stepping = typ or Devices.RUN_UNTIL_BREAK
            while self.stepping == Devices.RUN_UNTIL_BREAK and self.send_command() is None:
                self._dummy_done()
    def stop(self):
        if self.is_connected() and not self.is_ready():
            self.stepping = Devices.STOPPED
    def pause(self):
        if self.state == Devices.READY or self.state == Devices.RUNNING:
            self.send_pause()
    def resume(self):
        if self.state == Devices.HOLD or self.state == Devices.PAUSED:
            self.send_resume()
    def cancel(self):
        if self.state == Devices.HOLD or self.state == Devices.PAUSED:
            #FIXME: 'cancel' really should just stop in current position, under control,
            # but the device STOP command lets the current command complete.
            self.send_stop()
    def estop(self):
        if self.is_connected():
            self.send_estop()
            self.restart_program()
    def assemble(self, top_tab, options):
        """Called from UI to assemble code in top_tab.  options is a PersistentProject
        object, which is used to provide appropriate assembly options (such as library folder
        search order).
        """
        if self.can_assemble():
            self.ui.clear_error_list()
            self.ui.hide_error_list()
            self.ui.unhighlight_error()
            self.code.assemble(top_tab, options)
            # Add errors to ui error list (tree view model)
            for ei in range(0,self.code.semantic_error_count()):
                line = self.code.get_error_line(ei)
                msg = self.code.get_error_text(ei)
                tab = self.code.get_error_tab(ei)
                self.ui.add_error_list(tab.get_filename_str(), line+1, msg, ei)
                self.ui.show_error_list()
            self.update_exec_pointer()
            self.adjust_breakpoints()
    def make_listing(self, list_tab):
        if self.assembly_valid():
            self.code.make_listing(list_tab)
    def flash(self):
        if not self.can_flash():
            return False
        return True
    def cancel_flash(self):
        pass
    def input_sim_update(self, mask):
        pass

class RS485Devices(Devices):
    """Represents real device chain on RS485 port.
    """
    # Command codes currently understood by GM215...
    CMD_ESTOP = 0
    CMD_STOP = 1
    CMD_PAUSE = 2
    CMD_RESUME = 3
    CMD_RUN = 4
    CMD_FLASH = 5
    CMD_FIRMWARE = 6
    CMD_QSHORT = 7
    CMD_QLONG = 8
    CMD_SETPC = 9
    CMD_SETPAGE = 10
    CMD_READBACK = 11
    CMD_ERASE = 12
    CMD_ENDFLASH = 0xFFFF
    CMD_INSIM = 13

    # Flash ROM states
    FLASH_NONE = 0          # Not programming
    FLASH_WAIT = 1          # Waiting for response ('P' or 'E' or 'F')
    FLASH_WAIT_CAN = 2      # Waiting for cancel response ('P' or 'E' or 'F')
    FLASH_READBACK = 3      # Waiting for readback data (until timeout)

    def __init__(self):
        super(RS485Devices, self).__init__()
        self._state = Devices.DISCONNECTED
        self.fd = -1    # Serial port file descriptor
        self.f = None   # Serial port file object
        self.fdtags = None
        self.idle_tag = None
        self.r_handler = None
        self.n_expect = 0
        self.wait_rdy = False
        self.inst_done = False
        self.pollt = time.time()
        self.flash_state = self.FLASH_NONE
        self.flash_write_time = None
        self.new_insim_state = 0

    def target_name(self):
        return "GM215"

    def add_fd(self, fd, rdcb=None, wrcb=None):
        # Add file descriptor for r and/or w callback.
        # Callbacks should take parameters (fd,cond),
        # fd is file descriptor, cond will be GObject.IO_* as appropriate.
        rtag = GObject.io_add_watch(fd, GObject.IO_IN, rdcb) if rdcb else None
        wtag = GObject.io_add_watch(fd, GObject.IO_OUT, wrcb) if wrcb else None
        return (rtag, wtag)
    def remove_fd(self, tags):
        for tag in tags:
            if tag is not None:
                GObject.source_remove(tag)

    def log_resp(self, x, typ):
        if self.trace:
            print(typ, "recv", len(x), ":", ' '.join(["%02X" % c for c in x]))

    def x_qs_resp(self, x):
        """Handle 4-byte query short response from X axis only"""
        flgs, pc = struct.unpack("<HH", x)
        try:
            self.devs[0].flags = flgs
            self.devs[0].pc = pc
            self.addr = pc  # Set "overall" address (always have X axis!)
            self.gui_data.actions.append(lambda gui: gui.update_status(0, self.devs[0]))
        except AttributeError:
            print("Axis 0 discovered by short query")
            self._send_qlong(True)

    def yzw_qs_resp(self, x):
        """Handle 2-byte query short response from axes other than X"""
        flgs = struct.unpack("<H", x)[0]
        axisnum = flgs & Device.MASK_AXISNUM
        try:
            dev = self.devs[axisnum]
            dev.flags = flgs
            dev.pc = self.devs[0].pc    # Assume others at same PC (avoid off-by-1 errors)
            self.gui_data.actions.append(lambda gui: gui.update_status(axisnum, dev))

        except AttributeError:
            print("Axis", flgs & Device.MASK_AXISNUM, "discovered by short query")
            self._send_qlong(True)

    def handle_qshort(self, x):
        self.log_resp(x, "qshort")
        #if len(x) & 1:
        # Discard initial sync char (which is a zero with framing error, then 0xFF; or sometimes just a single
        # zero or 0xFF)
        if len(x) & 1:
            x = x[1:]
        else:
            x = x[2:]
        if len(x) >= 4:
            self.x_qs_resp(x[0:4])
            if len(x) >= 6:
                self.yzw_qs_resp(x[4:6])
                if len(x) >= 8:
                    self.yzw_qs_resp(x[6:8])
                    if len(x) >= 10:
                        self.yzw_qs_resp(x[8:10])
        else:
            # Devices do not respond to RUN, so issue qlong...
            self._send_qlong()
            return
        self.test_rdy()

    def test_rdy(self):
        if not self.wait_rdy:
            return
        for d in self.devs:
            if d is not None and d.is_busy():
                return
        self.wait_rdy = False
        time.sleep(0.002)
        self._done()

    def discard(self, x):
        pass

    def handle_qlong(self, x):
        self.log_resp(x, "qlong")
        x = x[len(x) % 10:]     # Ignore initial part not multiple of 10 length
        self._handle_qlong(x)

    def handle_initial_qlong(self, x):
        """Initial qlong response.  This is handled specially (after connecting) in order to create the
        set of devices which are detected on the RS485 bus.
        """
        self.log_resp(x, "initial qlong")
        x = x[len(x) % 10:]     # Ignore initial part not multiple of 10 length
        self.devs = [None]*4
        self.n_devs = 0
        for n in range(0,len(x),10):
            flg, pc = struct.unpack("<HH", x[n:n+4])
            axisnum = flg & Device.MASK_AXISNUM
            if self.devs[axisnum]:
                print("Duplicate axis", axisnum, "in single response!")

            else:
                self.devs[axisnum] = GM215Device("XYZW"[axisnum], axisnum)
                self.n_devs += 1
                print("Detected axis", axisnum, "=", self.devs[axisnum].axisname)
        self._handle_qlong(x)
        for n in range(4):
            if self.devs[n] is None:
                self.gui_data.actions.append(lambda gui: gui.update_status(n, None))
        if not self.n_devs:
            # Lost contact with all devices.
            self.gui_data.actions.append(lambda gui: gui.device_notify("No response from any device."))
    def _handle_qlong(self, x):
        for d in self.devs:
            if d is not None:
                d.noqresp += 1
        for n in range(0,len(x),10):
            flg, pc, pos, vel = struct.unpack("<HHIH", x[n:n+10])
            if vel & 0x8000:
                vel &= 0x7FFF
            else:
                vel = -vel
            pos = int(pos>>8 & 0xFFFFFF)
            axisnum = flg & Device.MASK_AXISNUM
            if axisnum == 0:
                self.addr = pc  # Set "overall" address (always have X axis!)
            try:
                dev = self.devs[axisnum]
                dev.flags = flg
                dev.pc = pc
                dev.pos = pos
                dev.vel = vel
                dev.vin = 0
                dev.noqresp = 0
            except AttributeError:
                # Axis added dynamically (currently missing Device object), then handle that case
                print("Axis", axisnum, "discovered after initial query")
                self.handle_initial_qlong(x)
                return
            self.gui_data.actions.append(lambda gui: gui.update_status(axisnum, dev))
        # Check for timely responses
        msg = ''

        if self.trace:
            for d in self.devs:
                if d is not None:
                    print("Device %s is at program counter 0x%04X, insn_len = 0x%04X\n" % (d.axisname, d.pc, self.insn_len))

        for d in self.devs:
            if d is not None:
                if d.noqresp > 1:
                    msg += "Device %s not responding\n" % (d.axisname,)
                elif d.error_state():
                    msg += "Device %s is signalling %s error\n" % (d.axisname, "-PFB"[d.error_state()])
                elif d.pc < self.addr-self.insn_len or d.pc > self.addr+self.insn_len:
                    msg += "Device %s is at inconsistent program counter 0x%04X (should be 0x%04X)\n" % \
                        (d.axisname, d.pc, self.addr)
        if msg:
            # Some error.  Purge any unread data.
            self.f.timeout = 0.05
            self.f.read(256)
            self.gui_data.actions.append(lambda gui: gui.device_notify(msg))
            self.n_devs = 0 # Force initial query

        self.test_rdy()

    def handle_poll(self, x):
        self.log_resp(x, "poll")

    def expect(self, n, handler):
        """Called after writing command to serial port.  Specify expected
        number of bytes to read.
        """
        if n:
            self.f.timeout = self.ui.get_resp_timeout()
            x = self.f.read(n)
            handler(x)

    def idle_func(self):
        """This is used (via idle_add()) to process any unsolicited serial data.
        Depends on timeout of about 5-10ms to achieve reasonable CPU utilization.
        """

        if self.f == None:
            # serial port is not connected
            return

        try:
            self.f.timeout = 0.005
            if self.flash_state == self.FLASH_WAIT:
                x = self.f.read(2)
                if len(x):
                    self.handle_flash_resp(x)
                '''  Not doing this...  always wait for response...
                if self.flash_write_time is not None and time.time() - self.flash_write_time > 0.03:
                    # 30ms has passed.  Assume write complete (this if no 'P' received)
                    self.flash_write_time = None
                    self.flash_continue()
                '''
                return True
            elif self.flash_state == self.FLASH_WAIT_CAN:
                if self.flash_write_time is not None and time.time() - self.flash_write_time > 0.03:
                    self.flash_write_time = None
                    self.handle_flash_can_resp('EE')

            elif self.flash_state == self.FLASH_READBACK:
                x = self.f.read(256)
                if len(x):
                    self.handle_flash_readback(x)
                else:
                    # Rx timeout, so drop back to normal mode
                    self.flash_state = self.FLASH_NONE
                return True
            elif self.new_insim_state != self.insim_state:
                self._send_insim()
                return True
            elif self.inst_done:
                self.wait_rdy = False
                self.inst_done = False
                self.addr = self.next_addr
                self._done()
                return True
            self.f.timeout = 0.
            x = self.f.read(128)
            if len(x):
                self.log_resp(x, "unsolicited")
                self._send_qlong(initial=True)
            else:
                if self.send_next_command:
                    self.send_next_command = False
                    err = self.send_command(False)
                    if err is None:
                        return True # remain in RUNNING state
                    # Else halt (error)
                    self.stepping = Devices.STOPPED
                    self.state = Devices.READY
                # Else poll using qlong...
                # The 'running' test inhibits polling when ready for next instruction, however this
                # prevents updating the status display (e.g. for I/O) so leave it out.
                elif self.ui.get_polling(): # and self.state == self.RUNNING:
                    t = time.time()
                    if t-self.pollt > self.ui.get_poll_rate():
                        self.pollt = t
                        self._send_qlong()

            return True # Reinstate callback
        except serial.SerialException as sx:
            import traceback
            traceback.print_exc()
            print("Serial error:", str(sx))
            self._disconnect()
            return False
        except ValueError as sx:
            # Get this on Windows (usually when setting timeout parameter)
            import traceback
            traceback.print_exc()
            print("Serial error:", str(sx))
            self._disconnect()
            return False

    def _connect(self, devname):
        """Open serial port with given device node name e.g. /dev/ttyUSB0 on Linux.
        Return True if OK (with state set to READY), else post error message dialog then return False.
        """
        print("Connecting to", devname)
        try:
            self.f = serial.Serial(devname, 115200, timeout=0.02)
        except serial.SerialException as sx:
            self.conn_error = str(sx)
            return False

        self.state = Devices.READY
        self.insim_state = -1   # Unknown
        self._send_qlong(True)
        return True
    def _disconnect(self):
        """Close serial port, set state to DISCONNECTED.
        """
        if self.state != Devices.DISCONNECTED:
            print("Disconnecting", self.devname)
            self.state = Devices.DISCONNECTED
            if self.fdtags is not None:
                self.remove_fd(self.fdtags)
                self.fdtags = None
            if self.idle_tag is not None:
                self.idle_tag = None
            self.f.close()
            self.f = None
            self.fd = -1
    def get_serport_list(self):
        """Return list of serial ports.
        Note: this is called each time the user drops down the setting->serialPort combo box.
        Under windows, the simple port name is returned e.g. COM1.
        Under Linux, we look in /dev/serial/by-id, but return only the file name therein (actually a link).
          If the user adds ports manually via the entry, then full path names must be used to distinguish
          from the ones in by-id.
        Return is list of tuples.  Each tuple contains two strings:
          - actual device node to pass to serial.Serial()
          - "human readable" version.  On Windows, this might be expanded out to
             something like "USB Serial Port (COM4)".  On Linux, it might be a contraction of
             the full path to be just relative to /dev/serial/by-id.
        """
        try:
            if have_lports:
                # Available since pyserial 2.6
                spl = [(p, d) for p, d, hw in lports.comports()]
            elif sys.platform == 'win32':
                spl = [(win32_full_port_name(x), x) for x in win32_enumerate_serial_ports()]
            else:
                base = "/dev/serial/by-id"
                spl = [(os.path.join(base, x), x) for x in os.listdir(base)]
        except:
            spl = []
        return spl

    def _write_insn(self, binlist, fast, instant, nxtaddr):
        """binlist is list of 1-4 32-bit words, which is the next instruction
        to send.
        """
        self.wait_rdy = True
        self.insn_len = len(binlist)
        # Argh! Want high 16 bits before low!
        data = [(d&0xFFFF)<<16|(d&0xFFFF0000)>>16 for d in binlist]
        self._send_cmd(self.CMD_RUN, 0, packfmt="I"*len(data), args=tuple(data))
        if instant:
            # Does not need any query round-trip time
            self.inst_done = True
            self.next_addr = nxtaddr
        elif fast:
            # Does not need full query (no pos or velocity change)
            self._send_qshort()
        else:
            self._send_qlong()
    def _send_pause(self):
        self._send_cmd(self.CMD_PAUSE, 0)
    def _send_resume(self):
        self._send_cmd(self.CMD_RESUME, 0)
    def _send_stop(self):
        self._send_cmd(self.CMD_STOP, 0)
    def _send_estop(self):
        self.wait_rdy = False
        self.flash_state = self.FLASH_NONE
        self._send_cmd(self.CMD_ESTOP, 0)
        for d in self.devs:
            if d is not None:
                d.reset_offset();
    def _send_qshort(self):
        self._send_cmd(self.CMD_QSHORT, 6+2*self.n_devs, self.handle_qshort)
        pass
    def _send_qlong(self, initial=False):
        if initial or not self.n_devs:
            self._send_cmd(self.CMD_QLONG, 42, self.handle_initial_qlong)
        else:
            self._send_cmd(self.CMD_QLONG, 2+10*self.n_devs, self.handle_qlong)
    def _send_pgm_ctr(self, pc):
        self._send_cmd(self.CMD_SETPC, 0, packfmt="H", args=(pc,))
        self._send_qlong()  # Get updated PC etc.
        self.update_exec_pointer()
    def _send_run(self, data):
        self.wait_rdy = True
        # Argh! Want high 16 bits before low!
        data = [(d&0xFFFF)<<16|(d&0xFFFF0000)>>16 for d in data]
        self._send_cmd(self.CMD_RUN, 1, self.discard, packfmt="I"*len(data), args=tuple(data))
        self._send_qlong()  # Get updated PC etc.
    def _poll(self):
        self.expect(100, self.handle_poll)
    def _send_readback(self, axis_num):
        self.flash_state = self.FLASH_READBACK
        self._send_cmd(self.CMD_READBACK + (axis_num<<8), 0, self.discard)
    def _send_erase(self):
        self._send_cmd(self.CMD_ERASE, 0, self.discard)
    def _send_insim(self):
        self._send_cmd(self.CMD_INSIM, 0, self.discard, packfmt="H", args=(self.new_insim_state,))
        self.insim_state = self.new_insim_state
    def _send_cmd(self, cmd, expect, handler=None, packfmt="", args=(), bindata=None):
        if not self.f:
            return
        dly = self.ui.get_char_delay()
        cmddly = self.ui.get_cmd_delay()
        s = struct.pack("<H%s" % packfmt, cmd, *args)
        if bindata is not None:
            s += bindata
        #self.f.flush(); time.sleep(0.02)   #FIXME testing
        # s is always even length
        try:
            for n in range(0,len(s),2):
                self.f.write(s[n:n+2])
                self.f.flush()
                if cmddly > 0. and n+2 < len(s):
                    time.sleep(cmddly)
                cmddly = dly
            if self.trace:
                print("sent", len(s), "bytes:", ' '.join(["%02X" % c for c in s]))
            self.expect(expect, handler)
        except serial.SerialException as sx:
            traceback.print_exc()
            print("Serial error:", str(sx))
            self._disconnect()
        except Exception as ex:
            traceback.print_exc()
            print("generic serial error:", str(ex))
            print("No serial port")
            self._disconnect()

    def single_step(self):
        if self.can_step():
            self.stepping = Devices.STEP_INSN
            self.send_command()
    def run_until_break(self, typ=None):
        if self.can_step():
            self.stepping = typ or Devices.RUN_UNTIL_BREAK
            self.send_command()

    def flash(self):
        """Write object code to devices.

        """
        if not self.can_flash():
            return False
        self.flash_addr = 0
        self.flash_state = self.FLASH_WAIT
        block = self.code.get_block(self.flash_addr, 64)
        self.flash_addr += 64
        self._send_cmd(self.CMD_FLASH, 2, self.handle_flash_resp, bindata=block)
        return True
    def cancel_flash(self):
        self.flash_state = self.FLASH_WAIT_CAN
        self._send_cmd(self.CMD_ENDFLASH, 2, self.handle_flash_can_resp)

    def handle_flash_resp(self, x):
        self.log_resp(x, "flash")
        if not len(x):
            return
        if x == 'PP':
            self.flash_continue()
        elif x.startswith('E'):
            self.flash_complete()
        else:  # Don't get this any more.  Error reported in status bit.
            self.flash_fail()
    def handle_flash_readback(self, x):
        self.log_resp(x, "readback")    # For now, just log.
    def handle_flash_can_resp(self, x):
        self.log_resp(x, "flash cancel")
        if not len(x):
            return
        self.flash_state = self.FLASH_NONE

    def flash_continue(self):
        # Got 'PP' response.  Send next 256 bytes (64 locations), padding if necessary with GOTO 0.
        self.gui_data.actions.append(lambda gui: gui.set_flash_progress(self.flash_addr, self.code.get_obj_len()))
        if self.flash_addr >= self.code.get_obj_len():
            print("flash sending EOF")
            self.flash_state = self.FLASH_WAIT
            self._send_cmd(self.CMD_ENDFLASH, 2, self.handle_flash_resp)
        else:
            print("flash sending block addr", self.flash_addr)
            block = self.code.get_block(self.flash_addr, 64)
            self.flash_addr += 64
            self.flash_state = self.FLASH_WAIT
            #time.sleep(0.003)   # Give a little extra time for all flashes to write (after rx 'P')
            self.f.write(block)
            self.flash_write_time = time.time()
    def flash_complete(self):
        print("flash complete")
        self.gui_data.actions.append(lambda gui: gui.flash_done("Programming complete."))
        self.flash_state = self.FLASH_NONE
    def flash_fail(self):
        print("flash fail")
        self.gui_data.actions.append(lambda gui: gui.flash_done("Programming error encountered."))
        self.flash_state = self.FLASH_NONE

    def input_sim_update(self, mask):
        self.new_insim_state = mask
        # Actual device update in idle func (since we can get a flurry of these)
