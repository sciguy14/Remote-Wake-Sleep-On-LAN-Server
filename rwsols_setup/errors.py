class SetupError(Exception):
    """A problem the user should read; the wizard prints it and stops the current flow."""


class CommandError(SetupError):
    pass


class NetworkError(SetupError):
    pass


class ApiError(SetupError):
    def __init__(self, message, status=None, errors=None):
        SetupError.__init__(self, message)
        self.status = status
        self.errors = errors or []


class ConflictError(SetupError):
    """Something already exists that the wizard did not create. Needs --force or user action."""
