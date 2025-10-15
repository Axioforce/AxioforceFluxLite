## Models API Integration (Lightweight Frontend)

This project’s frontend communicates with a local backend over socket.io. Model packaging, importing, metadata retrieval, activation/deactivation, and deletion are all done by emitting events to the local backend and listening for corresponding status/data events. The backend handles storage and any cloud interactions on your behalf.

### Connection setup
- **Discover `socketPort`** by requesting the backend config, then initialize the socket and register listeners.

```255:260:src/renderer/App.tsx
static getStartupData = () => {
    dynamoBridge.emit('getDynamoConfig', '');
    dynamoBridge.emit('getGroups', '');
    //dynamoBridge.emit('getAthletes', '');
    //dynamoBridge.emit('getChartOptions', '');
}
```

```270:274:src/renderer/App.tsx
if ( fluxConfig.socketPort && !dynamoBridge.socket ){
    dynamoBridge.initializeSocket(fluxConfig.socketPort)
    this.addEventListeners()
    Axioforce.getStartupData()
}
```

- **Socket creation**:

```32:44:src/renderer/communication/DynamoBridge.ts
initializeSocket(socketPort : string){
    //Create socket connected to specified url
    this.socket = io(`http://localhost:${socketPort}`)

    //When socket connection is complete, connect socket events to handler functions
    this.socket.on('connect', () => {
        this.socket?.onAny((eventName : string, data : any) => {
            this.handleEvent(eventName, data)
        })
    })
}
```

### Events and payloads

- **Get model metadata**
  - Emit: `getModelMetadata` with `{ deviceId: string }`
  - Listen: `modelMetadata` with `ModelMetadata[]`

```47:54:src/renderer/settings/DeviceEditor.tsx
const getModelMetadata = () => {
    setModelMetadata(null);
    if(props.device && props.device.axfId !== ''){
        console.log('GET MODEL METADATA');
        dynamoBridge.emit('getModelMetadata', {
            deviceId: props.device.axfId
        });
    }
}
```

```64:68:src/renderer/settings/DeviceEditor.tsx
dynamoBridge.on('modelMetadata', (data : any) => {
    console.log(modelMetadata);
    console.log(data);
    setModelMetadata(data);
});
```

- **Realtime data streaming**
  - Listen: `jsonData` carries realtime device data frames.
  - Handler prototype: `(data: DeviceDataSetTransmissible) => void`
  - The app updates device caches and UI on each frame.

```212:220:src/renderer/App.tsx
addEventListeners = () => {
    dynamoBridge.on('getGroupsStatus', this.handleGroupList);
    dynamoBridge.on('jsonData', this.handleNewData);
    dynamoBridge.on('error', this.displayError);
    dynamoBridge.on('getDynamoConfigStatus', this.handleDynamoConfig);
    dynamoBridge.on('captureUpdate', this.handleCaptureStatus);
    dynamoBridge.on('updateDynamoConfigStatus', this.handleDynamoConfig);
    dynamoBridge.on('setModelBypassStatus', this.handleModelBypassStatus);
    dynamoBridge.on('battery', this.handleBattery);
}
```

```364:395:src/renderer/App.tsx
//Handle processing new data sets from server
handleNewData = (data : DeviceDataSetTransmissible) => {
    if(deviceManager.groupMap.get(data.groupId)){
        const thisSerialDevice = deviceManager.groupMap.get(data.groupId)?.devices.get(data.deviceId);
        if(thisSerialDevice){
            if(thisSerialDevice.data.length > this.state.fluxConfig.maxDataSetCache){
                thisSerialDevice.data.shift();
            }
            thisSerialDevice.data.push(data);
            if(!this.state.playbackActive){
                if(this.state.isRealTime){
                    deviceManager.updateXYZ(data);
                    if(data.groupId === deviceManager.selectedGroupId){
                        this.setState({ dataRate : data.dataRate })
                    }
                    // update counters and chart series
                    axfChartManager.appendData(data);
                }
            }
        } else {
            dynamoBridge.emit('getGroups',['']);
        }
    } else {
        dynamoBridge.emit('getGroups',['']);
    }
}
```

- **Sample jsonData frame**
  - Event: `jsonData`

```json
{
    "deviceId": "06.00000030",
    "time": 1760466841735,
    "recordId": 28699,
    "sensors": [
        {
            "name": "Front Left Outer",
            "axfId": "06.00000030.0",
            "x": -0.0002169994891974293,
            "y": -0.010267902971947138,
            "z": -0.0011417449766282298,
            "vector": 0.010333465140565802
        },
        {
            "name": "Front Left Inner",
            "axfId": "06.00000030.1",
            "x": -0.0005786653045273005,
            "y": -0.000800096335475662,
            "z": -0.000897085338780736,
            "vector": 0.0013340801271889949
        },
        {
            "name": "Front Right Outer",
            "axfId": "06.00000030.2",
            "x": -0.004249573330120025,
            "y": -0.02226934800409306,
            "z": -0.003191449053719614,
            "vector": 0.022894717318149155
        },
        {
            "name": "Front Right Inner",
            "axfId": "06.00000030.3",
            "x": -0.0015551630059161437,
            "y": -0.024136239453537787,
            "z": -0.0018104813200841112,
            "vector": 0.02425395698734245
        },
        {
            "name": "Rear Right Outer",
            "axfId": "06.00000030.4",
            "x": -0.0077034818665161426,
            "y": -0.012268143810637976,
            "z": -0.005007367254643945,
            "vector": 0.015327253904373032
        },
        {
            "name": "Rear Right Inner",
            "axfId": "06.00000030.5",
            "x": -0.003056076139533341,
            "y": -0.002800337174167849,
            "z": -0.002615139684565279,
            "vector": 0.0049010657238441075
        },
        {
            "name": "Rear Left Outer",
            "axfId": "06.00000030.6",
            "x": -0.0045389059823836755,
            "y": -0.009934529498831854,
            "z": -0.0012885407593379126,
            "vector": 0.010998039878476243
        },
        {
            "name": "Rear Left Inner",
            "axfId": "06.00000030.7",
            "x": -0.003236909047197866,
            "y": -0.014868456900936362,
            "z": -0.001511452873822547,
            "vector": 0.01529160163568944
        },
        {
            "name": "Sum",
            "axfId": "06.00000030.sum",
            "x": -0.025135774165391922,
            "y": -0.09734505414962769,
            "z": -0.01746326126158237,
            "vector": 0.10204328593385169
        }
    ],
    "moments": {
        "x": -0.019562752917408943,
        "y": -0.029296021908521652,
        "z": 0.01353416871279478
    },
    "cop": {
        "x": 0,
        "y": 0
    },
    "groupId": "06.00000030",
    "dataRate": 459
}
```

#### Do I need `startCapture` to receive `jsonData`?

- **No.** The renderer listens for `jsonData` immediately after the socket is initialized; there is no separate `startStream` event in the renderer. Frames are buffered regardless, and the UI updates charts only when in realtime mode.

```364:395:src/renderer/App.tsx
// Always buffer incoming frames
thisSerialDevice.data.push(data);

