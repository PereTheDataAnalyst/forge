"""Password policy checks (standard library only)."""

MIN_LENGTH = 10
MAX_LENGTH = 128

_COMMON = frozenset(
    {
        "password", "password1", "password12", "password123", "password1234", "passw0rd123",
        "1234567890", "12345678910", "123456789012", "qwertyuiop", "qwerty12345", "qwerty123456",
        "iloveyou123", "welcome123", "welcome1234", "letmein1234", "admin12345", "administrator",
        "abcdefghij", "abc1234567", "1q2w3e4r5t", "changeme123", "nigeria1234", "lagos12345",
    }
)


def validate_password(password: str, email: str | None = None) -> list[str]:
    """Return a list of human-readable problems. An empty list means the password is acceptable."""
    problems: list[str] = []
    if len(password) < MIN_LENGTH:
        problems.append(f"Use at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        problems.append(f"Use at most {MAX_LENGTH} characters.")
    if password.strip() == "":
        problems.append("The password cannot be only whitespace.")
    if len(set(password)) < 5:
        problems.append("Use a password with more variety than a few repeated characters.")
    if password.lower() in _COMMON:
        problems.append("This password is too common. Choose something less predictable.")
    if email:
        local = email.split("@", 1)[0].lower()
        if len(local) >= 4 and local in password.lower():
            problems.append("The password must not contain the first part of your email address.")
    return problems
