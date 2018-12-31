import re, os, math, sys, traceback, tokenize, io, struct


class CodeError(Exception):
    def __init__(self, primary_location, primary_msg, *args):
        """General semantic assembly error.
        Parameters:
        -- primary_location: AddressMark object indicating primary error source location.
             If None, will be filled in later with set_location() or set_line_tab()
        -- primary_msg: string message
        -- *args: pairs of secondary locations, message additional information (if any)
        """
        if primary_location is not None:
            primary_location.get_mark()
        self.all = [(primary_location, primary_msg)]
        for a in range(0, len(args), 2):
            # extract pairs of (location, msg) for secondary messages
            args[a].get_mark()
            self.all.append((args[a], args[a+1]))
    def get_all(self):
        """"Return list of (location,msg) tuples, including primary."""
        return self.all
    def append(self, loc, msg):
        if loc is None:
            loc = self.all[0][0]
        else:
            loc.get_mark()
        self.all.append((loc, msg))
    def set_location(self, loc):
        self.all[0] = (loc, self.all[0][1])
    def set_line_tab(self, line, tab):
        am = AddressMark(line, tab)
        am.get_mark()
        self.all[0] = (am, self.all[0][1])

class LineError(CodeError):
    """Exception class for errors detected at line in tab"""
    def __init__(self, line, tab, msg):
        super(LineError, self).__init__(AddressMark(line, tab), msg)

class ScanError(CodeError):
    """Exception class for errors detected during scanning.  These need to
    be caught, and the line+tab info filled in using set_line_tab(), then re-raised or handled."""
    def __init__(self, msg):
        super(ScanError, self).__init__(None, msg)

class FatalError(LineError):
    """Exception class used to signal errors which abort current assembly, such
    as being unable to access an imported file, or exceeding a threshold error count.
    """
    pass

class AddressMark(object):
    """Base class for all things which can end up with an address in the object code,
    including breakpoints and all types of instruction.
    
    Note that the address may be unresolved, in which case get_addr() will return None.
    We always keep a valid tab (i.e. buffer for the source text) and a mark in that
    text.  The mark is created from the given source line number (0-based) but thereafter
    the line may move around with edits, so only the text mark is actually stored,
    from which the current line number can be retrieved using get_line().
    
    Efficiency note: assembly would spend a lot of time creating source marks if
    we created the mark in __init__, so this is deferred at the cost of some storage.
    A mark is created only for AddressMarks which persist over possible edits to the source.
    """
    def __init__(self, line, tab, category=None):
        self.addr = None
        self.tab = tab
        self.line = line
        self.mark = None
        if isinstance(category, str):
            # More fancy e.g. for breakpoints (create now)
            tbuf = tab.buf()
            self.mark = tbuf.create_source_mark(None, category, tbuf.get_iter_at_line(self.line))
            self.mark.set_visible(False)
    def get_mark(self):
        if self.mark:
            return self.mark
        # Simple (invisible) for remembering line positions
        self.mark = self.tab.buf().create_mark(None, self.tab.buf().get_iter_at_line(self.line), True)
        return self.mark
    def get_addr(self):
        return self.addr
    def get_tab(self):
        return self.tab
    def set_addr(self, addr):
        self.addr = addr
    def get_tbuf(self):
        """Return the text buffer"""
        return self.tab.buf()
    def get_iter(self):
        """Return actual position (TextIter) in text buffer."""
        tbuf = self.get_tbuf()
        if tbuf is None:
            return None
        if self.mark:
            return tbuf.get_iter_at_mark(self.mark)
        return tbuf.get_iter_at_line(self.line)
    def get_line(self):
        """Return current line (0-based, may have moved if edits)
        """
        iter = self.get_iter()
        if iter is None:
            return -1
        return iter.get_line()
    def get_line_text(self):
        tbuf = self.get_tbuf()
        if tbuf is None:
            return "???"
        si = self.get_iter()
        ln = si.get_line()
        if ln+1 < tbuf.get_line_count():
            ei = tbuf.get_iter_at_line(si.get_line()+1)
            return tbuf.get_text(si, ei, False)
        else:
            ei = tbuf.get_end_iter()
            t = tbuf.get_text(si, ei, False)
            if not t.endswith('\n'):
                return t + '\n' # force CR at end
            return t
        
    def delete_mark(self):
        """Delete mark if not already.  This should be done prior to deleting
        this object, although it is done automatically at garbage collection time.
        """
        if self.mark is None:
            return
        if self.mark.get_deleted():
            return
        tbuf = self.get_tbuf()
        if tbuf is None:
            return
        tbuf.delete_mark(self.mark)
        # Actually de-ref the mark, to catch invalid use of this object
        del self.mark
    def __del__(self):
        if hasattr(self, 'mark'):
            self.delete_mark()
        
class Label(AddressMark):
    def __init__(self, line, tab, addr=None):
        """Create new label for address 'addr' at 'line' (0-based) in TextBuffer tbuf.
        Adds a label mark in tbuf.
        
        If addr is not None, then this label is already resolved, otherwise it remains for
        later resolution.  At this level, labels are not aware of their own namespace or
        even their name.  Those are managed by the caller in symbol tables.
        """
        super(Label, self).__init__(line, tab)
        self.set_addr(addr)
    def set_block_index(self, bi):
        self.block_index = bi
    def set_block_insn_index(self, bii):
        self.block_insn_index = bii
    def get_block_index(self):
        return self.block_index
    def get_block_insn_index(self):
        return self.block_insn_index
    def is_resolved(self):
        return self.get_addr() is not None
        
class Bkpt(AddressMark):
    def __init__(self, line, tab, addr):
        """Create breakpoint at address 'addr' at 'line' (0-based) in TextBuffer tbuf.
        Adds a bkpt source mark in tbuf.
        
        Breakpoints always have a resolved address.  When the text is recompiled, existing
        breakpoints have their addresses adjusted if possible (else they are deleted).
        """
        super(Bkpt, self).__init__(line, tab, category='bkpt')
        self.set_addr(addr)
        
