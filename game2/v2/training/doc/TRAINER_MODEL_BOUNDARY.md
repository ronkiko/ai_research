# Trainer And Model Boundary

The future Trainer-to-Player/model interface must be a formal training
contract. A Trainer must not import model runtime implementation modules or
Console internals to obtain privileged gameplay state.
