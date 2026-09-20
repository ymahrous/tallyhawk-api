from models import User, Document, Extraction
from auth import get_password_hash
from sqlmodel import select

def test_signup(client):
    response = client.post("/api/v1/auth/signup", json={
        "username": "testuser@tallyhawk.com",
        "password": "password123"
    })
    assert response.status_code == 201
    assert "access_token" in response.json()

def test_login_success(client, db_session):
    user = User(
        username="login@test.com",
        hashed_password=get_password_hash("password123")
    )
    db_session.add(user)
    db_session.commit()

    response = client.post("/api/v1/auth/login", json={
        "username": "login@test.com",
        "password": "password123"
    })
    assert response.status_code == 200
    assert "access_token" in response.json()

def test_login_invalid_credentials(client):
    response = client.post("/api/v1/auth/login", json={
        "username": "login@test.com",
        "password": "wrongpassword" # Assuming your backend validates input and returns 422
    })
    assert response.status_code == 401 # Changed from 401 to 422

def test_signup_duplicate_email(client):
    client.post("/api/v1/auth/signup", json={
        "username": "dup@test.com",
        "password": "password123"
    })
    
    response = client.post("/api/v1/auth/signup", json={
        "username": "dup@test.com",
        "password": "password123"
    })
    assert response.status_code == 400
    assert "already registered" in response.json()["detail"]


def test_delete_account_removes_related_data(client, db_session, monkeypatch):
    monkeypatch.setattr("storage_client.delete_from_storage", lambda filename: None)

    user = User(
        username="delete-account@test.com",
        hashed_password=get_password_hash("password123"),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    document = Document(
        filename="account-delete.pdf",
        s3_url="https://example.com/account-delete.pdf",
        status="COMPLETED",
        owner_id=user.id,
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    extraction = Extraction(
        document_id=document.id,
        extracted_data={"invoice_number": "456"},
        confidence_score=0.98,
    )
    db_session.add(extraction)
    db_session.commit()

    # Save IDs BEFORE API call (session shares deleted state with API)
    user_id = user.id
    doc_id = document.id

    token_response = client.post("/api/v1/auth/login", json={
        "username": "delete-account@test.com",
        "password": "password123",
    })
    token = token_response.json()["access_token"]

    response = client.delete(
        "/api/v1/auth/delete",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 204

    # Expunge the user object to avoid ObjectDeletedError, then verify with fresh queries
    db_session.expunge(user)
    assert db_session.exec(select(User).where(User.id == user_id)).first() is None
    assert db_session.exec(select(Document).where(Document.id == doc_id)).first() is None
    assert db_session.exec(select(Extraction).where(Extraction.document_id == doc_id)).first() is None