class Insn(AddressMark):
    def __init__(self, line, tab):
        """Abstract Base Class for all instructions.
        
        Note that several insns can be created from one source line when the
        comma separator is used for multiple axes.
        """
        super(Insn, self).__init__(line, tab)
        self.insn = 0xFFFFFFFF      # Actual object code (32-bit int).  Default to -1 to help
                                    # catch bugs.
    def get_binary(self):
        return self.insn
    def set_branch_field(self, value):
        self.insn &= 0xFFFF0000
        self.insn |= value & 0xFFFF
    def get_branch_field(self):
        return self.insn & 0xFFFF
    def set_lower_16(self, value):
        self.insn &= 0xFFFF0000
        self.insn |= value & 0xFFFF
    def set_lower_24(self, value):
        self.insn &= 0xFF000000
        self.insn |= value & 0xFFFFFF
    def set_lower_24_sign_mag(self, value):
        sign = 1
        if value < 0:
            sign = 0    # Yeah wierd: 0 sign for negative.
            value = -value
        self.insn &= 0xFF000000
        self.insn |= value & 0x7FFFFF
        self.insn |= sign << 23
    def set_lower_24_swapped(self, value):
        # Used in VELOCITY, ACCELERATION: shifted so that LSB is in command data field
        self.insn &= 0xFF000000
        self.insn |= (value & 0xFF) << 16
        self.insn |= value >> 8 & 0xFFFF
    def set_lower_24_swapped_sign_mag(self, value):
        # Used in SPEED CONTROL: shifted so that LSB is in command data field, sign magnitude
        # with sign bit in LSW[15]
        self.insn &= 0xFF000000
        sign = 0
        if value < 0:
            sign = 1
            value = -value
        self.insn |= (value & 0xFF) << 16
        self.insn |= value >> 8 & 0x7FFF
        self.insn |= sign << 15
    def set_upper_2(self, value):
        self.insn &= 0x3FFFFFFF
        self.insn |= (value & 0x3) << 30
    def set_upper_8(self, value):
        self.insn &= 0x00FFFFFF
        self.insn |= (value & 0xFF) << 24
    def get_upper_8(self):
        return self.insn >> 24 & 0xFF
    def set_command_data(self, value):
        self.insn &= 0xFF00FFFF
        self.insn |= (value & 0xFF) << 16
    def get_command_data(self):
        return self.insn >> 16 & 0xFF
    def set_opcode_6(self, value):
        self.insn &= 0xC0FFFFFF
        self.insn |= (value & 0x3F) << 24
    def set_opcode(self, value):    # standard 5-bit opcode
        self.insn &= 0xE0FFFFFF
        self.insn |= (value & 0x1F) << 24
    def set_sub_command(self, value):    # standard 3-bit sub-command
        self.insn &= 0x1FFFFFFF
        self.insn |= (value & 0x7) << 29
    def set_chain(self, chain):     # standard chain bit
        self.insn &= 0xDFFFFFFF
        self.insn |= (int(bool(chain)) & 0x1) << 29
    def get_chain(self):
        return (self.insn & 0x20000000) != 0
        
    def is_unresolved_branch(self):
        # Base class default is non-control-flow
        return False
    def is_chained(self):
        """Return whether this insn chains to the next.  Currently, this
        is only possible for MOVE, HOME or JOG."""
        return False
    def is_end_of_block(self):
        """Return whether this is an end-of-block branch i.e. the following
        instruction cannot execute unless it is labelled and somewhere else jumps to it.
        Currently, just unconditional GOTO, and RETURN"""
        return False
    def get_branch(self):
        """Return branch address.  Default None for all except GOTO, IF or CALL."""
        return None
    def is_nextable(self):
        """Return whether this instruction makes sense for the "step next" command.
        This applies to CALL, IF and GOTO with loop count."""
        return False
    def is_fast(self):
        """Return whether this insn is fast running i.e. a single query short
        will be sufficient to update local status.  Most insns are fast except
        for MOVE, HOME, JOG, SPEED CONTROL, WAIT."""
        return True
    def is_instant(self):
        """Return whether this insn is_fast() AND the next instruction address can be determined statically.
        If so, then a round-trip time to retrieve status/PC can be avoided.
        Return value is a (bool, int) tuple.  bool is True if instant, and int value (if True)
        is the next address.  The next address may only be valid after labels are resolved.
        Insns do not store their own address, so in the common case of returning 'addr+1', the
        int value is set to -1.
        """
        return (False, 0)
    def is_pos_valid(self):
        """Return whether reported position (from query long) is valid during execution of
        this instruction.  True except for HOME and SPEED CONTROL.
        """
        return True
    def is_vel_valid(self):
        """Return whether reported velocity (from query long) is valid during execution of
        this instruction.  Currently, always true.
        """
        return True
    def is_reset_offset(self):
        """Return whether this insn resets the device offset so that it should read as '0'
        from the current device position.  Currently, only the RESPOS insn does this.
        If returns True, then there must be a get_reset_offset() method which returns the
        device position which is reported to the user as '0'.
        """
        return False;
        

class ControlFlowInsn(Insn):
    """ABC for all control-flow instructions.
    """
    def __init__(self, line, tab, dest_label):
        super(ControlFlowInsn, self).__init__(line, tab)
        self.branch = dest_label    # If a str, then is a forward ref which need fixup.
                                    # Otherwise, is a Label object.  May also be None
                                    # for implicit branches like RETURN.
    def is_unresolved_branch(self):
        return isinstance(self.branch, str)
    def get_branch(self):
        return self.branch
    def set_branch(self, lab):
        """Resolve forward-ref label, to Label object lab.
        Currently, addresses are always stored in low word of insn code.
        """
        self.branch = lab
        self.set_branch_field(lab.get_addr())
        
class GotoInsn(ControlFlowInsn):
    """Goto instructions.
    GOTO label [LOOP n TIMES]
    """
    def __init__(self, line, tab, dest_label, n=0):
        super(GotoInsn, self).__init__(line, tab, dest_label)
        self.set_upper_8(0x03)  # GOTO
        self.set_command_data(n)
        if n < 0 or n > 255:
            raise CodeError(self, "Loop count %d out of range [0..255]" % n)
    def is_end_of_block(self):
        return self.get_command_data() == 0  # no loop count
    def is_nextable(self):
        return self.get_command_data() != 0 # i.e. not unconditional
    def is_instant(self):
        if self.get_command_data() == 0:
            return (True, self.get_branch_field())
        return (False, 0)

class ConditionalInsn(ControlFlowInsn):
    """Conditional instructions.
    IF axis flag IS state GOTO label
    axis is 0,1,2,3 for X,Y,Z,W
    flag is ConditionalInsn.{IN1|IN2|IN3|RDY|ERR|VEL|POS|VIN}
    state is bool (for OFF|ON) or ConditionalInsn.{OFF|ON|LT|EQ|GT}
    """
    IN1 = 0
    IN2 = 1
    IN3 = 2
    RDY = 3
    ERR = 4
    VEL = 5
    POS = 6
    VIN = 7
    
    OFF = 0
    ON = 1
    LT = 2
    EQ = 3
    GT = 4
    def __init__(self, line, tab, axis, flag, state, dest_label):
        super(ConditionalInsn, self).__init__(line, tab, dest_label)
        self.axis = axis
        self.set_upper_2(axis)
        self.set_opcode_6(0x05)
        if flag < 0 or flag > 7:
            raise CodeError(self, "Bad conditional source flag %d" % flag)
        if isinstance(state, bool):
            self.set_command_data((1 if state else 0)<<5 | flag&7)
        else:
            if state < 0 or state > 4:
                raise CodeError(self, "Bad conditional state %d" % state)
            self.set_command_data(state<<5 | flag&7)

class CallInsn(ControlFlowInsn):
    """Call instructions.
    CALL label
    """
    def __init__(self, line, tab, dest_label):
        super(CallInsn, self).__init__(line, tab, dest_label)
        self.set_upper_8(0x04)  # CALL
        self.set_command_data(0)
    def is_nextable(self):
        return True
    def is_instant(self):
        return (True, self.get_branch_field())
        
class ReturnInsn(ControlFlowInsn):
    """Return instructions.
    RETURN
    
    This is treated as a control flow, which it is, however there is no
    explicit branch address so it is always 'resolved' but returns None
    for get_branch().
    """
    def __init__(self, line, tab):
        super(ReturnInsn, self).__init__(line, tab, None)
        self.set_upper_8(0x12)  # RETURN
        self.set_command_data(0)
        self.set_lower_16(0)
    def is_unresolved_branch(self):
        return False
    def is_end_of_block(self):
        return True
    def get_chain(self):
        """Override default use of bit 13, this is special case"""
        return False

class AxisInsn(Insn):
    """ABC for all axis-specific instructions i.e. the opcode starts with X,Y,Z or W.
    """
    def __init__(self, line, tab, axis):
        """axis parameter is 0,1,2,3 for X,Y,Z,W respectively"""
        super(AxisInsn, self).__init__(line, tab)
        self.axis = axis
        self.set_upper_2(axis)
    def is_chained(self):
        return self.get_chain()
        
class AxisMaskInsn(Insn):
    """ABC for all axis-mask instructions i.e. where X,Y,Z,W are option bits.
    """
    def __init__(self, line, tab, axes):
        """axes parameter is bitmask with 1,2,4,8 for X,Y,Z,W respectively"""
        super(AxisMaskInsn, self).__init__(line, tab)
        self.set_command_data(axes & 0x0F)
    def is_instant(self):
        return (True, -1)
        
class AnalogInputInsn(AxisMaskInsn):
    """Analogue input instructions
    ANALOG INPUTS TO axes
    """
    def __init__(self, line, tab, axes):
        super(AnalogInputInsn, self).__init__(line, tab, axes)
        self.set_upper_8(0x0A)
        self.set_lower_16(0)
                
class VectorAxesInsn(AxisMaskInsn):
    """Vector axes instructions
    VECTOR AXES|AXIS ARE|IS axes
    """
    def __init__(self, line, tab, axes):
        super(VectorAxesInsn, self).__init__(line, tab, axes)
        self.set_upper_8(0x0B)
        self.set_lower_16(0)
        
class ResPosInsn(AxisMaskInsn):
    """Reset position instructions
    RESPOS axes
    """
    def __init__(self, line, tab, axes):
        super(ResPosInsn, self).__init__(line, tab, axes)
        self.set_upper_8(0x15)
        self.set_lower_16(0)
    def is_reset_offset(self):
        return True;
    def get_reset_offset(self):
        return 0x3FFFFF;
        
