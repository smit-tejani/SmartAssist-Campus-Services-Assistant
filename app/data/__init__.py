"""Data primitives for the SmartAssist application."""

from .campus_maps import (
    CampusLocation,
    CampusMap,
    MapVariant,
    get_campus_map,
)

__all__ = [
    "CampusLocation",
    "CampusMap",
    "MapVariant",
    "get_campus_map",
]
