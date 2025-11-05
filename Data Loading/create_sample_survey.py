"""
Script to create a sample survey in MongoDB for testing
"""
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import pymongo

load_dotenv()

# Connect to MongoDB
MONGO_URI = os.getenv("MONGODB_URI", "mongodb://mongo:27017/smartassist")
client = pymongo.MongoClient(MONGO_URI)
db = client["smartassist"]

# Create a sample survey
sample_survey = {
    "title": "Fall 2025 Course Evaluation",
    "description": "Help us improve our courses by sharing your feedback",
    "survey_type": "course_evaluation",
    "status": "active",
    "target_audience": "students",
    "questions": [
        {
            "question_id": "q1",
            "question_text": "How satisfied are you with the overall course content?",
            "question_type": "rating",
            "options": None,
            "required": True,
            "order": 1
        },
        {
            "question_id": "q2",
            "question_text": "Which aspect of the course was most helpful?",
            "question_type": "multiple_choice",
            "options": [
                "Lectures and presentations",
                "Hands-on projects",
                "Group discussions",
                "Reading materials",
                "Other"
            ],
            "required": True,
            "order": 2
        },
        {
            "question_id": "q3",
            "question_text": "What improvements would you suggest for this course?",
            "question_type": "text",
            "options": None,
            "required": False,
            "order": 3
        },
        {
            "question_id": "q4",
            "question_text": "Would you recommend this course to other students?",
            "question_type": "yes_no",
            "options": None,
            "required": True,
            "order": 4
        }
    ],
    "start_date": datetime.now().isoformat(),
    "end_date": (datetime.now() + timedelta(days=30)).isoformat(),
    "is_anonymous": True,
    "created_by": "admin@tamucc.edu",
    "created_by_name": "System Administrator",
    "created_at": datetime.now().isoformat(),
    "total_responses": 0
}

# Insert the survey
result = db.surveys.insert_one(sample_survey)
print(f"✅ Sample survey created successfully!")
print(f"Survey ID: {result.inserted_id}")
print(f"Title: {sample_survey['title']}")
print(f"Questions: {len(sample_survey['questions'])}")
print(f"Target: {sample_survey['target_audience']}")
print(f"Ends: {sample_survey['end_date'][:10]}")

# Create another sample survey
service_survey = {
    "title": "Campus Services Feedback",
    "description": "Share your experience with our campus services",
    "survey_type": "service_feedback",
    "status": "active",
    "target_audience": "all",
    "questions": [
        {
            "question_id": "q1",
            "question_text": "How would you rate our ticket resolution service?",
            "question_type": "rating",
            "options": None,
            "required": True,
            "order": 1
        },
        {
            "question_id": "q2",
            "question_text": "How was your experience booking appointments?",
            "question_type": "multiple_choice",
            "options": [
                "Excellent - Very easy",
                "Good - Easy enough",
                "Average - Some difficulties",
                "Poor - Very difficult"
            ],
            "required": True,
            "order": 2
        },
        {
            "question_id": "q3",
            "question_text": "Any suggestions for improving our services?",
            "question_type": "text",
            "options": None,
            "required": False,
            "order": 3
        }
    ],
    "start_date": datetime.now().isoformat(),
    "end_date": (datetime.now() + timedelta(days=15)).isoformat(),
    "is_anonymous": True,
    "created_by": "admin@tamucc.edu",
    "created_by_name": "System Administrator",
    "created_at": datetime.now().isoformat(),
    "total_responses": 0
}

result2 = db.surveys.insert_one(service_survey)
print(f"\n✅ Second survey created successfully!")
print(f"Survey ID: {result2.inserted_id}")
print(f"Title: {service_survey['title']}")
print(f"Questions: {len(service_survey['questions'])}")
print(f"Target: {service_survey['target_audience']}")

print("\n🎉 Done! Refresh your browser to see the surveys.")