class MovingAverageInsn(AxisMaskInsn):
    """Mavg instructions
    MOVING AVERAGE axes n SAMPLES
    """
    def __init__(self, line, tab, axes, n):
        super(MovingAverageInsn, self).__init__(line, tab, axes)
        self.set_upper_8(0x09)
        self.set_lower_16(n & 0x7F)
        if n < 0 or n > 127:
            raise CodeError(self, "Moving average sample count %d out of range [0..127]" % n)
        
class JogInsn(AxisMaskInsn):
    """Jog instructions.
    JOG axis,...
    """
    def __init__(self, line, tab, axes):
        super(JogInsn, self).__init__(line, tab, axes)
        self.set_upper_8(0x11)
        self.set_lower_16(0)
    def is_fast(self):
        return False
    def is_instant(self):
        return (False, 0)
        
        
class MoveInsn(AxisInsn):
    """Move instructions.
    axis [+|-]n [, ...]
    Note that several of these can be chained on one line (comma) but this class instance
    is created for one at a time.
    """
    def __init__(self, line, tab, axis, relative, n, chain):
        """relative is 0 for absolute, 1 or -1 for relative (specifying sign)."""
        super(MoveInsn, self).__init__(line, tab, axis)
        self.set_chain(chain)
        self.set_opcode(0x01 if relative else 0x00)
        if relative:
            n *= relative
            self.set_lower_24_sign_mag(n)
        else:
            self.set_lower_24(n)
        if relative and (n < -0x7FFFFF or n > 0x7FFFFF) or \
           not relative and (n < 0 or n > 0xFFFFFF):
            raise CodeError(self, "%s amount %d out of range for axis %d" % \
                ("Relative move" if relative else "Move", n, axis))
    def is_fast(self):
        return False
        
class HomeInsn(AxisInsn):
    """Home instructions.
    HOME axis,...
    Note that several of these can be chained on one line (comma) but this class instance
    is created for one at a time.
    """
    #FIXME: this insn would be better reformulated as axis bitfield type rather than chained
    def __init__(self, line, tab, axis, chain):
        super(HomeInsn, self).__init__(line, tab, axis)
        self.set_chain(chain)
        self.set_opcode(0x02)
        self.set_lower_24(0)
    def is_fast(self):
        return False
    def is_pos_valid(self):
        return False
        
class ConfigureInsn(AxisInsn):
    """Configure instructions.
    axis CONFIGURE i AMPS, IDLE AT p% AFTER s SECONDS
    i is float in range 0..7.0
    p is int in range 0..99
    s is float in range 0..25.5
    """
    def __init__(self, line, tab, axis, i, p, s):
        super(ConfigureInsn, self).__init__(line, tab, axis)
        if i < 0. or i > 7.:
            raise CodeError(self, "Current %f out of range [0..7.0]" % i)
        if p < 0. or p > 99.:
            raise CodeError(self, "Percent idle current %f out of range [0..99.0]" % p)
        if s < 0. or s > 25.5:
            raise CodeError(self, "Time to idle %f out of range [0..25.5]" % s)
        i = int(i*10)
        s = int(s*10)
        p = int(p)
        self.set_opcode_6(0x0E)
        self.set_command_data(i)
        self.set_lower_16(p<<8 | s)
    def is_instant(self):
        return (True, -1)

class ClockwiseLimitInsn(AxisInsn):
    """Clockwise Limit instructions.
    axis LIMIT CW n
    """
    def __init__(self, line, tab, axis, n):
        super(ClockwiseLimitInsn, self).__init__(line, tab, axis)
        self.set_opcode_6(0x0F)
        self.set_lower_24(n)
        if n < 0 or n > 0xFFFFFF:
            raise CodeError(self, "Clockwise limit %d out of range" % n)
    def is_instant(self):
        return (True, -1)

class CompareInsn(AxisInsn):
    """Compare instructions.
    axis COMPARE VALUE n
    """
    def __init__(self, line, tab, axis, n):
        super(CompareInsn, self).__init__(line, tab, axis)
        self.set_opcode_6(0x14)
        self.set_lower_24(n)
        if n < 0 or n > 0xFFFFFF:
            raise CodeError(self, "Compare value %d out of range" % n)
    def is_instant(self):
        return (True, -1)

class AccelerationInsn(AxisInsn):
    """Accel instructions.
    axis ACCELERATION n
    """
    def __init__(self, line, tab, axis, n):
        super(AccelerationInsn, self).__init__(line, tab, axis)
        self.set_opcode_6(0x0C)
        if n < 0 or n > 0xFFFF:
            raise CodeError(self, "Acceleration %f out of range" % float(n))
        self.set_lower_24_swapped(int(n)*256)
    def is_instant(self):
        return (True, -1)

class VelocityInsn(AxisInsn):
    """Velocity instructions.
    axis VELOCITY n
    """
    def __init__(self, line, tab, axis, n):
        super(VelocityInsn, self).__init__(line, tab, axis)
        self.set_opcode_6(0x07)
        if n < 0 or n > 0xFFFF:
            raise CodeError(self, "Velocity %f out of range" % float(n))
        self.set_lower_24_swapped(int(n)*256)
    def is_instant(self):
        return (True, -1)

class PositionAdjustInsn(AxisInsn):
    """PositionAdjust instructions.
    axis POSITION ADJUST n
    """
    def __init__(self, line, tab, axis, n):
        super(PositionAdjustInsn, self).__init__(line, tab, axis)
        self.set_opcode_6(0x10)
        self.set_command_data(0)
        self.set_lower_16(n)
        if n < -0x8000 or n > 0x7FFF:
            raise CodeError(self, "Position adjust %d out of range" % n)

class SpeedControlInsn(AxisInsn):
    """SpeedControl instructions.
    axis SPEED CONTROL n
    """
    def __init__(self, line, tab, axis, n):
        super(SpeedControlInsn, self).__init__(line, tab, axis)
        self.set_opcode_6(0x0D)
        if n < -0x800000 or n > 0x7FFFFF:
            raise CodeError(self, "Speed control %f out of range" % float(n))
        self.set_lower_24_swapped_sign_mag(int(n)*256)
    def is_fast(self):
        return False
    def is_pos_valid(self):
        return False

class OutInsn(AxisInsn):
    """Out instructions.
    axis OUTn state
    n is 1,2,3
    state is OutInsn.{OFF|ON|BR|RS|ERR}
    """
    OFF = 0
    ON = 1
    BR = 2
    RS = 3
    ERR = 4
    def __init__(self, line, tab, axis, n, state):
        super(OutInsn, self).__init__(line, tab, axis)
        self.set_opcode_6(0x06)
        self.set_command_data((n&3)<<4 | state&0x0F)
        self.set_lower_16(0)
        if n not in [1,2,3]:
            raise CodeError(self, "Output number %d out of range [1,2,3]" % n)
        if state not in [0,1,2,3,4]:
            raise CodeError(self, "State %d out of range [OFF,ON,BR,RS,ERR]" % state)

class Out1Insn(OutInsn):
    def __init__(self, line, tab, axis, state):
        super(Out1Insn, self).__init__(line, tab, axis, 1, state)
class Out2Insn(OutInsn):
    def __init__(self, line, tab, axis, state):
        super(Out2Insn, self).__init__(line, tab, axis, 2, state)
class Out3Insn(OutInsn):
    def __init__(self, line, tab, axis, state):
        super(Out3Insn, self).__init__(line, tab, axis, 3, state)

class ZeroOffsetInsn(AxisInsn):
    """ZeroOffset instructions.
    axis ZERO OFFSET n
    """
    def __init__(self, line, tab, axis, n):
        super(ZeroOffsetInsn, self).__init__(line, tab, axis)
        self.set_opcode_6(0x13)
        self.set_lower_24(n)
        if n < 0 or n > 0x7FFFFF:
            raise CodeError(self, "Offset %d out of range" % n)
    def is_instant(self):
        return (True, -1)

class WaitInsn(Insn):
    """Wait instructions.
    WAIT secs SECONDS
    secs is float in range [0.000..65.535]
    """
    def __init__(self, line, tab, secs):
        super(WaitInsn, self).__init__(line, tab)
        self.set_opcode_6(0x08)
        self.set_upper_2(0)
        self.set_command_data(0)
        if secs < 0. or secs > 65.535:
            raise CodeError(self, "Wait time %f out of range [0..65.535]" % secs)
        secs = int(secs * 1000)
        self.set_lower_16(secs)
    def is_fast(self):
        return False


