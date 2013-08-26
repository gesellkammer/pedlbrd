#!/usr/bin/env python2.7
import os
import sys
import liblo
from fnmatch import fnmatch
import time
import atexit 

class Completer(object):
    def __init__(self):
        self.commands = ['api', 'register', 'quit', 'help', 'broadcast', 'list', 'autoget', 'gui', 'info']
        self.update()
        
    def update(self, sync = True):
        global print_enabled
        condition = [False]
        def callback(path, args, types, src):
            api = args[1:]
            methods = [method.split('#')[0] for method in api]
            self.oscapi = methods
            self.matches = self.commands + self.oscapi
            self.matches.sort()
            condition[0] = True
        register_callback('/reply/api', callback, oneshot=True)
        print_enabled = False
        s.send(DEFAULT_ADDR, '/api/get')
        if sync:
            t0 = time.time()
            while not condition[0]:
                time.sleep(0.1)
                if time.time() - t0 > 2:
                    print "Enter 'api' to update the api when the device is present."
                    break
            print_enabled = True
        return condition
    def complete(self, text, state):
        out = []
        for word in self.matches:
            if word.startswith(text):
                out.append(word)
        try:
            return out[state]
        except IndexError:
            return None

# //////////////////////////////////////
#             H E L P E R S
# //////////////////////////////////////

def getflag(args, arg, remove=False):
    if arg in args:
        args.remove(arg)
        return True
    return False

def getoption(args, option, default=None, remove=False):
    try:
        index = args.index(option)
        try:
            value = args[index+1]
            if value.startswith('-'):
                raise ValueError("option %s had no value!" % option)
            if remove:
                args.pop(index+1)
                args.pop(index)
            return value
        except IndexError:
            raise IndexError('no value set for option %s' % option)
    except ValueError:  # not in args
        return default

def unpack(iterable, result=list):
    """Similar to python 3's *rest unpacking
    
    >>> x, y, rest = unpack('test')
    >>> x
    t
    >>> y
    e
    >>> rest
    ('s', 't')
    """
    def how_many_unpacked():
        import inspect, opcode
        f = inspect.currentframe().f_back.f_back
        if ord(f.f_code.co_code[f.f_lasti]) == opcode.opmap['UNPACK_SEQUENCE']:
            return ord(f.f_code.co_code[f.f_lasti+1])
        raise ValueError("Must be a generator on RHS of a multiple assignment!!")
    iterator = iter(iterable)
    has_items = True
    amount_to_unpack = how_many_unpacked() - 1
    item = None
    for num in xrange(amount_to_unpack):
        if has_items:        
            try:
                item = iterator.next()
            except StopIteration:
                item = None
                has_items = False
        yield item
    if has_items:
        yield result(iterator)
    else:
        yield None

# ////////////////////////////////////////
#     USAGE and HELP
# ////////////////////////////////////////

def usage():
    print """{progname} [options] [port]

    Listen and send OSC to/from port (if given)

    --noautoget
                fill in a reply id whenever a GET action is sent
                (for ex. to get smoothing for analog 1, you would type 
                    "/smoothing/get replyid 1" instead of "/smoothing/get 1")

    --broadcast 
                send messages to 255.255.255.255 instead of a speficic host

    --hostname hostname (default='localhost')
                the IP address of the host (or localhost).

    --include "/set/*:/get/*" 
               include paths mathing the patterns 
               (use ':'' between patterns)
    --exclude "/debug*"
                exclude paths mathing the patterns


    Example
    -------

    {progname} --exclude "/debug*:/info" 9999

      will listen to port 9999, excluding all messages matching /debug* and /info
      From this same port you can send messages with the syntax:

      pedl> /path foo 3.14

      or if you need to specify the address:

      pedl> hostname:port /path foo 3.14

    """.format(progname=os.path.split(sys.argv[0])[1])
    sys.exit()

def register_callback(path, callback, oneshot=False):
    if oneshot:
        kind = 'oneshot'
    else:
        kind = 'normal'
    registered_callbacks[path] = (kind, callback)

