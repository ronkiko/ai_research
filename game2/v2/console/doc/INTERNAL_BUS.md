# Internal Bus

The Console-owned channels are Engine CONTROL, STATE, TELEMETRY, and EVENTS.
Controller is the only gameplay client of Engine CONTROL. Display consumes
STATE. Publishers use bounded, non-blocking policies so observers cannot pause
the Engine.
