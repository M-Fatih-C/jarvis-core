"""Exceptions for Firebase and Firestore integration."""


class FirebaseError(Exception):
    """Base exception for all Firebase operations."""
    pass


class FirestoreUnavailableError(FirebaseError):
    """Raised when Firestore or Firebase Emulator cannot be reached."""
    pass


class FirestorePermissionError(FirebaseError):
    """Raised when Firestore security rules reject an operation."""
    pass
