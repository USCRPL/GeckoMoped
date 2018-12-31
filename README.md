## GeckoMoped

![GM215 Picture](https://www.geckodrive.com/media/catalog/product/cache/c687aa7517cf01e65c009f6943c2b1e9/g/m/gm215-1.jpg)

GeckoMoped is an enhanced version of GeckoDrive's GM215 [motor controller driver](https://www.geckodrive.com/support/geckomotion.html).  It adds a complete Python API, allowing you to easily drive GM215 controllers from your own apps!  It also includes an updated version of the GeckoMotion GUI that fixes many of the original's issues with connections.


#### Features

- Based off of latest GeckoMotion (1.0.31)
- Installable as a lightweight Python package, instead of an installer containing literally an entire Python interpreter
- Ported to Python 3
- API allowing execution of GeckoMotion code exactly as if it was typed into the GUI
- Cross-platform (instead of just being available for Windows)
    - API works on all platforms supported by Python
    - GUI runs anywhere pyGTK/PyGObject does
         - Unfortunately, this does not include Windows at present.
- Connection bugs in GUI fixed
- Accurate reporting of controllers' position and velocity


#### Example Program

```python
from geckomoped import gm_api

drv = gm_api.GeckoDriver(None, None)

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
; also note: testing has revealed that the units of 
; this velocity are roughly (1/3.8) steps per second
x velocity 100

; move!
x+1000, y+2000

"""

drv.load_program(example_program)

drv.run()

drv.wait_for_program()

drv.shutdown()

```

#### More about the Connection Errors

Back when we used the original GeckoMotion GUI here at Rocket Propulsion Lab, we were plagued with issues with motor controllers disconnecting and stopping if we so much as looked at them wrong.  Also, to even get the app to execute code, we had to go into one of the debugging menus and enable periodic sending of a QLong.  If you're reading this, chances are you've dealt with similar issues.  Luckily, GeckoMoped has fixes for both!

The random disconnections turn out to be because the developers of the GUI made some poor assumptions about threading that came back to bite them in the butt.  The communications protocol with the motor controllers is such that the host PC must send data quickly at times; if it delays more than a few tens of milliseconds than everything breaks.  You might think that the original developers would put such an important heartbeat function in its own thread, but instead they added it to the GUI as a GTK "idle" function.  By definition, idle functions are only called when the GUI event loop has no other work to do.  So, if the GUI had lots of work to do (because you were interacting with it) for more than 50ms or so, then the idle function would never get called and the motor controllers would lose connection!  

The QLong issue is due to a similar oversight.  A QLong is a query sent to the motor controllers that causes them to respond with their current position and velocity, and where in the program they are.  As you can imagine, the GUI needs this information to drive the debugger view and the position readouts.  Unfortunately, it seems like no one ever wrote code in the GUI to send QLongs, so it waits forever for data that never shows up, and appears to hang on the first line of the program.  I can only imagine that the developers left the debug QLong checkbox on while they were testing, or just expected users to figure this out.

For some reason, these issues have never gotten fixed.  We emailed GeckoDrive about them, and their response was that these motor controllers were not designed for continuous operation in "debug" mode, where they're connected to a computer.  Instead, you're supposed to download code once, then physically switch them into "run" mode and run them untethered.  That wasn't going to work for our application, where we're driving an industrial CNC from complex and changing calculations to construct a rocket fuselage.  So we decided to fix it ourselves.  First, we wrote a Python class, `GeckoDriver`, which acts as an API and runs the idle function in a background thread every 20ms.  It also periodically sends QLongs to update its state data.  That worked great, so we also ported the threading code back to the GUI instead of that idle-function based nonsense.

Today, we use this driver in our day-to-day operations, and have had an order of magnitude less issues with the system. GeckoDrive might claim that GM215s are "not generally recommended for end-user CNC applications", but that is entirely a software issue: by using this driver, we have no problems driving these capable and powerful controllers directly from a computer program.


#### License

As far as I can tell, GeckoDrive did not include any sort of license with GeckoMotion.  Accordingly, I am releasing this library as public domain.

That said, it is still 90% their code, and they deserve all the credit for writing it and getting it this far.  I just added some polish on top of it.

#### Dependencies

##### API Only:
- [pyserial](https://pypi.org/project/pyserial/)

##### GUI:
- [PyGObject](https://pypi.org/project/PyGObject/)
- GTK and GtkSourceView libraries installed (which are accessed through PyGObject)