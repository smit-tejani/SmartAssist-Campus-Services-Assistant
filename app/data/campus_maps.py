"""Campus map datasets used by SmartAssist.

This module exposes two map variants:

``primary``
    The historical dataset that shipped with the original SmartAssist
    chatbot. It focuses on core academic and student-life buildings and is
    the most conservative option when backwards compatibility matters.

``islanderhack``
    A richer map curated for the IslanderHack build of Smart Campus. It
    keeps all of the primary locations while adding residence halls,
    recreation areas, and showcase destinations that power the enhanced UI
    from that branch.

The active dataset can be toggled through
:func:`app.core.config.Settings.campus_map_variant` which ultimately reads
from the ``CAMPUS_MAP_VARIANT`` environment variable.  For developers who
prefer a manual switch, two commented examples are provided inside the
configuration module so the behaviour can be controlled by simply
commenting/uncommenting a line.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Dict, Iterator, Mapping, Optional


@dataclass(frozen=True)
class CampusLocation:
    """Represents a point-of-interest that can be surfaced on the map."""

    name: str
    lat: float
    lng: float
    address: str
    description: str
    hours: Optional[str] = None
    category: Optional[str] = None
    icon: Optional[str] = None

    def to_response(self) -> Dict[str, object]:
        """Serialise the location into a JSON-friendly dictionary."""

        payload = asdict(self)
        # ``None`` values are removed to keep the payload compact for the
        # chatbot responses and to avoid displaying empty strings in the UI.
        return {key: value for key, value in payload.items() if value is not None}


@dataclass(frozen=True)
class CampusMap:
    """A collection of campus locations and lookup helpers."""

    variant: str
    description: str
    locations: Mapping[str, CampusLocation]
    aliases: Mapping[str, str]

    def iter_aliases(self) -> Iterator[tuple[str, CampusLocation]]:
        """Yield every alias paired with the resolved :class:`CampusLocation`."""

        for slug, location in self.locations.items():
            yield slug, location
            yield location.name.lower(), location
        for alias, slug in self.aliases.items():
            if slug in self.locations:
                yield alias, self.locations[slug]

    def lookup(self, text: str) -> Optional[CampusLocation]:
        """Resolve the first location that appears inside ``text``."""

        lowered = text.lower()
        for alias, location in self.iter_aliases():
            if alias in lowered:
                return location
        return None

    def alias_mapping(self) -> Dict[str, Dict[str, object]]:
        """Return a dictionary mapping aliases to serialisable locations."""

        lookup: Dict[str, Dict[str, object]] = {}
        for alias, location in self.iter_aliases():
            lookup[alias] = location.to_response()
        return lookup


class MapVariant(str, Enum):
    PRIMARY = "primary"
    ISLANDERHACK = "islanderhack"


def _primary_map() -> CampusMap:
    locations: Dict[str, CampusLocation] = {
        "library": CampusLocation(
            name="Mary and Jeff Bell Library",
            lat=27.713788736691168,
            lng=-97.32474868648656,
            address="Mary and Jeff Bell Library, TAMUCC",
            description="Main library with study spaces and research resources",
            hours="Mon-Fri 7:30am-11pm",
            category="academics",
        ),
        "university_center": CampusLocation(
            name="University Center (UC)",
            lat=27.712071037382053,
            lng=-97.3257065414334,
            address="University Center, TAMUCC",
            description="Student hub with dining, bookstore, and meeting spaces",
            hours="Mon-Fri 7am-10pm",
            category="student-life",
        ),
        "islander_dining": CampusLocation(
            name="Islander Dining",
            lat=27.711621676963894,
            lng=-97.32258737277509,
            address="Islander Dining, TAMUCC",
            description="Main dining hall with multiple food stations",
            hours="Daily 7am-9pm",
            category="dining",
        ),
        "nrc": CampusLocation(
            name="Natural Resources Center (NRC)",
            lat=27.715332468715157,
            lng=-97.32880933649331,
            address="Natural Resources Center, TAMUCC",
            description="Environmental science and research facility",
            hours="Mon-Fri 8am-5pm",
            category="academics",
        ),
        "engineering": CampusLocation(
            name="Engineering Building",
            lat=27.712772225261283,
            lng=-97.32565431063824,
            address="Engineering Building, TAMUCC",
            description="College of Engineering classrooms and labs",
            hours="Mon-Fri 8am-6pm",
            category="academics",
        ),
        "cch": CampusLocation(
            name="Corpus Christi Hall (CCH)",
            lat=27.71516058584113,
            lng=-97.32370567166191,
            address="Corpus Christi Hall, TAMUCC",
            description="Admissions, financial aid, and student services",
            hours="Mon-Fri 8am-5pm",
            category="student-life",
        ),
        "student_services": CampusLocation(
            name="Student Services Center",
            lat=27.71374042156452,
            lng=-97.32390201020142,
            address="Student Services Center, TAMUCC",
            description="Student support services and administration",
            hours="Mon-Fri 8am-5pm",
            category="student-life",
        ),
        "bay_hall": CampusLocation(
            name="Bay Hall",
            lat=27.713613491472024,
            lng=-97.32348514338884,
            address="Bay Hall, TAMUCC",
            description="Business college classrooms and faculty offices",
            hours="Mon-Fri 8am-5pm",
            category="academics",
        ),
        "sciences": CampusLocation(
            name="Center for the Sciences",
            lat=27.712809298665885,
            lng=-97.32486990268086,
            address="Center for the Sciences, TAMUCC",
            description="Science labs and classrooms",
            hours="Mon-Fri 8am-6pm",
            category="academics",
        ),
        "education": CampusLocation(
            name="College of Education and Human Development",
            lat=27.713186318706956,
            lng=-97.32428916719182,
            address="College of Education and Human Development, TAMUCC",
            description="Education college offices and classrooms",
            hours="Mon-Fri 8am-5pm",
            category="academics",
        ),
        "faculty_center": CampusLocation(
            name="Faculty Center",
            lat=27.712820723536026,
            lng=-97.32358260567656,
            address="Faculty Center, TAMUCC",
            description="Faculty offices and meeting rooms",
            hours="Mon-Fri 8am-5pm",
            category="academics",
        ),
        "dugan": CampusLocation(
            name="Dugan Wellness Center",
            lat=27.711601112024837,
            lng=-97.32413753070178,
            address="Dugan Wellness Center, TAMUCC",
            description="Student health services and counseling",
            hours="Mon-Fri 8am-5pm",
            category="health",
        ),
        "business": CampusLocation(
            name="College of Business",
            lat=27.714591440638948,
            lng=-97.32466461335527,
            address="College of Business, TAMUCC",
            description="College of Business and entrepreneurship programs",
            hours="Mon-Fri 8am-5pm",
            category="academics",
        ),
        "tidal_hall": CampusLocation(
            name="Tidal Hall",
            lat=27.715529412703646,
            lng=-97.32710819211944,
            address="Tidal Hall, TAMUCC",
            description="Student housing residence hall",
            hours="24/7 for residents",
            category="housing",
        ),
        "harte": CampusLocation(
            name="Harte Research Institute",
            lat=27.713459500631362,
            lng=-97.32815759566772,
            address="Harte Research Institute, TAMUCC",
            description="Gulf of Mexico research and marine science",
            hours="Mon-Fri 8am-5pm",
            category="research",
        ),
        "counseling": CampusLocation(
            name="University Counseling Center",
            lat=27.712490577148014,
            lng=-97.32168122550681,
            address="University Counseling Center, TAMUCC",
            description="Mental health and counseling services for students",
            hours="Mon-Fri 8am-5pm",
            category="health",
        ),
    }

    aliases: Dict[str, str] = {
        "uc": "university_center",
        "university center": "university_center",
        "dining": "islander_dining",
        "islander dining": "islander_dining",
        "natural resources": "nrc",
        "nrc": "nrc",
        "corpus christi hall": "cch",
        "student services": "student_services",
        "center for sciences": "sciences",
        "wellness": "dugan",
        "health": "dugan",
        "counseling center": "counseling",
    }

    return CampusMap(
        variant=MapVariant.PRIMARY.value,
        description="Historic SmartAssist campus map",
        locations=locations,
        aliases=aliases,
    )


def _islanderhack_map() -> CampusMap:
    locations: Dict[str, CampusLocation] = {}

    # Start with the primary dataset to guarantee backwards compatibility.
    base = _primary_map()
    locations.update(base.locations)

    # Add enriched Smart Campus points inspired by the IslanderHack build.
    locations.update(
        {
            "momentum": CampusLocation(
                name="Momentum Village",
                lat=27.708421,
                lng=-97.328912,
                address="Momentum Village, TAMUCC",
                description="Apartment-style housing for upperclassmen with shuttle access",
                hours="24/7 for residents",
                category="housing",
            ),
            "islander_village": CampusLocation(
                name="Islander Village",
                lat=27.709951,
                lng=-97.327211,
                address="Islander Village, TAMUCC",
                description="Suite-style student housing close to the Dugan Wellness Center",
                hours="24/7 for residents",
                category="housing",
            ),
            "performing_arts": CampusLocation(
                name="Performing Arts Center",
                lat=27.714912,
                lng=-97.322172,
                address="Performing Arts Center, TAMUCC",
                description="Concert hall and event venue for music, dance, and theatre",
                hours="Open for scheduled events",
                category="arts",
            ),
            "sandbar": CampusLocation(
                name="The Sandbar",
                lat=27.711291,
                lng=-97.325917,
                address="The Sandbar, TAMUCC",
                description="Student lounge with esports arena, study pods, and grab-n-go cafe",
                hours="Mon-Thu 8am-10pm; Fri 8am-8pm",
                category="student-life",
            ),
            "islander_market": CampusLocation(
                name="Islander Market",
                lat=27.712341,
                lng=-97.325112,
                address="Islander Market, TAMUCC",
                description="Convenience store for snacks, tech accessories, and school supplies",
                hours="Daily 7am-11pm",
                category="dining",
            ),
            "hike_bike": CampusLocation(
                name="Hike & Bike Trail",
                lat=27.710612,
                lng=-97.320817,
                address="Hike & Bike Trail, TAMUCC",
                description="Scenic bayside trail linking housing, Momentum Village, and the beach",
                hours="Open daily",
                category="recreation",
            ),
            "university_beach": CampusLocation(
                name="University Beach",
                lat=27.708972,
                lng=-97.324812,
                address="University Beach, TAMUCC",
                description="Campus beach with volleyball courts, hammocks, and kayak launch",
                hours="Daily 6am-10pm",
                category="recreation",
            ),
            "iolab": CampusLocation(
                name="I-Create Lab",
                lat=27.713892,
                lng=-97.322598,
                address="I-Create Lab, TAMUCC",
                description="Makerspace featuring 3D printers, XR labs, and collaborative studios",
                hours="Mon-Fri 8am-8pm",
                category="innovation",
            ),
            "career_center": CampusLocation(
                name="Career & Professional Development Center",
                lat=27.713122,
                lng=-97.323201,
                address="Career & Professional Development Center, TAMUCC",
                description="Career coaching, internships, and employer meetups",
                hours="Mon-Fri 8am-5pm",
                category="student-life",
            ),
            "islander_suites": CampusLocation(
                name="Islander Housing Suites",
                lat=27.708012,
                lng=-97.326101,
                address="Islander Housing Suites, TAMUCC",
                description="Mixed-use residence hall with collaborative lounges and study decks",
                hours="24/7 for residents",
                category="housing",
            ),
        }
    )

    aliases: Dict[str, str] = dict(base.aliases)
    aliases.update(
        {
            "momentum village": "momentum",
            "momentum": "momentum",
            "islander village": "islander_village",
            "village": "islander_village",
            "performing arts": "performing_arts",
            "performing arts center": "performing_arts",
            "sandbar": "sandbar",
            "islander market": "islander_market",
            "market": "islander_market",
            "trail": "hike_bike",
            "hike": "hike_bike",
            "bike": "hike_bike",
            "beach": "university_beach",
            "university beach": "university_beach",
            "makerspace": "iolab",
            "i-create": "iolab",
            "career center": "career_center",
            "career": "career_center",
            "suites": "islander_suites",
        }
    )

    return CampusMap(
        variant=MapVariant.ISLANDERHACK.value,
        description="Smart Campus IslanderHack enhanced map",
        locations=locations,
        aliases=aliases,
    )


_VARIANTS: Dict[MapVariant, CampusMap] = {
    MapVariant.PRIMARY: _primary_map(),
    MapVariant.ISLANDERHACK: _islanderhack_map(),
}


def get_campus_map(variant: str | MapVariant) -> CampusMap:
    """Return the requested campus map variant.

    Parameters
    ----------
    variant:
        Either a :class:`MapVariant` enum value or the string name of the
        variant.  Unknown variants automatically fall back to the primary map
        to keep the chatbot functional.
    """

    if isinstance(variant, str):
        try:
            variant_enum = MapVariant(variant)
        except ValueError:
            variant_enum = MapVariant.PRIMARY
    else:
        variant_enum = variant

    return _VARIANTS.get(variant_enum, _VARIANTS[MapVariant.PRIMARY])
