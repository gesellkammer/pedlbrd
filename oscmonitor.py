import sys
import time
try:
    import liblo
except ImportError:
    print "Liblo not installed!"
    print sys.path
    sys.exit(0)

class OSCMonitorServer(object):
    def __init__(self, app, coreaddr=None, port=None, exclude=None):
        self.app = app
        self._quitting = False
        self.exclude = exclude
        self.coreaddr = coreaddr if coreaddr is not None else ('localhost', 47120)
        try:
            if port is None:
                self.server = liblo.ServerThread()
            else:
                self.server = liblo.ServerThread(port)
            self.ok = ok = True
        except:
            self.server = None
            self.ok = ok = False
            return
        self.started = False
        self.server.add_method('/quit', None, self.quit_handler)
        self.server.add_method('/ping', None, self.ping_handler)
        self.server.add_method(None, None, self.default_handler)
        self.port = self.server.port
        self.server.send(self.coreaddr, '/registerdata')

    def signout(self):
        self.server.send(self.coreaddr, '/signout')
        
    def stop(self):
        if not self.started:
            return
        self.started = False
        self.server.send(self.coreaddr, '/signout')
        self.server.stop()
        self.server.free()

    def start(self):
        self.started = True
        self.server.start()

    def default_handler(self, path, args, types, src):
        if path not in self.exclude:
            argstr = ", ".join(map(str, args))
            msg = " ".join((path.ljust(16), argstr))
            self.app.post(msg)

    def quit_handler(self, path, args, types, src):
        if self._quitting:
            return
        self._quitting = True
        self.app.quit(external=True)

    def ping_handler(self, path, args, types, src):
        ping_id = args[0]
        self.server.send(src, '/reply', ping_id)

class TerminalApp(object):
    def __init__(self, coreaddr=('localhost', 47120), exclude=['/heartbeat']):
        self.monitor = OSCMonitorServer(self, coreaddr=coreaddr, exclude=exclude)
        self.monitor.start()
        self._running = False
    def post(self, msg):
        print msg
    def quit(self):
        self.monitor.stop()
    def mainloop(self):
        self._running = True
        while self._running:
            time.sleep(10)

def argv_getoption(argv, option, default=None, remove=False, astype=None):
    """
    Get the value of an --option in the command line

    progname --option value

    * If the --option is not present, return default
    * If the --option has not a value following it --> raises ValueError
    
    remove: remove both --option and value from argv
    astype: value = astype(value). Raise TypeError if this conversion fails
    """
    try:
        index = argv.index(option)
        try:
            value = argv[index+1]
            if value.startswith('-'):
                raise ValueError("option %s had no value!" % option)
            if remove:
                argv.pop(index+1)
                argv.pop(index)
            if astype:
                try:
                    value = astype(value)
                except ValueError:
                    raise TypeError("could not interpret value %s as type given" % str(value))
            return value
        except IndexError:
            raise ValueError('no value set for option %s' % option)
    except ValueError:  # not in argv
        return default

def usage():
    print """{progname} [options] [address]

options:

--exclude path1:path2:...
        Exclude messages with the given path
        --exclude /heartbeat

--frontend tk/terminal     default:tk

    """.format(progname=sys.argv[1])

def start(frontend, coreaddr, exclude):
    if frontend == 'tk':
        from pedlbrd.oscmonitortk import App
        app = App(monitor_constructor=OSCMonitorServer, coreaddr=coreaddr, exclude=exclude)
    else:
        app = TerminalApp(coreaddr=('127.0.0.1', 47120), exclude=exclude)
    try:
        app.mainloop()
    except KeyboardInterrupt:
        app.quit()

if __name__ == "__main__":
    print "starting..."
    exclude  = argv_getoption(sys.argv, '--exclude', default='', remove=True)
    frontend = argv_getoption(sys.argv, '--frontend', default='tk', remove=True)
    if '--help' in sys.argv:
        usage()
        sys.exit(0)
    if ":" in exclude:
        exclude = exclude.split(":")
    else:
        exclude = [exclude]
    if exclude:
        print "excluding: %s" % exclude
    start(frontend, coreaddr=('127.0.0.1', 47120), exclude=exclude)
    