def main_callback(path, args, types, src):
    if path in registered_callbacks:
        kind, callback = registered_callbacks[path]
        callback(path, args, types, src)
        if kind == 'oneshot':
            del registered_callbacks[path]
    elif path == '/reply' and args[0] in registered_callbacks:
        print "foo"
        kind, callback = registered_callbacks[args[0]]
        callback(path, args[1:], types[1:], src)
        if kind == 'oneshot':
            del registered_callbacks[args[0]]
    else:
        if any(fnmatch(path, wildcard) for wildcard in exclude_wildcards):
            return
        match = any(fnmatch(path, wildcard) for wildcard in include_wildcards)
        if match and print_enabled:
            if len(args) < 8:
                arg_str = ", ".join(map(str, args)) if args else ""
                print "{host}:{port} {path} {types} {args}".format(
                    path=path.ljust(18), types=types.ljust(6), host=src.hostname, port=src.port,
                    args=arg_str)
            else:
                print "{host}:{port} {path}".format(host=src.hostname, port=src.port, path=path)
                for arg in args:
                    print "       ", arg

def echo_handler(path, args, types, src):
    host, port = src.hostname, src.port
    addr = (host, port)
    sources.add(addr)
    print "/echo ", path, args, types, addr
    
def heartbeat_handler(path, args, types, src):
    pass

def quit_handler(path, args, types, src):
    addr = src.hostname, src.port
    try:
        sources.remove(addr)
    except KeyError:
        pass
    print "\n>> Device at %s:%d just quit" % addr

def println_handler(path, args, types, src):
    for s in args:
        print s

def set_default_addr():
    global DEFAULT_ADDR
    if broadcast:
        DEFAULT_ADDR = ADDR_BROADCAST
    else:
        DEFAULT_ADDR = ADDR_LOCAL

def parse_addr(tok):
    if ":" in tok:
        host, port = tok.split(":")
        port = int(port)
    else:
        try:
            int(tok)
            host, port = 'localhost', int(tok)
        except ValueError:
            raise ValueError("could not parse address: %s" % tok)
    return host, port
            
def parse_args(toks):
    def parse_tok(tok):        
        arg = tok
        try:
            return int(tok)
        except ValueError:
            pass
        try: 
            return float(tok)
        except ValueError:
            return arg
    return map(parse_tok, toks)


def list_devices(show=False):
    s.send(('255.255.255.255', 47120), '/echo')
    time.sleep(0.5)
    if show:
        for source in sources:
            print " - ", source
    return sources

def show_api():
    if COMPLETER:
        COMPLETER.update()
    result = []
    def callback(path, args, types, src):
        result.extend(args)
    register_callback('/reply/api', callback, oneshot=True)
    global print_enabled
    print_enabled = False
    s.send(DEFAULT_ADDR, '/api/get')
    time.sleep(0.3)
    print_enabled = True
    if len(result) < 1:
        print "failed!"
        return
    msg = "     API     "
    print "-" * len(msg)
    print msg
    print "-" * len(msg)

    methods = result[1:]
    for method in methods:
        path, sig, doc = method.split('#')
        print "{path} {sig} {doc}".format(path=path.ljust(20), sig=sig.ljust(6), doc=doc)

def show_help():
    commands = (
        ('api', 'Show a detailed report on the OSC api'),
        ('broadcast', 'Toggle broadcasting. When ON, OSC messages will be sent to the whole network'),
        ('register', 'Register this process to receive all OSC messages from the Pedlbrd device'),
        ('quit', 'Exit this process')
    )
    cmd_max_length = max(len(cmd) for cmd, _ in commands)
    show_api()
    print "\n - - - - - - - - - - - - - -\n"
    for cmd, doc in commands:
        print "{cmd}  {doc}".format(cmd=cmd.ljust(cmd_max_length), doc=doc)

def openctrlpanel():
    if sys.platform == 'darwin':
        paths = ['extra/pd/pedlctrl.pd', 'pedlctrl.pd']
        for path in paths:
            if os.path.exists(path):
                os.system('open %s' % path)
                break

# ----- MAIN ------

