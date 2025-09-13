# SmartAssist – Campus Services Assistant

SmartAssist is an AI-powered campus services assistant designed to enhance student support and streamline campus interactions at **Texas A&M University–Corpus Christi (TAMU-CC)**. It provides students with a centralized, intelligent platform to get answers to common questions, submit service requests, schedule appointments, and receive real-time location guidance — all from a single responsive web interface.

## 🚀 Features

- **Intelligent Knowledge Base & FAQs** – Searchable database of articles, guides, and step-by-step instructions available 24/7.
- **AI-Powered Chatbot** – Natural language query handling, with escalation to live chat when needed.
- **Service Request & Ticket Management** – Submit, track, and manage service requests in real-time.
- **Appointment Scheduling** – Book and manage advising or service appointments from anywhere.
- **Responsive Web Portal** – Consistent user experience across desktop, tablet, and mobile devices.
- **Feedback & Surveys** – Built-in rating tools for students to share feedback.
- **Analytics & Reporting** – Dashboards for staff to monitor ticket resolution time, common issues, and knowledge base usage.
- **Location Guidance** – Real-time directions for professor offices, classrooms, labs, and campus buildings.
- **Optional Community Forum** – Peer-to-peer Q&A and collaboration space.
- **Admin Portal** – For staff to update professor info, building changes, and event venues.
- **LLM + RAG Integration** – Retrieves professor details and building info from the campus database.

## 🎯 Goals

1. Resolve **60%+ of student queries** through self-service without staff intervention.
2. Reduce **average ticket resolution time by 30%** with an organized ticketing system.
3. Provide a **mobile-friendly interface** meeting accessibility standards for on-the-go access.
4. Deliver **analytics dashboards** to help staff improve services and reduce repetitive inquiries.
5. Automate repetitive location-based questions to **reduce front desk dependency**.

## 🛠️ Tech Stack

- **Frontend:** React (Responsive Web Portal, UI Components)
- **Backend:** Node.js / Express (API Development), Python for AI/ML Pipeline
- **Database:** PostgreSQL / MongoDB (Professor and Building Info, Tickets, User Data)
- **AI & NLP:** LLM + Retrieval-Augmented Generation (RAG) pipeline
- **Maps Integration:** Google Maps API or OpenStreetMap
- **Deployment:** Docker + AWS (Staging & Production Environments)
- **Version Control:** GitHub/GitLab

## 🧪 Testing

Our testing approach covers multiple levels to ensure reliability and accuracy of the system:

- Unit Tests for chatbot responses and API endpoints
- Integration Tests for frontend-backend communication
- System Testing for end-to-end user scenarios
- User Testing with students and faculty for feedback
