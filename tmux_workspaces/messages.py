"""Status severity remains visible without relying on color."""

ERROR = "Error: "


def failure(reason):
    reason = " ".join(str(reason).split()) or "the settings were not saved"
    return reason if reason.startswith(ERROR) else ERROR + reason


def failed(message):
    return message.startswith(ERROR)