// UI updates are gated by realtime/playback state
if(!this.state.playbackActive){
  if(this.state.isRealTime){
    deviceManager.updateXYZ(data);
    if(data.groupId === deviceManager.selectedGroupId){
      this.setState({ dataRate : data.dataRate })
    }
    axfChartManager.appendData(data);
  }
}
```

- **When to use `startCapture`:** Use it to begin a recording session and receive `captureUpdate` progress/completion events. It’s not required for live `jsonData` frames.

- **Starting/stopping streaming (capture-driven)**
  - Emit: `startCapture` to begin streaming/logging for a group/context
  - Emit: `stopCapture` to end; listen to `captureUpdate` for progress and completion
  - Typical payloads:
    - Start: `{ groupId: string, captureType: string, captureName?: string | null, tags?: string[] }`
    - Stop: `{ groupId: string }`

```491:498:src/renderer/App.tsx
else if(instruction === 'startCapture'){
    this.setState({isCapturing : true})
    dynamoBridge.emit('startCapture', payload)
}
else if(instruction === 'stopCapture'){
    this.setState({isCapturing : false})
    dynamoBridge.emit('stopCapture', payload)
}
```

```63:76:src/renderer/Capture.tsx
const captureInstruction = {
    groupId : props.selectedGroupId,
    captureType : selectedCaptureType,
    captureName : captureName,
    tags: ['AxioforceFlux2', 'AxioforceFlux2Jump']
};
props.handleComponentInstruction('startCapture', captureInstruction);
```

```105:111:src/renderer/Capture.tsx
props.handleComponentInstruction('stopCapture', {
    groupId : props.selectedGroupId
});
```

```171:191:src/renderer/App.tsx
handleCaptureStatus = (thisUpdate: CaptureTransmissible) => {
    if (thisUpdate.status === 'cancelled') {
        this.setState({ currentCapture: null, currentPhase: null, isCapturing: false });
    } else if (thisUpdate.stopTime) {
        this.setState({ currentCapture: thisUpdate, currentPhase: null, isCapturing: false });
        if(thisUpdate.captureTypeId === 'weight'){
            dynamoBridge.emit('getAthletes', ['']);
        }
    } else {
        const thisPhase = thisUpdate.phases.find((phase) => phase.phaseId === thisUpdate.currentPhaseId)
        this.setState({ currentCapture: thisUpdate, currentPhase: thisPhase });
    }
}
```

> Note: Diagnostics listeners also exist for `parsedData` and `rawHex` streams used in diagnostic views.

- **Activate / Deactivate a model**
  - Emit: `activateModel` or `deactivateModel` with `{ deviceId: string, modelId: string }`
  - Listen: `modelActivationStatus`

```245:258:src/renderer/settings/DeviceEditor.tsx
const handleActivateModel = (e: React.ChangeEvent<HTMLInputElement>) => {
    const modelId = e.target.name;
    if(props.device && modelId){
        if(e.target.checked){
            dynamoBridge.emit('activateModel', {
                deviceId: props.device.axfId,
                modelId: modelId
            });
        }
        else{
            dynamoBridge.emit('deactivateModel', {
                deviceId: props.device.axfId,
                modelId: modelId
            });
        }
    }
}
```

- **Delete a model**
  - Emit: `deleteModel` with `{ deviceId: string, modelId: string }`
  - Listen: `modelDeletionStatus`

```268:276:src/renderer/settings/DeviceEditor.tsx
if(props.device){
    dynamoBridge.emit('deleteModel', {
        deviceId: props.device.axfId,
        modelId: returnValue
    });
}
```

- **Package a model**
  - Emit: `packageModel` with `{ forceModelDir: string, momentsModelDir: string, outputDir: string }`
  - Listen: `modelPackageStatus`

```77:85:src/renderer/ModelPackager.tsx
const handlePackageModel = () => {
    console.log('Package Model');
    if(forceModelDir !== '' || momentsModelDir !== '' || outputDir !== ''){
        dynamoBridge.emit('packageModel', {
            forceModelDir: forceModelDir,
            momentsModelDir: momentsModelDir,
            outputDir: outputDir
        });
    }
}
```

```57:65:src/renderer/ModelPackager.tsx
dynamoBridge.on('modelPackageStatus', (response : StatusUpdate) => {
    console.log(response);
    openStatusWindow(response);
});
```

- **Import/load a packaged model**
  - Emit: `loadModel` with `{ modelDir: string }` (path to `.axf-tfpkg`)
  - Listen: `modelLoadStatus`

```96:101:src/renderer/ModelPackager.tsx
dynamoBridge.emit('loadModel', {
    modelDir: dir.path
});
```

```62:65:src/renderer/ModelPackager.tsx
dynamoBridge.on('modelLoadStatus', (response : StatusUpdate) => {
    console.log(response);
    openStatusWindow(response);
});
```

### Types

- **ModelMetadata**

```1:10:src/types/model.ts
export type ModelMetadata = {
    modelId : string;
    deviceId : string;
    packageDate : number;
    forceModelTimestamp : number;
    momentModelTimestamp : number;
    forceModelRunNumber : number;
    momentModelRunNumber : number;
    location : 'local' | 'remote' | 'both';
}
```

### Minimal client example (socket.io-client)

```ts
import { io } from 'socket.io-client';

