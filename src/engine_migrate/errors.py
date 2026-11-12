"""Exceptions that the CLI turns into exit code 2."""

from __future__ import annotations


class EngineMigrateError(Exception):
    """Base class for user-facing errors."""


class UnknownEngineError(EngineMigrateError):
    """The engine name is not in the database."""


class UnknownVersionError(EngineMigrateError):
    """The version is not in the database for that engine."""


class ConfigParseError(EngineMigrateError):
    """The input configuration could not be understood."""
