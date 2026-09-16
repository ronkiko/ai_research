# Scheduling

Controller uses the latest Engine telemetry snapshot to place commands ahead of
the current tick and applies the configured finite hold. Missing decisions do
not create an infinite hold and do not pause Engine time. This document does
not define a new timing contract for Joystick v1.