export function createModelsClient(socketPort: number){
  const socket = io(`http://localhost:${socketPort}`);

  // listeners (register early)
  socket.on('jsonData', (frame) => console.log('frame', frame));
  socket.on('captureUpdate', (u) => console.log('capture', u));
  socket.on('modelMetadata', (models) => console.log('metadata', models));
  socket.on('modelPackageStatus', (s) => console.log('package', s));
  socket.on('modelLoadStatus', (s) => console.log('load', s));
  socket.on('modelActivationStatus', (s) => console.log('activate', s));
  socket.on('modelDeletionStatus', (s) => console.log('delete', s));

  return {
    getModelMetadata: (deviceId: string) => socket.emit('getModelMetadata', { deviceId }),
    activateModel: (deviceId: string, modelId: string) => socket.emit('activateModel', { deviceId, modelId }),
    deactivateModel: (deviceId: string, modelId: string) => socket.emit('deactivateModel', { deviceId, modelId }),
    deleteModel: (deviceId: string, modelId: string) => socket.emit('deleteModel', { deviceId, modelId }),
    packageModel: (forceModelDir: string, momentsModelDir: string, outputDir: string) =>
      socket.emit('packageModel', { forceModelDir, momentsModelDir, outputDir }),
    loadModel: (modelDir: string) => socket.emit('loadModel', { modelDir }),
    // streaming via capture
    startCapture: (groupId: string, captureType: string, captureName?: string | null, tags?: string[]) =>
      socket.emit('startCapture', { groupId, captureType, captureName: captureName ?? null, tags }),
    stopCapture: (groupId: string) => socket.emit('stopCapture', { groupId }),
  };
}
```

### Notes
- The frontend does not call any online DB directly for models; all cloud operations (e.g., deletion from device and cloud) are initiated via these backend events and handled server-side.
- Ensure you request config and establish the socket before emitting model events.

### Data rate (sampling vs emission)

- **Get configured rates**: emit `getDynamoConfig`, listen on `getDynamoConfigStatus` and read `samplingRate` and `emissionRate`.
- **Set configured rates**: emit `setSamplingRate` and `setDataEmissionRate` with a number.
- **Observe live rate**: read `dataRate` from each `jsonData` frame.

Code references

```212:220:src/renderer/App.tsx
addEventListeners = () => {
    dynamoBridge.on('getDynamoConfigStatus', this.handleDynamoConfig);
}
```

```255:260:src/renderer/App.tsx
static getStartupData = () => {
    dynamoBridge.emit('getDynamoConfig', '');
    dynamoBridge.emit('getGroups', '');
}
```

```5:9:src/renderer/communication/ConfigEventMap.ts
configUpdateMap.set('emissionRate', 'setDataEmissionRate');
configUpdateMap.set('samplingRate', 'setSamplingRate');
```

```103:116:src/renderer/settings/AdvancedSettings.tsx
<AXFInput label="Sampling Rate" value={props.dynamoConfig.samplingRate} name="samplingRate" type="number" onChange={props.handleValueChange} pattern={/^-1$|^0$|^[1-9]$|^[1-9]\d$|^[1-9]\d\d$|^1[01]\d\d$|^1200$/} patternMessage='Must be a number between -1 and 1200'/>
<AXFInput label="Data Emission Rate" value={props.dynamoConfig.emissionRate} name="emissionRate" type="number" onChange={props.handleValueChange} pattern={/^-1$|^0$|^[1-9]$|^[1-9]\d$|^[1-4]\d\d$|^500$/} patternMessage='Must be a number between -1 and 500'/>
```

```376:380:src/renderer/App.tsx
if(data.groupId === deviceManager.selectedGroupId){
    this.setState({ dataRate : data.dataRate })
}
```

Minimal client

```ts
// get configured rates
socket.emit('getDynamoConfig', '');
socket.on('getDynamoConfigStatus', (resp) => {
  if (resp.status === 'success') {
    console.log('samplingRate', resp.data.samplingRate, 'emissionRate', resp.data.emissionRate);
  }
});

// set configured rates
socket.emit('setSamplingRate', 600);       // allowed: -1..1200
socket.emit('setDataEmissionRate', 250);   // allowed: -1..500

// observe live rate
socket.on('jsonData', (frame) => console.log('dataRate', frame.dataRate));
```

### Tare (zeroing sensors)

- **Purpose**: Zero the force/sensor baseline for the selected group.
- **Emit sequence**:
  1) `setReferenceTime` with `-1`
  2) `tare` with `[groupId]`

Code reference

```440:447:src/renderer/App.tsx
if(instruction === 'tareDevice'){
    if(deviceManager.selectedGroupId){
        dynamoBridge.emit('setReferenceTime', -1);
        dynamoBridge.emit('tare', [deviceManager.selectedGroupId]);
    }
}
```

Minimal client

```ts
socket.emit('setReferenceTime', -1);
socket.emit('tare', [groupId]);
```