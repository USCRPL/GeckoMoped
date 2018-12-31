from geckomoped import gm_api

# Example program showing vectored motion

drv = gm_api.GeckoDriver(None, None)

# Connect to the driver at the given serial port.
# You can call drv.get_serialports() to get a list of detected ports.
drv.connect('/dev/ttyUSB0')

example_program = """
; set up motor currents
x configure: 4 amps, idle at 50% after 1 seconds
y configure: 6 amps, idle at 50% after 5 seconds

; accelerate as slowly as possible
; to be honest, I have NO IDEA what the units of acceleration are
x acceleration 1
y acceleration 1

; enabled vectored motion
vector axes are x, y

; set the diagonal/pythagorean velocity for the vector move.
; yes it says X velocity, but that is a lie.
; also note: some careful testing has revealed that the units of 
; this velocity are roughly (1/3.8) steps per second
x velocity 100

; move!
x+1000, y+2000
"""

# Compile the program and load it to the controllers.  
# Will throw if there are any errors.
drv.load_program(example_program)

# Start the program executing
drv.run()

# Block until the current program finishes
drv.wait_for_program()

# Shut down comms thread
drv.shutdown()