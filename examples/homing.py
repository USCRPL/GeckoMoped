from geckomoped import gm_api

# Demo program showing homing of the X axis.
# You would want to connect a limit switch to the D2 input before running this!

drv = gm_api.GeckoDriver(None, None)

drv.connect('/dev/ttyUSB0')

home_x = """
x configure: 4 amps, idle at 50% after 1 seconds
y configure: 6 amps, idle at 50% after 5 seconds

x velocity 300
home x
"""

drv.load_program(home_x)

drv.run()

drv.wait_for_program()

drv.shutdown()