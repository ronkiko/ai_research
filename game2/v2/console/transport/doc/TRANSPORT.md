# Transport Boundary

Control queues and publisher queues are bounded. Observer delivery is
non-blocking for Engine, and per-client response policies preserve the existing
ACK behavior. Generic framing is implemented by the public framing contract;
channel policy remains Console-private.
