# World Authority

Engine is the only mutable owner of the Avatar and world state. Map parsing,
collision surfaces, and physics are local Engine implementation details. No
external domain receives the mutable world object.
