#!/usr/bin/env python
import pedlbrd
from pedlbrd import qtgui as gui
import time

WITHGUI = True

if WITHGUI:
	p = pedlbrd.Pedlbrd(autostart=False, restore_session=False)
	p.start(async=True)
	gui.startgui(async=False)
	

else:
	p = pedlbrd.Pedlbrd(autostart=False)
	p.start(async=False)



