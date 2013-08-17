import sys
import monitorcore
import liblo

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

if __name__ == "__main__":
	exclude = argv_getoption(sys.argv, '--exclude', default='', remove=True)
	if ":" in exclude:
		exclude = exclude.split(":")
	else:
		exclude = [exclude]
	if exclude:
		print "excluding: %s" % exclude

	try:
		app = monitorcore.App(coreaddr=('127.0.0.1', 47120), exclude=exclude)
	except RuntimeError:
		sys.exit(0)

	try:
		app.mainloop()
	except KeyboardInterrupt:
		pass
	