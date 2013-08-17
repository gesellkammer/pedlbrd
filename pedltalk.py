#!/usr/bin/env python
import os
import sys
import liblo
from fnmatch import fnmatch
import time
import atexit 

HISTFILE = os.path.join(os.path.expanduser("~"), ".pedlbrd/pedltalk.hist")

class Completer(object):
    def __init__(self):
        self.matches = [
            '/smoothing/get',
            '/smoothing/set',
            '/openlog',
            '/calibrate',
            '/resetstate',
            '/registerdata',
            '/registerall',
            '/registerui',
            '/api/get',
            '/midichannel/set',
            '/midichannel/get',
            '/midicc/get',
            '/midicc/set',
            '/maxanalog/get',
            '/maxanalog/set',
            '/quit',
            '/getstatus',
            '/heartperiod/get',
            '/heartperiod/set',
            '/dumpconfig',
            '/dataaddr/get',
            '/uiaddr/get',
            '/digitalmapstr/get',
            '/help',
            '/echo',
            '/filtertype/get',
            '/filtertype/set',
            'quit'
        ]
    def complete(self, text, state):
        out = []
        for word in self.matches:
            if word.startswith(text):
                out.append(word)
        try:
            return out[state]
        except IndexError:
            return None

try:
    import readline
    readline.parse_and_bind("tab: complete")
    atexit.register(readline.write_history_file, HISTFILE)
    try:
        readline.read_history_file(HISTFILE)
    except IOError:
        pass
    readline.set_completer(Completer().complete)
    readline.set_completer_delims(' \t\n`!@#$^&*()=+[{]}|;:\'",<>?')
except ImportError:
    pass

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

# MAIN

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

if broadcast:
    DEFAULT_ADDR = ('255.255.255.255', 47120)
else:
    DEFAULT_ADDR = (hostname, 47120)

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

def oscdump(path, args, types, src):
    for wildcard in exclude_wildcards:
        if fnmatch(path, wildcard):
            return
    doit = False
    for wildcard in include_wildcards:
        if fnmatch(path, wildcard):
            doit = True
            break
    if doit:
        if len(args) < 4:
            arg_str = ", ".join(map(str, args)) if args else ""
            print "{host}:{port} {path} {types} {args}".format(
                path=path.ljust(18), types=types.ljust(6), host=src.hostname, port=src.port,
                args=arg_str)
        else:
            print "{host}:{port} {path}".format(host=src.hostname, port=src.port, path=path)
            for arg in args:
                print "       ", arg

def heartbeat_handler(path, args, types, src):
    pass

s.add_method('/heartbeat', None, heartbeat_handler)
s.add_method(None, None, oscdump)


msg = "Listening to port: {port}".format(port=port)
print
print "-" * len(msg)
print msg
print "-" * len(msg)
print
print "include paths matching:", " ".join(include_wildcards)
print "exclude paths matching:", " ".join(exclude_wildcards)
print
print "To send, type [{address}] {path} [arg1 arg2 ... argn]"
print """
Example
-------

pedl> /path 'hello' 10.2
pedl> 196.168.0.100:47120 /add 2 3.1

Enter 'quit' or 'q' or press CTRL-D or CTRL-C to exit
"""

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

s.start()

try:
    while True:
        print
        cmd = raw_input("pedl> ")
        cmd = cmd.strip()
        if cmd == "quit" or cmd=="q":
            break
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
            except IOError:
                print "Could not send to ({host}, {port}). Error:\n{error}".format(host=host, port=port, error= sys.exc_info())
            time.sleep(0.2)
except (KeyboardInterrupt, EOFError):
    pass
finally:
    s.stop()
    s.free()

