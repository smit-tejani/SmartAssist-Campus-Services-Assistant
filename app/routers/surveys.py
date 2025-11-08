from __future__ import annotations

from datetime import datetime
from typing import List

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db.mongo import surveys_collection, db
from app.dependencies.auth import get_current_user
from app.services.notifications import _notify_survey_available
from app.routers.students import convert_objectid_to_str

router = APIRouter()


class SurveyQuestionCreate(BaseModel):
    question_id: str
    question_text: str
    question_type: str
    options: List[str] | None = None
    required: bool = True
    order: int


class SurveyCreate(BaseModel):
    title: str
    description: str | None = None
    survey_type: str
    target_audience: str = "all"
    questions: List[SurveyQuestionCreate]
    start_date: str
    end_date: str
    is_anonymous: bool = True


class SurveyAnswerSubmit(BaseModel):
    question_id: str
    answer: str | int


class SurveyResponseSubmit(BaseModel):
    answers: List[SurveyAnswerSubmit]


@router.post("/api/surveys/create")
async def create_survey(survey: SurveyCreate, user: dict = Depends(get_current_user)):
    if user.get("role") not in ["admin", "staff"]:
        raise HTTPException(status_code=403, detail="Only admin and staff can create surveys")

    survey_doc = {
        "title": survey.title,
        "description": survey.description,
        "survey_type": survey.survey_type,
        "status": "active",
        "target_audience": survey.target_audience,
        "questions": [q.dict() for q in survey.questions],
        "start_date": survey.start_date,
        "end_date": survey.end_date,
        "is_anonymous": survey.is_anonymous,
        "created_by": user.get("email"),
        "created_by_name": user.get("full_name", user.get("email")),
        "created_at": datetime.now().isoformat(),
        "total_responses": 0,
    }

    result = surveys_collection.insert_one(survey_doc)
    await _notify_survey_available(survey_doc, str(result.inserted_id))

    return {
        "success": True,
        "message": "Survey created successfully",
        "survey_id": str(result.inserted_id),
    }


@router.get("/api/surveys")
async def get_surveys(user: dict = Depends(get_current_user)):
    if user.get("role") not in ["admin", "staff"]:
        raise HTTPException(status_code=403, detail="Unauthorized")

    surveys = list(surveys_collection.find().sort("created_at", -1))
    return convert_objectid_to_str(surveys)


@router.get("/api/surveys/available")
async def get_available_surveys(user: dict = Depends(get_current_user)):
    user_email = user.get("email")
    user_role = user.get("role")

    query = {"status": "active", "end_date": {"$gte": datetime.now().isoformat()}}

    if user_role == "student":
        query["$or"] = [{"target_audience": "all"}, {"target_audience": "students"}]
    elif user_role == "staff":
        query["$or"] = [{"target_audience": "all"}, {"target_audience": "staff"}]

    surveys = list(surveys_collection.find(query).sort("created_at", -1))

    for survey in surveys:
        survey_id = str(survey["_id"])
        response = db.survey_responses.find_one({"survey_id": survey_id, "respondent_email": user_email})
        survey["already_responded"] = response is not None

    return convert_objectid_to_str(surveys)


@router.get("/api/surveys/submitted/count")
async def get_submitted_surveys_count(user: dict = Depends(get_current_user)):
    user_email = user.get("email")
    count = db.survey_responses.count_documents({"respondent_email": user_email})
    return {"count": count}


@router.get("/api/surveys/{survey_id}")
async def get_survey(survey_id: str, user: dict = Depends(get_current_user)):
    survey = surveys_collection.find_one({"_id": ObjectId(survey_id)})
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found")

    survey["_id"] = str(survey["_id"])

    response = db.survey_responses.find_one({"survey_id": survey_id, "respondent_email": user.get("email")})
    survey["already_responded"] = response is not None

    return survey


@router.post("/api/surveys/{survey_id}/submit")
async def submit_survey_response(survey_id: str, response: SurveyResponseSubmit, user: dict = Depends(get_current_user)):
    survey = surveys_collection.find_one({"_id": ObjectId(survey_id)})
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found")

    user_email = user.get("email")
    existing_response = db.survey_responses.find_one({"survey_id": survey_id, "respondent_email": user_email})
    if existing_response:
        raise HTTPException(status_code=400, detail="You have already submitted this survey")

    response_doc = {
        "survey_id": survey_id,
        "respondent_email": user_email,
        "respondent_name": user.get("full_name", user_email) if not survey.get("is_anonymous") else "Anonymous",
        "respondent_role": user.get("role"),
        "is_anonymous": survey.get("is_anonymous", False),
        "answers": [a.dict() for a in response.answers],
        "submitted_at": datetime.now().isoformat(),
    }

    db.survey_responses.insert_one(response_doc)

    surveys_collection.update_one({"_id": ObjectId(survey_id)}, {"$inc": {"total_responses": 1}})

    return {"success": True, "message": "Survey response submitted successfully"}


@router.get("/api/surveys/{survey_id}/results")
async def get_survey_results(survey_id: str, user: dict = Depends(get_current_user)):
    if user.get("role") not in ["admin", "staff"]:
        raise HTTPException(status_code=403, detail="Unauthorized")

    survey = surveys_collection.find_one({"_id": ObjectId(survey_id)})
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found")

    responses = list(db.survey_responses.find({"survey_id": survey_id}))

    return {
        "survey": convert_objectid_to_str(survey),
        "responses": convert_objectid_to_str(responses),
        "total_responses": len(responses),
    }


@router.put("/api/surveys/{survey_id}/close")
async def close_survey(survey_id: str, user: dict = Depends(get_current_user)):
    if user.get("role") not in ["admin", "staff"]:
        raise HTTPException(status_code=403, detail="Unauthorized")

    result = surveys_collection.update_one(
        {"_id": ObjectId(survey_id)},
        {"$set": {"status": "closed", "closed_at": datetime.now().isoformat()}},
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Survey not found")

    return {"success": True, "message": "Survey closed successfully"}


@router.delete("/api/surveys/{survey_id}")
async def delete_survey(survey_id: str, user: dict = Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can delete surveys")

    result = surveys_collection.delete_one({"_id": ObjectId(survey_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Survey not found")

    db.survey_responses.delete_many({"survey_id": survey_id})

    return {"success": True, "message": "Survey deleted successfully"}
