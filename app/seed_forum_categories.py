# seed_forum_categories.py

from app.db.mongo import forum_categories

seed = [
    {
        "slug": "general",
        "name": "General",
        "description": "Anything campus-related, announcements, doubts.",
    },
    {
        "slug": "it-support",
        "name": "IT / LMS support",
        "description": "Wi-Fi, LMS, portal, login, email issues.",
    },
    {
        "slug": "hostel",
        "name": "Hostel / Mess",
        "description": "Hostel timings, mess menu, facilities requests.",
    },
    {
        "slug": "events",
        "name": "Events & Clubs",
        "description": "Workshops, hackathons, student club activities.",
    },
    {
        "slug": "placements",
        "name": "Placements & Internships",
        "description": "Drives, resume review, off-campus opportunities.",
    },
]

for cat in seed:
    forum_categories.update_one(
        {"slug": cat["slug"]},
        {"$setOnInsert": cat},
        upsert=True,
    )

print("✅ Forum categories seeded (upserted).")
