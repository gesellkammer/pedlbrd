def sort_natural(l, key=None):
    """
    sort a sequence 'l' of strings naturally, so that 'item1' and 'item2' are before 'item10'
    """
    import re
    convert = lambda text: int(text) if text.isdigit() else text.lower()
    alphanum_key = lambda key: [ convert(c) for c in re.split('([0-9]+)', key) ]
    if key:
        keyfunc = lambda x:alphanum_key(key(x))
    else:
        keyfunc = alphanum_key
    return sorted(l, key=keyfunc)

