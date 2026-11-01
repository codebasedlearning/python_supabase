# (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
"""One exception type for "tell the user, no traceback"."""


class CliError(Exception):
    """An expected failure with a human-readable message."""