if __name__ == '__main__':
    # set the directory where this file is running as the base dir
    os.chdir( os.path.split(__file__)[0] )
    HISTFILE = os.path.join(os.path.expanduser("~"), ".pedlbrd/pedltalk.hist")
    registered_callbacks = {}
    if getflag(sys.argv, '--help', remove=True):
        usage()
        
    include_wildcards = getoption(sys.argv, '--include', default="*", remove=True)
    if include_wildcards:
        include_wildcards = include_wildcards.split(":")

    exclude_wildcards = getoption(sys.argv, '--exclude', default="", remove=True)
    if exclude_wildcards:
        exclude_wildcards = exclude_wildcards.split(":")

    hostname  = getoption(sys.argv, '--hostname', default='localhost', remove=True)
    broadcast = getflag(sys.argv, '--broadcast', remove=True)
    autoget   = not getflag(sys.argv, '--noautoget', remove=True)

    ADDR_BROADCAST = ('255.255.255.255', 47120)
    ADDR_LOCAL = (hostname, 47120)
    DEFAULT_ADDR = ADDR_LOCAL

    set_default_addr()

    try:
        port = sys.argv[1]
    except IndexError:
        port = None
        print ">> No port given. Will use a random generated port"

    if port:
        s = liblo.ServerThread(port)
    else:
        s = liblo.ServerThread()
        port = s.port

    s.add_method('/heartbeat', None, heartbeat_handler)
    s.add_method('/echo', None, echo_handler)
    s.add_method('/quit', None, quit_handler)
    s.add_method(None, None, main_callback)
    s.add_method('/println', None, println_handler)


    msg = "PEDLTALK -- Listening to port: {port}".format(port=port)
    print
    print "-" * len(msg)
    print msg
    print "-" * len(msg)
    print
    print "include paths matching:", " ".join(include_wildcards)
    print "exclude paths matching:", " ".join(exclude_wildcards)
    print """

To send, type [{address}] {path} [arg1 arg2 ... argn]"
NB: address is optional"

Example
-------

pedl> /midicc/set A1 120
pedl> 196.168.0.100:47120 /add 2 3.1

Press <TAB> for a list of available commands
Enter 'quit' or press CTRL-D to exit
    """
    s.start()
    sources = set()

    readline_available = True
    try:
        import readline
    except ImportError:
        readline_available = False

    if readline_available:
        readline.parse_and_bind("tab: complete")
        atexit.register(readline.write_history_file, HISTFILE)
        if os.path.exists(HISTFILE):
            readline.read_history_file(HISTFILE)
        COMPLETER = Completer()
        readline.set_completer(COMPLETER.complete)
        readline.set_completer_delims(' \t\n`!@#$^&*()=+[{]}|;:\'",<>?')
    else:
        print "could not import readline. TAB support is disabled"
        COMPLETER = None

    print_enabled = True

    try:
        while True:
            print
            options = ["autoget=%s" % ('ON' if autoget else 'OFF')]
            optionstr = "[%s]" % (", ".join(options))
            prompt = "{optionstr} pedl> ".format(optionstr=optionstr)
            cmd = raw_input(prompt)
            cmd = cmd.strip()
            if cmd == "quit" or cmd=="q":
                s.send(DEFAULT_ADDR, '/signout')
                break
            elif cmd == "broadcast":
                broadcast = not broadcast
                set_default_addr()
                print "broadcasting is %s. Default address is: %s" % ('ON' if broadcast else 'OFF', DEFAULT_ADDR)
            elif cmd == "register":
                s.send(DEFAULT_ADDR, '/registerall')
            elif cmd == "help":
                show_help()
            elif cmd == 'api':
                show_api()
            elif cmd == 'autoget':
                autoget = not autoget
                print "autoget is %s" % ('ON' if autoget else 'OFF')
            elif cmd == 'list':
                list_devices(show=True)
            elif cmd == 'gui':
                openctrlpanel()
            elif cmd == 'info':
                def callback(path, args, types, src):
                    info = args
                #TODO
                pass
            elif cmd.startswith('!!'):
                pycmd = cmd[2:]
                try:
                    out = eval(pycmd)
                    print out
                except:
                    print "\nException: %s", sys.exc_info()

            elif cmd.startswith('!'):
                systemcmd = cmd[1:]
                os.system(systemcmd)
            elif cmd:
                tokens = cmd.split()
                if tokens[0].startswith('/'):
                    path, args = unpack(tokens)
                    host, port = DEFAULT_ADDR
                else:
                    addr, path, args = unpack(tokens)
                    try:
                        host, port = parse_addr(addr)
                    except ValueError:
                        continue
                args = parse_args(args)
                if autoget and path.endswith("/get"):
                    args.insert(0, 0)
                    print "args --> ", args
                try:
                    s.send((host, port), path, *args)
                    print path, args
                except IOError:
                    print "Could not send to ({host}, {port}). Error:\n{error}".format(host=host, port=port, error= sys.exc_info())
                time.sleep(0.2)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        s.stop()
        s.free()

