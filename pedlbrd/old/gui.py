from PySide.QtCore import *
from  PySide.QtGui import *
import sys

qt_app = QApplication(sys.argv)
 
class Pedlbrd(QWidget):
    ''' An example of PySide/PyQt absolute positioning; the main window
        inherits from QWidget, a convenient widget for an empty window. '''
 
    def __init__(self):
        # Initialize the object as a QWidget and
        # set its title and minimum width
        QWidget.__init__(self)
        self.setWindowTitle('Pedlbrd')
        self.setMinimumWidth(400)
 
        # Create the QVBoxLayout that lays out the whole form
        self.layout = QVBoxLayout()
 
        # Create the form layout that manages the labeled controls
        self.form_layout = QFormLayout()
        self.form_layout.setFormAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.status = QLabel('connected', self)
        self.form_layout.addRow('STATUS', self.status)
 
        midichannels = [str(i) for i in range(1, 17)]
        self.midichannel = QComboBox(self)
        self.midichannel.addItems(midichannels)
 
        # Add it to the form layout with a label
        self.form_layout.addRow('&MIDI Channel', self.midichannel)
         
        # Add the form layout to the main VBox layout
        self.layout.addLayout(self.form_layout)
 
        # Add stretch to separate the form layout from the button
        self.layout.addStretch(1)
 
        # Create a horizontal box layout to hold the button
        self.button_box = QHBoxLayout()
 
        # Add stretch to push the button to the far right
        
 
        self.calibrate_button = QPushButton('Calibrate', self)
        self.showconfig_button = QPushButton('Show Config', self)
        self.quit_button = QPushButton('Quit', self)
 
        # Add it to the button box
        self.button_box.addWidget(self.calibrate_button)
        self.button_box.addWidget(self.showconfig_button)

        self.button_box.addStretch(1)
        self.button_box.addWidget(self.quit_button)

        # Add the button box to the bottom of the main VBox layout
        self.layout.addLayout(self.button_box)
 
        # Set the VBox layout as the window's main layout
        self.setLayout(self.layout)
 
    def run(self):
        # Show the form
        self.show()
        # Run the qt application
        qt_app.exec_()
 
def startgui():
    # Create an instance of the application window and run it
    app = Pedlbrd()
    app.run()