class CodeBlock(object):
    """Maintain list of Insn (and Label definition points).  The code block ends at the last
    unconditional branch Insn, so that any following code is unreachable unless it has a heading
    label.  In general the first item in a CodeBlock will be a label; any Insn before the first
    Label will be unreachable so it can be removed
    """
    def __init__(self):
        self.block = []     # Insn list
        self.labels = []    # Label list
        self.org = None     # When not None, is location of first 
    def append(self, am):
        """Append Insn or Label to list"""
        if isinstance(am, Label):
            am.set_block_insn_index(len(self.block))
            self.labels.append(am)
        else:
            self.block.append(am)
    def is_located(self):
        return self.org is not None
    def locate(self, org):
        """Assign addresses to all labels and insns in this block, and set the overall block org
        """
        self.org = org
        for lab in self.labels:
            lab.set_addr(org + lab.get_block_insn_index())
        for a, insn in enumerate(self.block):
            insn.set_addr(org + a)
        if self.org < 0x10000 and self.get_next_org() >= 0x10000:
            raise CodeError(self.block[0x10000 - self.org], \
                "Program size exceeds available memory (64k instructions)")

    def get_next_org(self):
        return self.org + len(self.block)
    def get_insn_list(self):
        return self.block

class Namespace(AddressMark):
    def __init__(self, line, tab, filename):
        """Namespaces represent a set of labels (and possibly nested namespaces) qualified
        by a 'name'.  Note that the name used depends on the context of the importer, and thus may
        vary depending on context, so only the actual file name is stored here.
        
        Since this derives from AddressMark, each Namespace has a reference location
        which is the 'import' statement, or the first line of the top-level file for the
        anonymous top-level namespace.
        
        Insn objects are also maintained by the Namespace object, arranged in a list of CodeBlock.
        """
        super(Namespace, self).__init__(line, tab)
        self.filename = filename
        self.labels = {}        # Symbol table (by name, unqualified)
        self.namespaces = {}    # Nested namespaces (by 'as' name)
        self.blocks = [CodeBlock()]        # List of CodeBlock, with 1st one
        self.cblock = 0         # Current index in blocks
    def get_filename(self):
        return self.filename
    def namespace_filename(self, nsname):
        return self.namespaces[nsname].filename
    def add_label(self, name, label):
        """Add label with (unqualified) name 'name' and Label object 'label'"""
        if name in self.labels:
            raise CodeError(label, "Duplicate label '%s'" % (name, ),
                                    self.labels[name], "first defined here")
        self.labels[name] = label
        label.set_block_index(self.cblock)
        self.blocks[self.cblock].append(label)
    def add_namespace(self, nsname, ns):
        """Add subnamespace"""
        if nsname in self.namespaces:
            raise CodeError(ns, "Duplicate namespace '%s'" % (nsname, ),
                                    self.namespaces[nsname], "first defined here")
        self.namespaces[nsname] = ns
    def has_namespace(self, nsname):
        return nsname in self.namespaces
    def add_insn(self, insn):
        """Add Insn object to code block list.
        If insn is unconditional branch, terminate current block.
        """
        self.blocks[self.cblock].append(insn)
        if insn.is_end_of_block():
            self.new_block()
    def new_block(self):
        """Append a new block to list"""
        self.cblock = len(self.blocks)
        self.blocks.append(CodeBlock())
    def get_block(self, index):
        return self.blocks[index]
    def get_label(self, qlabelname, for_insn):
        """Find Label object with given (possibly) qualified label name.
        E.g. foo.bar.xxx will find subnamespace 'foo', then sub-subnamespace 'bar' within that,
        then the actual label object named 'xxx' in that.
        'for_insn' is the instruction for which we are doing the lookup - helps generate useful
        error messages.
        
        Note that extra spaces are allowed around the dots e.g. 'foo . bar'.
        
        Returns tuple (label, namespace) if found, else raises CodeError
        """
        if '.' in qlabelname:
            ns, rest = qlabelname.split('.', 1)
            return self.get_namespace(ns.strip(), for_insn).get_label(rest.strip(), for_insn)
        # not qualified, look in self
        if qlabelname in self.labels:
            return (self.labels[qlabelname], self)
        raise CodeError(for_insn, "Could not find label '%s'" % qlabelname)
    def get_namespace(self, nsname, for_insn):
        """Similar to get_label(), except look for (unqualified) namespace"""
        if nsname in self.namespaces:
            return self.namespaces[nsname]
        raise CodeError(for_insn, "Could not find namespace '%s'" % nsname)

class AxisMask:
    """Dummy class for denoting axis mask in instruction parse template"""
    pass

