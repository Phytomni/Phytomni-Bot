# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)

"""Exception declarations used by Phytomni's asyncpg boundary."""


class PostgresError(Exception):
    """Base class for driver-reported database errors."""


class InterfaceError(Exception):
    """Driver connection/interface failure."""


class FeatureNotSupportedError(PostgresError):
    """Database feature is not supported by the active backend."""
