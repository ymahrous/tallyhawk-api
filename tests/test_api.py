from sqlmodel import Session, select
from models import User, Document, Extraction
from auth import get_password_hash
from datetime import datetime, timezone

def test_upload_unauthorized(client):
    response = client.post(
        "/api/v1/upload/",
        files={"file": ("test.jpg", b"fake_image_data", "image/jpeg")}
    )
    assert response.status_code == 401

def test_get_documents_unauthorized(client):
    response = client.get("/api/v1/documents/") # Note: Fixed typo from /api/v1/documents/
    assert response.status_code == 401

def test_delete_document_unauthorized(client):
    response = client.delete("/api/v1/documents/test-document-id")
    assert response.status_code == 401

def test_delete_document_with_extraction(client, db_session, monkeypatch):
    monkeypatch.setattr("storage_client.delete_from_storage", lambda filename: None)

    user = User(
        username="delete_test@test.com",
        hashed_password=get_password_hash("password123"),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    document = Document(
        filename="test.pdf",
        s3_url="https://example.com/test.pdf",
        status="COMPLETED",
        owner_id=user.id,
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    extraction = Extraction(
        document_id=document.id,
        extracted_data={"invoice_number": "123"},
        confidence_score=0.99,
    )
    db_session.add(extraction)
    db_session.commit()

    token_response = client.post("/api/v1/auth/login", json={
        "username": "delete_test@test.com",
        "password": "password123"
    })
    token = token_response.json()["access_token"]

    response = client.delete(
        f"/api/v1/documents/{document.id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 204

    # Verify deletion using the same session
    assert db_session.exec(select(Document).where(Document.id == document.id)).first() is None
    assert db_session.exec(select(Extraction).where(Extraction.document_id == document.id)).first() is None

# def test_get_documents_authorized(client):
#     # 1. Manually create the user in the test database
#     with TestingSessionLocal() as session:
#         user = User(
#             username="api_test@test.com",
#             hashed_password=get_password_hash("password123")
#         )
#         session.add(user)
#         session.commit()
        
#     # 2. Get the token
#     token_response = client.post("/api/v1/auth/login", json={
#         "username": "testuser@tallyhawk.com",
#         "password": "password123"
#     })
    
#     # 3. Access protected route with token
#     response = client.get(
#         "/api/v1/documents/", # Note: Make sure this matches your actual route in main.py!
#         headers={"Authorization": f"Bearer {token_response.json()['access_token']}"}
#     )
#     print(response.json())
#     assert response.status_code == 200
#     assert response.json() == []