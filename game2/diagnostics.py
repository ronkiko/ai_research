"""Optional console diagnostics must never own the runtime lifecycle."""


def console_message(*args, **kwargs):
    """A closed/detached terminal is not a simulation or checkpoint failure."""
    try:
        print(*args, **kwargs)
    except (OSError, ValueError):
        # Only console writes are best effort. File/checkpoint errors still propagate.
        pass
