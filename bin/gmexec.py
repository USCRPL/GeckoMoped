#!/usr/bin/env python3

from geckomoped import gm_api
import argparse

# Utility program for running a GeckoMotion motor control program from the command line.

# arg parsing
# ------------------------------------------------------------------------------------------------------------------
parser = argparse.ArgumentParser()

parser.add_argument("-p", "--port", help="The serial port that the motion controllers are connected to.", type=str, metavar='port', required=True)
parser.add_argument("-l", "--logfile", help="Log binary communications to the given logfile.  Useful for debugging.", type=str, metavar='logfile', required=False)
parser.add_argument("-s", "--simulate", help="Use simulated dummy motor controllers (--port value ignored).  Useful for testing if your code compiles.", action="store_true", required=False)
parser.add_argument("script", help="GeckoMotion script to compile and execute.", type=str)

args = parser.parse_args()

port = args.port
log_file_path = args.logfile
script_path = args.script
simulate = args.simulate

# execute program
# ------------------------------------------------------------------------------------------------------------------

if simulate:
    print(">> [running in simulated mode]")

# init controllers
print(">> Connecting to controllers...")
drv = gm_api.GeckoDriver(log_file_path, None, simulate)

if not drv.connect(port):
    print("Error: failed to connect to motor controllers on port %s" % port)
    drv.shutdown()
    exit(1)

print(">> Connected!")

# read program file
print(">> Compiling script...")

script_text = None
try:
    script_file = open(script_path, "r")
    script_text = script_file.read()
    script_file.close()
except OSError as ex:
    print("Error: failed to read script file: %s" % str(ex))
    drv.shutdown()
    exit(1)

try:
    drv.load_program(script_text)
except gm_api.GMCompileException as ex:
    print("Error: failed to compile GeckoMotion script: %s" % str(ex))
    drv.shutdown()
    exit(1)

print(">> Compiled!")

print(">> Running program...")
try:
    drv.run()

    drv.wait_for_program()

    print(">> Program done.")

except gm_api.GMInvalidStateException as ex:
    print("Error executing GeckoMotion script: %s" % str(ex))
    drv.shutdown()
    exit(1)

except KeyboardInterrupt:
    print("Caught keyboard interrupt, stopping...")
    drv.stop()

drv.shutdown()