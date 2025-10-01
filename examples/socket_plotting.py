import json
from datetime import datetime
import sys
import socketio
import pyqtgraph as pg
from PyQt5 import QtWidgets, QtCore
from pyqtgraph import AxisItem
import numpy as np

previous_time = 0

def send_message_to_server(channel, message=None):
    # This function sends a custom message to the server.
    if message is None:
        sio.emit(channel)
    else:
        sio.emit(channel, message)


# Create a Socket.IO client
sio = socketio.Client()

# Global variables to store device information
device_name = "Unknown Device"
# sensor_locations = []
# sensor_locations = [
#     (-260.03, 133.03),
#     (-260.03, 133.03),
#     (260.03, 133.03),
#     (260.03, 133.03),
#     (-260.03, -133.03),
#     (-260.03, -133.03),
#     (260.03, -133.03),
#     (260.03, -133.03)
# ]
sensor_locations = [
    (-133.03, 260.03),
    (-133.03, 260.03),
    (133.03, 260.03),
    (133.03, 260.03),
    (-133.03, -260.03),
    (-133.03, -260.03),
    (133.03, -260.03),
    (133.03, -260.03)
]



# Maximum number of data points to display
max_data_points = 1000


# Custom AxisItem to display human-readable dates
class TimeAxisItem(AxisItem):
    def tickStrings(self, values, scale, spacing):
        strings = []
        for value in values:
            try:
                # Check if the value is finite and then convert
                if np.isfinite(value):
                    dt = datetime.fromtimestamp(value / 1000.0)
                    strings.append(dt.strftime('%Y-%m-%d %H:%M:%S'))
                else:
                    strings.append('')
            except Exception as e:
                print(f"Error converting timestamp: {e}, value={value / 1000.0}")
                strings.append('')
        return strings


# Lists to hold incoming data for time and x, y, z values
time = []
data_x = []
data_y = []
data_z = []

# Create a PyQt application
app = QtWidgets.QApplication(sys.argv)

# Create a pyqtgraph window for force data
win = pg.GraphicsLayoutWidget(show=True)
win.setWindowTitle(device_name)
plot = win.addPlot(title=device_name, axisItems={'bottom': TimeAxisItem(orientation='bottom')})
plot.addLegend()

# Create curves for each data series
curve_x = plot.plot(pen='r', name='X-axis')
curve_y = plot.plot(pen='g', name='Y-axis')
curve_z = plot.plot(pen='b', name='Z-axis')

# Create another plot for sensor locations and CoP
plot_sensors = win.addPlot(title="Sensor Locations and CoP")
plot_sensors.setAspectLocked(True)  # Keep the aspect ratio locked

# Create curves for sensors and CoP
sensor_curve = plot_sensors.plot(pen=None, symbol='o', symbolBrush=(255, 0, 0), name='Sensors')
cop_curve = plot_sensors.plot(pen=None, symbol='x', symbolBrush=(0, 255, 0), name='CoP')
# Fix the plot range
plot_sensors.setXRange(-300, 300)
plot_sensors.setYRange(-150, 150)


def update():
    curve_x.setData(np.array(time), np.array(data_x))
    curve_y.setData(np.array(time), np.array(data_y))
    curve_z.setData(np.array(time), np.array(data_z))

    # Update sensor locations and CoP plot
    if sensor_locations:
        sensor_x, sensor_y = zip(*sensor_locations)
        sensor_curve.setData(sensor_x, sensor_y)


def plot_cop(cop_x, cop_y):
    cop_curve.clear()  # Clear previous CoP data to avoid broadcasting issues
    cop_curve.setData([cop_x], [cop_y])  # Plot the latest CoP


def append_data(time_list, data_list, time_value, value):
    if time_value:
        time_list.append(time_value)
    data_list.append(value)
    if len(data_list) > max_data_points:
        data_list.pop(0)
    if len(time_list) > max_data_points:
        time_list.pop(0)


def update_device_name():
    global device_name, sensor_locations
    plot.setTitle(device_name)
    win.setWindowTitle(device_name)


@sio.event
def connect():
    print("Connected to the server")
    sio.emit('getConnectedDevices')
    sio.emit('tareAll')


@sio.event
def disconnect():
    print("Disconnected from the server")


@sio.on('connectedDeviceList')
def on_connected_device_list(data):
    global device_name, sensor_locations
    print("Received connectedDeviceList response from server:")
    if data:
        device = data[0]  # Assuming there is at least one device
        device_name = device['name']
        sensors = device['devices'][0]['sensors']
        sensor_locations = [(sensor['location']['x'], sensor['location']['y']) for sensor in sensors if
                            sensor['location']['x'] is not None and sensor['location']['y'] is not None]

        # Schedule update of plot title and window title in the main thread
        QtCore.QTimer.singleShot(0, update_device_name)

        print(f"Device Name: {device_name}")
        print(f"Sensor Locations: {sensor_locations}")


@sio.on('jsonData')
def on_json_message(data):
    global device_name, previous_time
    try:
        if 'arent' in data['deviceId']:  # Only worry about the "Parent" device
            timestamp = data['time'] / 1000.0  # Convert to seconds if needed
            sensors = data['sensors']
            cop = data['cop']
            moments = data['moments']
            print(f'Time Difference: {timestamp - previous_time}')
            previous_time = timestamp

            # Extract force values from sensors
            Fx = sum(sensor['x'] for sensor in sensors)
            Fy = sum(sensor['y'] for sensor in sensors)
            Fz = sum(sensor['z'] for sensor in sensors)

            append_data(time, data_x, timestamp, Fx)
            append_data(time, data_y, None, Fy)
            append_data(time, data_z, None, Fz)

            plot_cop(cop['x'] * 1000, cop['y'] * 1000)  # Convert to mm if needed

        update()  # Update the plots with new data
    except (ValueError, IndexError, KeyError) as e:
        print(f"Error parsing JSON data: {e}")
        print(data)


@sio.on('csvData')
def on_message(new_data):
    global device_name
    # Parse the CSV data
    row = new_data.split(',')
    try:
        timestamp = float(row[0])
        serial_id = row[1]
        if "16" in serial_id:
            Fx = float(row[2])
            Fy = float(row[3])
            Fz = float(row[4])
            COPx = float(row[5]) * 1000 if row[5] else 0
            COPy = float(row[6]) * 1000 if row[6] else 0

            # if 'arent' in serial_id:  # Demo Left Plate
            append_data(time, data_x, timestamp, Fx)
            append_data(time, data_y, None, Fy)
            append_data(time, data_z, None, Fz)

            plot_cop(COPx, COPy)

            update()  # Update the plots with new data
    except (ValueError, IndexError):
        print("Error parsing data")


# Connect to the Socket.IO server
sio.connect('http://localhost:3000')

# Start the PyQt event loop
timer = pg.QtCore.QTimer()
timer.timeout.connect(update)  # Connect update function to the timer
timer.start(1)  # Update every 1 ms

# Run the application
if (sys.flags.interactive != 1) or not hasattr(QtCore, 'PYQT_VERSION'):
    QtWidgets.QApplication.instance().exec_()

# Disconnect from the server
sio.disconnect()
