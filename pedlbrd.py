#!/usr/bin/env python
import pedlbrd
from pedlbrd import gui
import time

WITHGUI = True

if WITHGUI:
	gui.prepare()
	p = pedlbrd.Pedlbrd(autostart=False, restore_session=False)
	p.start(async=True)
	try:
		gui.start(p.config['osc_port'])
	except KeyboardInterrupt:
		pass
	p.stop()

else:
	p = pedlbrd.Pedlbrd(autostart=False)
	p.start(async=False)



