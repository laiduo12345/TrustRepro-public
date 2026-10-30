"""Text observations safe for PostgreSQL, with original files left untouched."""


def visible_nul(value):
    """Show NUL as U+2400 in text values, including nested PDF tables and logs."""
    if isinstance(value, str):
        return value.replace("\x00", "\u2400")
    if isinstance(value, dict):
        return {key: visible_nul(item) for key, item in value.items()}
    if isinstance(value, list):
        return [visible_nul(item) for item in value]
    return value
