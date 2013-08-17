from collections import OrderedDict as _OrderedDict
import json

def sort_natural(l, key=None): 
    """
    sort a sequence 'l' of strings naturally, so that 'item1' and 'item2' are before 'item10'

    key: a function to use as sorting key

    Examples
    ========

    >>> seq = ["e10", "e2", "f", "e1"]
    >>> sorted(seq)
    ['e1', 'e10', 'e2', 'f']
    >>> sort_natural(seq)
    ['e1', 'e2', 'e10', 'f']

    >>> seq = [(2, "e10"), (10, "e2")]
    >>> sort_natural(seq, key=lambda tup:tup[1])
    [(10, "e2"), (2, "e10")]
    >>> sort_natural(seq, key=1) # this is the same as above
    [(10, "e2"), (2, "e10")]
    """
    import re
    if isinstance(key, int):
        import operator
        key = operator.itemgetter(key)
    convert = lambda text: int(text) if text.isdigit() else text.lower() 
    alphanum_key = lambda key: [ convert(c) for c in re.split('([0-9]+)', key) ] 
    if key:
        keyfunc = lambda x:alphanum_key(key(x))
    else:
        keyfunc = alphanum_key
    return sorted(l, key=keyfunc)

def sort_natural_dict(d, recursive=True, aslist=False):
    """
    sort dict d naturally and recursively
    """
    rows = []
    if recursive:
        for key, value in d.iteritems():
            if isinstance(value, dict):
                value = sort_natural_dict(value, recursive=recursive, aslist=aslist)
            rows.append((key, value))
            sorted_rows = sort_natural(rows, key=0)
    else:
        sorted_rows = [(key, d[key]) in sort_natural(d)]
    if aslist:
        return sorted_rows
    return _OrderedDict(sorted_rows)

def argv_getflag(argv, arg, remove=False):
    """
    Return if arg is in argv

    if remove, remove arg from argv
    """
    if arg in argv:
        if remove:
            argv.remove(arg)
        return True
    return False

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

# class MixedIndentEncoder(json.JSONEncoder):
#     def default(self, obj):
#         if isinstance(obj, (list, tuple)):
#             return repr(obj)
#         else:
#             return json.JSONEncoder.default(self, obj)

