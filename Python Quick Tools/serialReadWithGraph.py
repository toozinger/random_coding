# -*- coding: utf-8 -*-
"""
Created on Fri Jan 21 13:02:42 2022

@author: dowdt
"""

from datetime import datetime
import time
import serial
import serial.tools.list_ports
import sys
import pandas as pd
import os

from PyQt5 import QtWidgets, QtCore
# from pyqtgraph import PlotWidget, plot
import pyqtgraph as pg




# Function for checking what COM ports are connected, and which are open
# ******************************************************************
def checkComPorts():
    rawPorts = serial.tools.list_ports.comports()   
    
    serialPorts = []
    serialPortDescriptions = []
    
    # find the open ports, and their hwids
    for portNumber, portDescription, VID_PID in sorted(rawPorts):
          serialPorts.append(portNumber)
          serialPortDescriptions.append(portDescription) 
          
    return serialPorts, serialPortDescriptions        
        
# Function to open connection to serial port
# ******************************************************************
def openSerial(COMport, baudRate):

    # Try to open connection
    try:
        serialDataConnection = serial.Serial(COMport,baudRate,timeout=0.1, write_timeout=0.5)
        serialDataConnection.flushInput()
        time.sleep(0.2) # wait for Arduino
        # print("Controller Arduino Connected")
        return serialDataConnection
    
    # Attemps to self-fix issue from un-closed ports
    except:
        pass
        # print("Failed to connect to Arduino. Trying again")
        
    # try to close the serial if it's open
    try:
        serialDataConnection.close()   
    except:
        pass
       
    try:
        serialDataConnection = serial.Serial(COMport,baudRate,timeout=5)
        serialDataConnection.flushInput()
        time.sleep(2) # wait for Arduino
        # print("Controller Arduino Connected")
        return serialDataConnection
    except:
        print("Failed to connect. Serial busy with another program")
        return False

# Manually run this to check which ports are available
# ******************************************************************
serialPorts, serialPortDescriptions = checkComPorts()
print(f"Serial ports connected: {serialPorts}")
print(f"Serial port descriptions: {serialPortDescriptions}")
           

# Main Program    
# ******************************************************************  ******************************************************************

class MainWindow(QtWidgets.QMainWindow):

    def __init__(self, *args, **kwargs):
        super(MainWindow, self).__init__(*args, **kwargs)

        self.graphWidget = pg.PlotWidget()
        self.setCentralWidget(self.graphWidget)
        
        self.startTime = datetime.now()
        
        # Initialize plot variables
        self.x = []
        self.y = []

        self.graphWidget.setBackground('w')

        pen = pg.mkPen(color=(255, 0, 0))
        self.data_line =  self.graphWidget.plot(self.x, self.y, pen=pen)
        
        # Timer for plotting update interval
        self.plotTimer = QtCore.QTimer()
        self.plotTimer.setInterval(100)
        self.plotTimer.timeout.connect(self.update_plot_data)
        self.plotTimer.start()
        
        # Timer for checking serial (only reads if there's data)
        self.serialReadTimer = QtCore.QTimer()
        self.serialReadTimer.setInterval(5)
        self.serialReadTimer.timeout.connect(self.readSerial)
        self.serialReadTimer.start()
        
        # Setup communication and save name and location
        COMport = "COM6"
        baudRate = 9600
        saveName = "30s vibe test"
        self.saveFileLocal = "G:\My Drive\Documents\Purdue\GraduateSchool\Hybrid Additive Manufacturing HAM\HAM Tests\Deposition Mass Tests"
        
        # Add current datetime to savefile name, to keep files well documented
        saveFileDatetime = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        self.saveFileName = f"{saveFileDatetime} {saveName}"
        txtSaveFileName = f"{self.saveFileName}.txt"
        self.saveFileFullName = os.path.join(self.saveFileLocal, txtSaveFileName)
        
        # Connect to serial device
        self.serialDataConnection = openSerial(COMport, baudRate)
        
        # Checkc if COM connected
        if not self.serialDataConnection: 
            self.closeEvent()
            
        # Initialization
        self.inWaiting = 0
        self.allData = []
    


    def update_plot_data(self):
        
        self.data_line.setData(self.x, self.y)  # Update the data.

    
    # Function to read the serial data, if new data is available
    def readSerial(self):
        
        if self.inWaiting < self.serialDataConnection.in_waiting:
            try:
                data = self.serialDataConnection.readline().decode('unicode_escape').replace("\r\n","")            
            except:
                data = 0
                print("Error in reading serial line")
                
            data = self.cleanData(data)
            now = datetime.now()
            
            print(f"{now} {data}")
            self.allData.append([now, data])
            with open(self.saveFileFullName, "a") as saveFile: saveFile.write(f"{now}, {data}\n")
            
            timeSinceStart = (datetime.now() - self.startTime).total_seconds()
            
            # print(f"x: {timeSinceStart}")
            # print(f"y: {data}")
            self.x.append(timeSinceStart)
            self.y.append(data)
            
    def cleanData(self, data):
        
        # print(f"RawRead: {data}")
            
        dataList = data.split()
        cleanData = []
        
        for item in dataList:
            try:
                cleanData.append(float(item))
            except ValueError:
                pass
        
        if len(cleanData) == 1: cleanData = cleanData[0]
        else: cleanData = 0
        
        # print(f"Clean Data: {cleanData}")
        
        return cleanData
    
    # Handeling close event nicer
    def closeEvent(self, event):
        
        self.serialReadTimer.stop()
        self.plotTimer.stop()
        
        self.serialDataConnection.close()
        
        # Optional save file to excel
        xlsSaveName = f"{self.saveFileLocal}\\{self.saveFileName}.xlsx"
        allDataDF = pd.DataFrame(self.allData)
        allDataDF.rename(columns={0: "dateTime", 1: "Data"})
        
        with pd.ExcelWriter(xlsSaveName) as writer:
            allDataDF.to_excel(writer, header=False, index=False)
            
        event.accept()
         

app = QtWidgets.QApplication(sys.argv)
w = MainWindow()
w.show()
sys.exit(app.exec_())