class Code:
    """Represents mapping between source text and object code.
    Retains reference to original GtkSource.TextBuffer(s) so that it can
    access breakpoints etc.
    
    Assembly starts with a specified top-level file.  Other files may be imported.
    Every source file will be brought in to a TextBuffer, and positions are
    maintained by TextMark objects, which refer back to buffers.
    
    Each buffer is contained in a Tab object.  If the file does not exist in
    a current tab, a new placeholder tab will be created for it.  Placeholders
    are not immediately visible on the display, however the user can explicitly
    open them (by file name) or implicitly by clicking on an assembler error
    message.
    
    The top-level file may be unnamed; its name is stored as "".  All imported
    files must be named.
    """
    # General line pattern... group1=instruction, group2=optional comment 
    gpat = re.compile(r"^\s*([^;#]*)(.*)$")
    # Word, string, integer, float and qualified and unqualified label patterns
    wpat = re.compile(r"^([A-Za-z_]\w*)(.*)$")  # g1 = word, g2=remainder
    spat = re.compile(r'^(?:('+'"""'+r"""|'''|"|')(.*?)\1)(.*)$""")   # g1=quote, g2=str, g3=remainder
    ipat = re.compile(r"^([+-]?[0-9]+)(.*)$")  # g1 = int, g2=remainder
    fpat = re.compile(r"^([+-]?[0-9]+(?:[.][0-9]*))(.*)$")  # g1 = float, g2=remainder
    qlpat = re.compile(r"^([A-Za-z_]\w*(?:\s*[.]\s*[A-Za-z_]\w*)*)(.*)$")
    uqlpat = re.compile(r"^([A-Za-z_]\w*)(.*)$")
    
    def __init__(self):
        self.obj = []       # List of Insn objects (indexed by address 0,1,...)
        self.root = None    # Root Namespace (anonymous, for top-level)
        self.aerrs = []     # Assembly errors
        self.err = None     # Step/run error message
        self.assembled = False
        self.mod_asm = True # True when source modified w.r.t. object code
        # Some stuff to make parsing more efficient:
        self.axisnames = dict(x=0, y=1, z=2, w=3, X=0, Y=1, Z=2, W=3)
        self._setup_opcode_table()
    def assemble(self, tab, options):
        """Assemble text program to convert to object code.
        Text should be disabled for editing when connected but
        not ready state of Devices, since we want consistency when the Devices
        are actually doing something.
        
        tab parameter is the top-level Tab object for this assembly.  From it
        can be obtained the TabManager, which allows access to any files which
        are imported by the top-level.
        
        options is a PersistentProject object (which contains all project settings).
        
        Tab objects contain TextBuffer objects, which contain the text to be assembled.
        """
        #print "Assembling"
        self.top_tab = tab
        self.options = options
        self.tab_mgr = tab.get_mgr()
        self.obj = []           # List of Insn
        self.nsblocks = []      # list of tuple (namespace, codeblock)
        topfilename = os.path.abspath(tab.get_filename_str())
        self.root = Namespace(0, tab, topfilename)    # New top-level namespace
        self.root.add_label("<boot>", Label(0, tab, 0)) # Dummy "boot" label at org 0.
                                                        # - marks initial code as 'reachable'
        self.setup_execdict()   # Set up local+global dict for pycode evaluation
        self.pycode_names = {}  # ...and internal pycode section names to (sourcefilename, lineno)
        self.pycode_nx = 0      # Reset name index
        self.clear_errors()
        self.importfiles = {topfilename : self.root}   # Dict mapping all absolute import files to namespace object
        self.org = None     # Catch errors using org before valid
        try:
            self.scan(tab, self.root)
            self.org = 0
            self.locate(self.root, 0)
            self.resolve()
        except CodeError:
            # Get here if fatal error raised somewhere
            pass
        # if all success...
        if not self.semantic_error_count():
            self.assembled = True
            self.mod_asm = False # now in agreement
            return True
        else:
            self.show_semantic_errors()
            return False
            
    def setup_execdict(self):
        self.uniq_label = 0
        self.execdict = {'_code' : self, 'emit' : self.emit, 'label' : self.label}
        for name, v in list(globals().items()):
            if name.endswith('Insn'):
                self.execdict[name] = v
    # "Callbacks" for use by python macros.
    def emit(self, insn_class, *args):
        """Emit an instruction into the current namespace code block.
        Parameters:
        -- insn_class: a class object representing the required instruction.  E.g. GotoInsn or
             MoveInsn.  Passing a non-Insn class may result in errors at a later point.
        -- args: parameters as required for that instruction type (excluding line and tab).
        """
        self.add_insn(insn_class(self.s_line, self.s_tab, *args), self.s_namespace)
    def label(self, labelstr=None):
        """Emit a label into the current namespace code block.
        Parameters:
        -- labelstr: label name, or default (None) to generate a unique name.
        Returns label name (useful if a unique name is generated; can be passed to
        an emitted GotoInsn etc.)
        """
        if labelstr is None:
            labelstr = ":%d" % self.uniq_label
            self.uniq_label += 1
        self.add_label(labelstr, Label(self.s_line, self.s_tab), self.s_namespace)        
        return labelstr
    
    def clear_errors(self):
        self.err = None
        self.first_liberr = True
        #for e in self.aerrs:
        #    tab = e[2]
        #    tab.buf().delete_mark(e[0])
        self.aerrs = []
    def get_errors(self):
        return self.aerrs
    def get_error_mark(self, err_index):
        return self.aerrs[err_index][0]
    def get_error_text(self, err_index):
        return self.aerrs[err_index][1]
    def get_error_tab(self, err_index):
        return self.aerrs[err_index][2]
    def get_error_line(self, err_index):
        iter = self.get_error_iter(err_index)
        if iter is None:
            return -1
        return iter.get_line()
    def get_error_iter(self, err_index):
        mark = self.get_error_mark(err_index)
        buf = mark.get_buffer()
        if buf is None:
            return None
        return buf.get_iter_at_mark(mark)
    def handle_error(self, code_error):
        """Add line to error list given CodeError.
        """
        elist = code_error.get_all()
        primary = True
        terminate = False
        for am, msg in elist:
            tab = am.get_tab()
            it = am.get_iter()
            if not primary:
                msg = '  ...'+msg
            self.aerrs.append((tab.buf().create_mark(None, it, True), msg, tab))
            primary = False
            if len(self.aerrs) == self.options.p.error_threshold:
                terminate = True
        if isinstance(code_error, FatalError):
            raise code_error
        if terminate:
            self.aerrs.append((tab.buf().create_mark(None, it, True), "Fatal: number of errors exceeds threshold", tab))
            raise code_error
            
    def semantic_error_count(self):
        return len(self.aerrs)
    def show_semantic_errors(self):
        for mark, text, tab in self.aerrs:
            buf = mark.get_buffer()
            if buf is None:
                print(tab.get_filename_condensed(), ": <deleted line> :", text)
            else:
                i = buf.get_iter_at_mark(mark)
                print(tab.get_filename_condensed(), ":", i.get_line() + 1, ":", text)

    def assembly_done(self):
        return self.assembled and self.err is None

    def address_from_line(self, line, tab):
        """Return object address given current line number in tab.
        TODO: This is a little expensive because line numbers can shift with editing,
        and we don't directly index object code by line number.
        """
        for addr, insn in enumerate(self.obj):
            if insn.get_tab() != tab:
                continue
            if insn.get_line() == line:
                return addr
        return None
    def get_obj_len(self):
        return len(self.obj)
    def binary_from_address(self, addr):
        """Return instruction code (list of 32-bit int) given address.
        Can return None if no (or incomplete) insn at given address.
        Also returns whether instruction is "fast", "instant", and next addr and list of insn objects.
        """
        bincode = []
        insnlist = []
        cont = True
        a = addr
        fast = False
        instant = False
        nxtaddr = 0
        while a < len(self.obj) and cont:
            insn = self.obj[a]
            insnlist.append(insn)
            bincode.append(insn.get_binary())
            cont = insn.is_chained()
            if not cont:
                fast = insn.is_fast()
                instant, nxtaddr = insn.is_instant()
            a += 1
        if cont or len(bincode) > 4 or len(bincode) == 0:
            if len(bincode) == 0:
                self.err = "No instruction at address "+str(addr)
            elif len(bincode) > 4:
                self.err = "Too many axes ("+str(len(bincode))+") in instruction at address "+str(addr)
            else:
                self.err = "Instruction not terminated at address "+str(addr)
            return (None, False, False, 0, None)
        return (bincode, fast, instant, nxtaddr, insnlist)
        
    def scan(self, tab, namespace):
        """Main token scanner and parser driver.  This is called for pass 1 which creates
        namespaces, labels therein, and Insn objects.
        After this returns, all listed instructions exist, but no object code locations
        have been assigned, and labels are not resolved.
        Leading and trailing spaces are not significant.
        Before scanning, ensure the file is up-to-date w.r.t. disc copy.  If not,
        but user has not modified buffer, then quietly reload it.  If it is out-of-date and the
        user has modified it, abort the assembly since the user needs to fix this up.
        """
        buf = tab.buf()
        if tab.is_file_modified_externally():
            if buf.get_modified():
                raise FatalError(1, tab, 
                    "File copy on disc has been modified since it was opened, with unsaved changes.")
            if not tab.reload_file():
                raise FatalError(1, tab, 
                    "File copy on disc has been modified since it was opened, and could not re-load.")
        t = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        # First, extract Python sections inside {{{ and }}}.  soffs/eoffs is char offsets of
        # non-matching sections (i.e. asm code).  sl/el is corresponding line number offsets.
        soffs = 0
        sl = 0
        if True:
          for m in re.finditer(r'^\{\{\{\s*(\w*)\s*$(.*?)^\s*\}\}\}\s*$', t, flags=re.M|re.S):
            eoffs = m.start()
            el = sl + t.count('\n', soffs, eoffs)
            self.scan_asm(tab, namespace, t, soffs, eoffs, sl)
            # Now handle the Python code
            self.run_pycode(tab, namespace, m.group(2), el-1, m.group(1))
            sl = el + t.count('\n', eoffs, m.end())
            soffs = m.end()
        # The tail part (if any) is also asm
        self.scan_asm(tab, namespace, t, soffs, len(t), sl)
    
    def scan_asm(self, tab, namespace, t, soffs, eoffs, sl):
        """Sub-scanner which just handles assembler code (Python 'macros' are extracted
        and handled separately in scan()).
        t is entire text,
        soffs is start char offset to start scanning
        eoffs is end char offset
        sl is line number (starting at zero) of the char at soffs.  This is incremented for
          each newline encountered.
        
        We use the Python token scanner, since it suits our purposes and is efficient.
        
        Initial converstion to cStringIO using slice is somewhat inefficient since it
        copies the string, but is the best we can do without writing C code.
        
        In this method, ss is method to call to handle next token (basically, is state-
        machine state).  Each scanner-state method returns a new state for ss, or
        raises a ScanError exception.  The latter is caught here, and ss will be set
        to eat tokens until the next newline.
        
        To avoid passing a lot of parameters, tab, namespace and the current line number
        are saved in self.  If need to recurse, then these are saved in locals, then
        restored on return.
        
        Each ss method sees the next token only, thus we must be able to parse with
        single token look-ahead.
        """
        sio = io.StringIO(t[soffs:eoffs])
        tokiter = tokenize.generate_tokens(sio.readline)
        ss = self.ss_linestart
        self.s_tab = tab
        self.s_namespace = namespace
        lcmt = False
        try:
            for tokid, tokstr, start, _, _ in tokiter:
                #print tokid, tokenize.tok_name[tokid], tokstr
                if tokid in (tokenize.INDENT, tokenize.DEDENT, tokenize.COMMENT, tokenize.NL, tokenize.ENDMARKER):
                    # indents and comments are not significant.  NL is python 'blank line' as
                    # opposed to NEWLINE at end of line with code.
                    continue
                if lcmt:
                    if tokid in (tokenize.NL, tokenize.NEWLINE):
                        lcmt = False
                    else:   
                        continue
                elif tokstr == ';':
                    lcmt = True
                    continue
                self.s_line = sl + start[0]-1   # 0-based line number of token start
                try:
                    ss = ss(tokid, tokstr)
                except ScanError as se:
                    se.set_line_tab(self.s_line, tab)
                    self.handle_error(se)
                    if tokid == tokenize.NEWLINE:
                        ss = self.ss_linestart
                    else:
                        ss = self.ss_eat_until_newline
                except CodeError as ce:
                    self.handle_error(ce)
                    if tokid == tokenize.NEWLINE:
                        ss = self.ss_linestart
                    else:
                        ss = self.ss_eat_until_newline
        except IndentationError:
            se = ScanError("Indentation error")
            se.set_line_tab(self.s_line, tab)
            self.handle_error(se)            
    def ss_linestart(self, tokid, tokstr):
        # Expect a name (label or opcode)
        if tokid == tokenize.NEWLINE:
            return self.ss_linestart
        if tokid == tokenize.NAME:
            self.s_opcode = tokstr
            return self.ss_colon_or_operand
        raise ScanError("Expected label or opcode at start of line, got '%s' (%d)" % (tokstr, tokid))
        
    def ss_colon_or_operand(self, tokid, tokstr):
        self.s_axis = None
        if tokid == tokenize.OP and tokstr == ':':
            self.add_label(self.s_opcode, Label(self.s_line, self.s_tab), self.s_namespace)
            return self.ss_expect_newline
        # Opcodes are case-insensitive
        self.s_opcode = self.s_opcode.lower()
        if self.s_opcode in self.botable:
            self.s_template = self.botable[self.s_opcode]
            if tokid == tokenize.NEWLINE:
                # No operand, skip to final processing for this opcode
                self.s_accum = []
                return self.ss_accumulate_until_newline(tokid, tokstr)
            self.s_accum = [(tokid, tokstr)]
            return self.ss_accumulate_until_newline
        try:
            self.s_axis = {'x':0, 'y':1, 'z':2, 'w':3}[self.s_opcode]
        except:
            raise ScanError("Opcode '%s' not recognized" % self.s_opcode)
        if tokid == tokenize.OP and tokstr in ('+','-') or tokid == tokenize.NUMBER or tokstr =='{':
            self.s_template = self.aotable['move']
            self.s_accum = [(tokid, tokstr)]
            return self.ss_accumulate_until_newline
        if tokid != tokenize.NAME:
            raise ScanError("Expected number or opcode after axis specification '%s'" % self.s_opcode)
        self.s_opcode = tokstr.lower()
        if self.s_opcode in self.aotable:
            self.s_template = self.aotable[self.s_opcode]
            self.s_accum = []
            return self.ss_accumulate_until_newline
        raise ScanError("Opcode '%s' not recognized" % self.s_opcode)
        
    def ss_eat_until_newline(self, tokid, tokstr):
        if tokid == tokenize.NEWLINE:
            return self.ss_linestart
        return self.ss_eat_until_newline
        
    def ss_accumulate_until_newline(self, tokid, tokstr):
        if tokid == tokenize.NEWLINE:
            # s_accum is accumulated token list of (id,str) tuples starting with
            # the one just past the opcode (if any)
            if self.s_axis is None:
                args = []
            else:
                args = [self.s_axis]
            tidx = self.gen_insns(self.s_template, 0, self.s_accum, args)
            if tidx < len(self.s_accum):
                raise ScanError("Extraneous operands starting at '%s'" % self.s_accum[tidx][1])
            return self.ss_linestart
        self.s_accum.append((tokid, tokstr))
        return self.ss_accumulate_until_newline
        
    def ss_expect_newline(self, tokid, tokstr):
        if tokid == tokenize.NEWLINE:
            return self.ss_linestart
        raise ScanError("Expected newline, got %s" % tokstr)
        
    def gen_type(self, typ, tidx, toklist, args):
        if tidx < len(toklist):
            tid, tstr = toklist[tidx]
            tidx += 1
            if tstr == '{':
                s = []
                while tidx < len(toklist):
                    tid, tstr = toklist[tidx]
                    tidx += 1
                    if tstr == '}':
                        try:
                            po = eval(''.join(s), self.execdict, self.execdict)
                            if typ == float and isinstance(po, int):
                                po = float(po)
                            if isinstance(po, typ):
                                args.append(po)
                            else:
                                raise ScanError("Macro evaluation did not return expected type %s, got %s" % \
                                        (str(typ), str(type(po))))
                        except ScanError:
                            raise
                        except Exception as e:
                            self.handle_pycode_error(e, self.s_tab, self.s_line-1, None)
                            args.append(typ(0)) # A rubbish value, to keep going
                        return tidx
                    else:
                        s.append(tstr)
                raise ScanError("Macro evaluation not terminated")
            else:
                if tid == tokenize.NUMBER and typ != str:
                    args.append(typ(tstr))
                elif tid == tokenize.STRING and typ == str:
                    args.append(eval(tstr))
                else:
                    # Handle leading sign.  Python tokenizer does not parse '-100' or '+100' as
                    # a single numeric token, so handle sign as separate char token.
                    if tstr in ['+','-'] and typ in [int,float] and tidx < len(toklist):
                        tidx = self.gen_type(typ, tidx, toklist, args)
                        if tstr == '-':
                            args[-1] = -args[-1]
                        return tidx
                    raise ScanError("Expected %s value, got '%s'" % (str(typ), tstr))
        return tidx
        
    def gen_insns(self, template, tidx, toklist, args):
        """Emit instruction(s) for current opcode.
        template is token matching template, toklist is list of (id,str) tuples for
        tokens beyond the opcode in the instruction (up to but not including the newline).
        tidx is the next token index (in toklist) to look at.
        args is list containing accumulated Insn ctor args so far.
        Returns index of next token to look at.
        """
        for obj in template:
            tid, tstr = toklist[tidx] if tidx < len(toklist) else (tokenize.NEWLINE, '\n')
            if obj in (int, float, str):
                tidx = self.gen_type(obj, tidx, toklist, args)
            elif type(obj) == str:
                if tstr.lower() != obj:
                    raise ScanError("Expected keyword '%s', got '%s'" % (obj, tstr))
                tidx += 1
            elif type(obj) == frozenset:
                if tstr.lower() not in obj:
                    raise ScanError("Expected one of %s, got '%s'" % (str(list(obj)), tstr))
                tidx += 1
            elif obj == Label:
                if tid != tokenize.NAME:
                    raise ScanError("Expected qualified label, got '%s'" % tstr)
                tidx = self.gen_label(tidx, toklist, args)
            elif obj == AxisMask:
                if tstr != '{' and tstr.lower() not in self.axisnames:
                    raise ScanError("Expected axis mask, got '%s'" % tstr)
                tidx = self.gen_axismask(tidx, toklist, args)
            elif type(obj) == tuple:
                tidx = self.gen_insns(obj, tidx, toklist, args)
            elif type(obj) == list:
                try:
                    tidx = self.gen_insns(obj[1:], tidx, toklist, args)
                except ScanError:
                    # Ok, didn't match so use default
                    if obj[0] is not None:
                        args.extend(obj[0])
            elif type(obj) == dict:
                if tstr in obj:
                    val = obj[tstr]
                    tidx += 1
                elif None in obj:
                    val = obj[None]
                else:
                    raise ScanError("Expected one of %s, got '%s'" % (str(list(obj.keys())), tstr))
                if type(val) == tuple:
                    args.append(val[0])
                    tidx = self.gen_insns(val[1], tidx, toklist, args)
                else:
                    args.append(val)
            elif isinstance(obj, type(Insn)):
                #print "Emit", self.s_opcode, obj, args
                self.add_insn(obj(self.s_line, self.s_tab, *args), self.s_namespace)
                args = []
            elif callable(obj):
                #print "Call", self.s_opcode, obj, args
                line = self.s_line
                tab = self.s_tab
                ns = self.s_namespace
                accum = self.s_accum
                obj(line, tab, ns, *args)
                self.s_accum = accum
                self.s_namespace = ns
                self.s_tab = tab
                self.s_line = line
        return tidx 
    
    def gen_label(self, tidx, toklist, args):
        """Label ref. is name ['.' name]..."""
        q = ""
        while tidx < len(toklist) and toklist[tidx][0] == tokenize.NAME:
            q += toklist[tidx][1]
            tidx += 1
            if tidx < len(toklist) and toklist[tidx][1] == '.':
                q += '.'
                tidx += 1
            else:
                break
        args.append(q)
        return tidx
        
    def gen_axismask(self, tidx, toklist, args):
        """Axis mask is {x|y|z|w} [',' {x|y|z|w}]...
        Also accept a macro {} which evaluates to an integer mask 0..15.
        """
        mask = 0
        if tidx < len(toklist):
            tid, tstr = toklist[tidx]
            if tstr == '{':
                tidx = self.gen_type(int, tidx, toklist, args)
                return tidx
        while tidx < len(toklist) and toklist[tidx][1].lower() in self.axisnames:
            mask += 1 << self.axisnames[toklist[tidx][1].lower()]
            tidx += 1
            if tidx < len(toklist) and toklist[tidx][1] == ',':
                tidx += 1
            else:
                break
        args.append(mask)
        return tidx
        
    def _setup_opcode_table(self):
        """Opcode table keyed by lowercase opcode.
        Axis names are special case, and switch to aotable; otherwise botable is used.
        Value is a template for matching the token stream following the opcode, which is tuple of:
        -- Python string: match literal tokenize.NAME (lowercase).  May also be a frozenset thereof,
           if a number of keyword aliases are defined.
        -- Python type int, float or str: tokenize.NUMBER (or .STRING) converted to this type
        -- Dict mapping string to int/tuple: match one of the specified strings (else error) and return int.
            May also map to tuple (int, template) where, if the key is recognized, parsing continues
            to recursively match against the given template.  First item in tuple is the int value
            to return if that match is used.  Template is used for further matching.
            An item with None key may exist in dict, in which case it is used to specify 
            a template which is followed if nothing else matched in the dict.  If the 'None' entry
            is used, then the option is deemed to have successfully matched nonetheless.
        -- Label type: return string (possibly qualified) which is a label reference
        -- AxisMask type: return axis mask from parsing comma delimited axis names
        -- List of the above types: signifies optional set of parameters, except that default values
           are specified by first item in list.  If the first template item in the list (i.e. element [1])
           does not match, the entire list is assumed not to exist and (if not None)
           default values (element [0], a list with values for each value item in this template
           section) will be returned in its place.  Parsing continues from same point.
        -- Tuple: sequence of above items
        -- Class object derived from Insn: when encountered, emit instruction from this class,
           using the accumulated args as per add_insn(Insn(line, tab, *args), namespace).
           If s_axis is not None, it is prepended to the arg list.
        -- Bound method: call with args (line, tab, namespace, *args).  Current ns, line and tab
           are saved and restored around the call to allow for possible recursion.  This is used
           for pseudo-ops like import.
        Note that after invoking Insn ctor or calling bound method, the arg list is reset to empty,
        including s_axis being reset to None.
        """
        swstates = {'off':0, 'on':1, 'br':2, 'rs':3, 'err':4}
        # Circular ref in following indicates possible repetition
        move_template = ([[0], {'+':1, '-':-1}], int, [[0], None])
        move_template[2][1] = {',':(1, (MoveInsn, self.axisnames, move_template)), None:(0, (MoveInsn,))}
        
        home_template = (self.axisnames, [[0], None])
        home_template[1][1] = {',':(1, (HomeInsn, home_template)), None:(0, (HomeInsn,))}
        
        #jog_template = (self.axisnames, [[0], None])
        #jog_template[1][1] = {',':(1, (JogInsn, jog_template)), None:(0, (JogInsn,))}
        
        config_template = (':', float, 'amps', ',', 'idle', 'at', int, '%', 'after', float, 'seconds', ConfigureInsn)
        posadj_template = (frozenset(['adjust', 'adj']), '+', '/', '-', int, PositionAdjustInsn)
        self.aotable = {
            # These ones follow axis specification...
            'move' : move_template,
            'zero' : ('offset', int, ZeroOffsetInsn),
            'offset' : (int, ZeroOffsetInsn),
            'velocity' : (float, VelocityInsn),
            'vel' : (float, VelocityInsn),
            'acceleration' : (float, AccelerationInsn),
            'accel' : (float, AccelerationInsn),
            'speed' : ('control', float, SpeedControlInsn),
            'config' : config_template,
            'configure' : config_template,
            'limit' : ('cw', int, ClockwiseLimitInsn),
            'compare' : ('value', int, CompareInsn),
            'position' : posadj_template,
            'pos' : posadj_template,
            'out' : (int, swstates, OutInsn),
            'out1' : (swstates, Out1Insn),
            'out2' : (swstates, Out2Insn),
            'out3' : (swstates, Out3Insn),
            }
        
        self.botable = {
            # Basic opcodes...
            'goto' : (Label, [[0], ',', 'loop', int, 'times'], GotoInsn),
            'call' : (Label, CallInsn),
            'return' : (ReturnInsn,),
            'ret' : (ReturnInsn,),
            'if' : (self.axisnames, {'in1':0, 'in2':1, 'in3':2, 'rdy':3, 'err':4, 'velocity':5, 'vel':5, \
                                        'position':6, 'pos':6, 'vin':7}, 
                                    'is', {'off':0, 'on':1, '<':2, '=':3, '>':4}, 
                                    [None, 'compare'],
                                    'goto', Label, ConditionalInsn),
            'wait' : (float, 'seconds', WaitInsn),
            'moving' : ('average', AxisMask, int, 'samples', MovingAverageInsn),
            'analog' : ('inputs', 'to', AxisMask, AnalogInputInsn),
            'vector' : (frozenset(('axis', 'axes')), frozenset(('are', 'is')), AxisMask, VectorAxesInsn),
            'respos' : (AxisMask, ResPosInsn),
            'home' : home_template,
            #'jog' : jog_template,
            'jog' : (AxisMask, JogInsn),
            'import' : (str, [[None], 'as', Label], self.do_import),
            }
        
    def substitute_path(self, line, tab, d):
        """Perform {...}[/] substitutions at head of d.
        """
        if os.path.isabs(d):
            return d
        else:
            if d.startswith("{project}"):
                base = self.options.get_project_folder()
                d = d[9:]
            elif d.startswith("{userlib}"):
                base = self.options.get_usrlib_folder()
                d = d[9:]
            elif d.startswith("{stdlib}"):
                base = self.options.get_stdlib_folder()
                d = d[8:]
            elif d.startswith("{"):
                #FIXME: should really detect this error in settings tab
                raise LineError(line, tab, "Invalid substitution '{...}' in %s" % d)
            else:
                return d
            if os.path.isabs(d):
                d = d[len(os.sep):]
            if not base:
                return None
            return os.path.join(base, d)
        
    def do_import(self, line, tab, ns, rawfilename, nsname):
        """Handle importation of library file filename as namespace 'nsname' (which may be None).
        
        If filename is absolute, then exactly that file is imported.  Otherwise, we search
        the defined list of library folders in order, looking for the first one which
        contains the named file.
        """
        filename = self.substitute_path(line, tab, rawfilename)
        if filename is None:
            raise LineError(line, tab, "Could not substitute '%s'" % rawfilename)
        if os.path.isabs(filename):
            filename = os.path.abspath(filename)
        else:
            tryfn = None
            for d in self.options.libsearch:
                d = self.substitute_path(line, tab, d)
                if d is None:
                    continue
                fn = os.path.join(d, filename)
                if os.access(fn, os.F_OK):
                    # File exists
                    tryfn = fn
                    break
            if not tryfn:
                e = LineError(line, tab, "Could not find %s in library search path" % filename)
                if self.first_liberr:
                    self.first_liberr = False
                    e.append(None, "which is (expanded out):")
                    for d in self.options.libsearch:
                        d = os.path.abspath(self.substitute_path(line, tab, d))
                        e.append(None, "   %s" % d)
                raise e
            filename = os.path.abspath(tryfn)
        # filename is now absolute file name to import.  First check to see whether we have ever imported
        # this file in this assembly.  If so, don't re-scan since this avoids possible infinite recursion
        # and makes importation an idempotent operation.
        # Next, check that there is no conflict in the chosen namespace name.  There is a conflict if
        # a different file is imported already with the chosen namespace name.  If both filename and
        # namespace are the same, ignore this import since it should be idempotent.
        # If the same file is being imported but with a different namespace, that's OK and we just create
        # a namespace alias without re-scanning.
        doscan = filename not in self.importfiles
        subtab = self.tab_mgr.get_tab(filename, open=True)
        if subtab is not None:
            if nsname is not None:
                if ns.has_namespace(nsname):
                    # Already in use: OK (do nothing) if same file, else error
                    if ns.namespace_filename(nsname) == filename:
                        return
                    e = LineError(line, tab, "Import '%s' namespace %s clash" % (filename, nsname))
                    e.append(ns.get_namespace(nsname, None), "used here")
                    raise e
                if doscan:    
                    subnamespace = Namespace(line, tab, filename)
                else:
                    subnamespace = self.importfiles[filename]   # Alias to existing model
                ns.add_namespace(nsname, subnamespace)
            else:
                # Merge into current namespace (no 'as name')
                subnamespace = ns
            if doscan:
                self.importfiles[filename] = subnamespace
                self.scan(subtab, subnamespace)
        else:
            raise LineError(line, tab, "Could not import '%s'" % filename)

    def locate(self, namespace, bi):
        """Second pass to locate object code and resolve label addresses.
        
        In this phase, self.org contains the next program location to assign.
        
        We are first called for the first block in the top-level namespace.  For each block,
        all unresolved label references out of that block cause all the blocks containing said
        labels the be located recursively.
        """
        block = namespace.get_block(bi)
        if block.is_located():
            return
        try:
            block.locate(self.org)
        except CodeError as e:
            # Can get error if crosses 64k boundary
            self.handle_error(e)
        self.org = block.get_next_org()
            
        self.obj.extend(block.get_insn_list())    # Copy into main list
        self.nsblocks.append((namespace, block))# Also remember block order
        ulist = []
        for insn in block.get_insn_list():
            if insn.is_unresolved_branch():     # Should alway be true at this stage
                qlab = insn.get_branch()        # Will be qualified label string
                try:
                    label, ns = namespace.get_label(qlab, insn)
                    if not label.is_resolved():
                        ulist.append((label, ns))
                except CodeError as e:
                    #self.handle_error(e)
                    pass    # Ignore for now, generate unresolved errors in resolve() pass.
        # Now recurse to bring in unresolved blocks
        for label, ns in ulist:
            self.locate(ns, label.get_block_index())
                        
    def resolve(self):
        """Label addresses are
        resolved and inserted into branching instructions.
        If this goes well, then assembly is complete.
        """
        for ns, block in self.nsblocks:
            for insn in block.get_insn_list():
                if insn.is_unresolved_branch():
                    qlab = insn.get_branch()
                    try:
                        label, labns = ns.get_label(qlab, insn)
                        insn.set_branch(label)
                    except CodeError as e:
                        self.handle_error(e)
                        
    def add_label(self, labelname, label, namespace):
        """Called when encountered new label line e.g. 'foo:'.
        """
        namespace.add_label(labelname, label)
    def add_insn(self, insn, namespace):
        namespace.add_insn(insn)
        
    def get_list_str(self):
        """Return asm list as one big string
        """
        lf = io.StringIO()
        print("""
Assembler listing
=================

org    binary      source line
----   ---------   -------------------------
""", file=lf)
        filename = ""
        for a, insn in enumerate(self.obj):
            f = insn.get_tab().filename
            if f != filename:
                print("    in: %s" % (f,), file=lf)
                filename = f
            b = insn.get_binary()
            print("%04X   %04X %04X%c  %s" % (a, b>>16, b & 0xFFFF, \
                    '+' if insn.get_chain() else ' ', '\n' if insn.get_chain() else insn.get_line_text()), end=' ', file=lf)
        return lf.getvalue()
        
    def make_listing(self, tab):
        """Write an assembler listing to specified tab.
        """
        buf = tab.buf()
        #buf.delete(buf.get_start_iter(), buf.get_end_iter())  
        buf.set_text(self.get_list_str())
        
    def handle_pycode_error(self, e, tab, openline, modname):
        typ, val, tb = sys.exc_info()
        if isinstance(e, SyntaxError):
            # Don't get traceback, so adjust line appropriately
            openline += e.lineno
            self.handle_error(LineError(openline, tab, "Python syntax error"))
            return
        self.handle_error(LineError(openline+1, tab, str(e)))
        elist = []
        for filename, oline, func, text in reversed(traceback.extract_tb(tb)[1:]):
            line = oline
            if filename in self.pycode_names:
                baseline, filetab, filemod = self.pycode_names[filename]
                filemod = "{{{"+filemod+"}}}" if filemod else "<macro code>"
                filename = None 
            elif filename == "<string>":
                # Get this when expanding {...} macros
                baseline, filetab, filemod = openline, tab, "<macro evaluation>"
                filename = None
            else:
                baseline, filetab, filemod = openline, tab, modname
                line = 1
            elist.append((baseline+line, func, filetab, filemod, filename, oline, text))
        if len(elist):
            for i, xx in enumerate(elist):
                line, func, filetab, filemod, extfile, origline, text = xx
                if func == "<module>" and filemod:
                    func = filemod
                if i == 0:
                    pfx = "  ...in... "
                else:
                    pfx = "  ...called from... "
                pfx += func
                if extfile:
                    pfx += " [%s : %d] %s" % (extfile, origline, text)
                self.handle_error(LineError(line, filetab, pfx))
        
    def run_pycode(self, tab, namespace, pycode, openline, modname):
        """Compile and execute 'pycode' (Python source).
        modname is optional identifier (string) which identifies this code section.
        
        self.execdict is used for local and global context, thus functions and classes
        may be defined in one pycode section, and used in another.
        
        Since multiple sections may be defined in a single source file, we use an internally
        generated "file name" (2nd compile() arg) which will be unique over the entire
        assembly run, and the self.pycode_names dict can be used to map back to the
        original source file and starting line number.
        """
        self.s_line = openline
        self.s_tab = tab
        self.s_namespace = namespace
        pname = "<internal>%d" % self.pycode_nx
        self.pycode_nx += 1
        self.pycode_names[pname] = (openline, tab, modname)
        try:
            exe = compile(pycode, pname, 'exec')
            eval(exe, self.execdict, self.execdict)
        except Exception as e:
            self.handle_pycode_error(e, tab, openline, modname)
                        
    def get_block(self, addr, n, add_ff=True):
        """Return string of object bytes suitable for programming to flash.
        addr is insn address, n is number of insns.  
        if add_ff:
            Extract 0..4n bytes, limited by actual code size
            Append 0xFFFF if less than 4n bytes available.
            Returns string of 4n bytes, or 4x+2 if only x (<n) insns available.
        else:
            If addr+n > len(obj) then the remainder is padded with GOTO 0 insns.
            Returns string of 4n bytes.
        
        """
        if add_ff:
            term = struct.pack('H', 0xFFFF)
            if addr >= len(self.obj):
                return term
            elif addr+n > len(self.obj):
                t = [self.obj[x].get_binary() for x in range(addr, len(self.obj))]
                t = [(d&0xFFFF)<<16|(d&0xFFFF0000)>>16 for d in t]
                t = struct.pack("<"+"I"*(len(self.obj)-addr), *t)
                return t+term
            t = [self.obj[x].get_binary() for x in range(addr, addr+n)]
            t = [(d&0xFFFF)<<16|(d&0xFFFF0000)>>16 for d in t]
            return struct.pack("<"+"I"*n, *t)
        else:
            fill = 0x03000000
            if addr >= len(self.obj):
                t = [fill]*n
            elif addr+n > len(self.obj):
                t = [self.obj[x].get_binary() for x in range(addr, len(self.obj))] + [fill]*(n-len(self.obj))
            else:
                t = [self.obj[x].get_binary() for x in range(addr, addr+n)]
            # Reorder and change to string
            t = [(d&0xFFFF)<<16|(d&0xFFFF0000)>>16 for d in t]
            return struct.pack("<"+"I"*n, *t)

            
    
