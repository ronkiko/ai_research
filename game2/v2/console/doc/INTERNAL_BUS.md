# Internal Bus

The Console-owned channels are Engine CONTROL, STATE, TELEMETRY, and EVENTS.
Controller is the only gameplay client of Engine CONTROL. Display consumes STATE
only, then renders either its human screen or semantic vision branch. Publishers
use bounded, non-blocking latest/FIFO policies so observers cannot pause Engine.
