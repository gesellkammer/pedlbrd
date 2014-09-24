#!/usr/bin/env python
import time
import sys
import os
import pedlbrd
import logging

logging.basicConfig()
logger = logging.getLogger("pedlbrd.py")

PORT_UNSET = -1

def with_gui(coreport):
    """create the core process and a gui on the local machine, on the same process"""
    logger.debug("with gui")
    # from pedlbrd import gui
    from pedlbrd import qtgui as gui

    p = get_core(coreport)
    p.start(async=True)
    try:
        gui.start(('localhost', p.config['osc_port'])) # TODO
    finally:
        p.stop()

def detached_gui(coreport):
    """create the core process and a gui on the local machine, on different processes"""
    logger.debug("detached_gui")
    import subprocess, time
    logger.debug("getting core")
    p = get_core(coreport)
    logger.debug("creating gui process")
    # from pedlbrd import gui
    from pedlbrd import qtgui as gui
    normal_guipath = os.path.splitext(gui.__file__)[0] + '.py'
    app_guipath = 'gui.py'
    for guipath in (normal_guipath, app_guipath):
        if os.path.exists(guipath):
            if sys.platform == 'darwin':
                #guiprocess = subprocess.Popen(args=[sys.executable, guipath])  
                subprocess.Popen(args=[sys.executable, guipath])
            break
    else:
        logger.error("could not find the gui!")
        return
    logger.debug("starting core")
    p.start(async=False)
    logger.debug("core exited!")

def detached_gui_reverse(coreport):
    """create the core process and a gui on the local machine, on different processes"""
    logger.debug("detached gui reverse")
    import subprocess, time
    from pedlbrd import qtgui as gui
    core_manager = subprocess.Popen(args=[sys.executable, 'pedlbrd.py', '--nogui'])
    # This will block until the gui quits
    logger.debug("starting gui")
    gui.start(('localhost', 47120))
    
    
def no_gui(coreport):
    """create the core process only"""
    logger.debug("no gui")
    p = get_core(coreport)
    if p is None:
        logger.error("could not create driver")
        return False
    logger.debug("starting headless core process. Press CTRL-C or send /quit to OSC port {coreport}".format(coreport=p._oscserver.port))
    p.start(async=False)
    return True

def oscmon():
    from pedlbrd import oscmonitor
    oscmonitor.start('tk', ('127.0.0.1', 47120), exclude=['/heartbeat'])

def only_gui():
    # TODO
    pass

# ------------------------
# HELPERS
# ------------------------
def get_core(coreport):
    """Create the core driver locally, on the given OSC port"""
    try:
        if coreport == PORT_UNSET:
            # use default
            core = pedlbrd.Pedlbrd(restore_session=False)
        else:
            core = pedlbrd.Pedlbrd(restore_session=False, osc_port=coreport)
    except pedlbrd.OSCPortUsed:
        core = None
    return core

def inside_virtualenv():
    return os.getenv("VIRTUAL_ENV") is not None

# ---------------------------
# Command line
# ---------------------------

def usage():
    print("""{progname} [options]

    --port portnumber   core: listen to the given port for OSC messages.
    --nogui             start the service headless
    --oscmon            only start the osc monitoring
    --help              this help message
    """.format(progname=os.path.split(sys.argv[0])[1]))

# //////////////////////////////////////////////////////////////////////
# MAIN

if __name__ == '__main__':
    from pedlbrd import util
    if util.argv_getflag(sys.argv, '--help'):
        usage()
        sys.exit(0)

    DETACHED = True
    GUI      = True

    if util.argv_getflag(sys.argv, '--nogui'):
        GUI = False
    
    OSCMON = util.argv_getflag(sys.argv, '--oscmon')
    if OSCMON:
        oscmon()
        sys.exit(0)
    
    port = util.argv_getoption(sys.argv, '--port', PORT_UNSET, astype=int)

    if not GUI:
        no_gui(port)
    else:
        if DETACHED:
            detached_gui_reverse(port)
        else:
            logger.debug("starting core and gui in single process")
            with_gui(port)
    sys.exit(0)