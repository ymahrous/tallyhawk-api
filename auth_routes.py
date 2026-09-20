import os
import resend
import storage_client
from sqlalchemy import delete
import database, models, auth
from pydantic import BaseModel
from sqlmodel import Session, select
from datetime import datetime, timezone
from typing import Optional
from dependencies import get_current_user
from tasks import reconvert_user_currency_task
from fastapi import APIRouter, HTTPException, Depends, status, Body

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])

class LoginRequest(BaseModel):
    username: str
    password: str

class SignupRequest(BaseModel):
    username: str
    password: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

# Settings models - use from models.py
UserSettingsUpdate = models.UserSettingsUpdate
UserSettingsRead = models.UserSettingsRead

@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(request: SignupRequest, session: Session = Depends(database.get_session)):
    # 1. Check if user already exists
    existing_user = session.exec(select(models.User).where(models.User.username == request.username)).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # 2. Create new user
    new_user = models.User(
        username=request.username,
        hashed_password=auth.get_password_hash(request.password)
    )
    session.add(new_user)
    session.commit()
    session.refresh(new_user)
    
    # 3. Log them in automatically by returning a token
    token = auth.create_access_token(data={"sub": new_user.username})
    return TokenResponse(access_token=token)

@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest, session: Session = Depends(database.get_session)):
    user = session.exec(select(models.User).where(models.User.username == request.username)).first()
    if not user or not auth.verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    
    token = auth.create_access_token(data={"sub": user.username, "plan": user.plan})
    return TokenResponse(access_token=token)

@router.post("/change-password")
def change_password(
    request: ChangePasswordRequest,
    user: models.User = Depends(get_current_user),
    session: Session = Depends(database.get_session),
):
    if not auth.verify_password(request.current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect.")
    user.hashed_password = auth.get_password_hash(request.new_password)
    session.add(user)
    session.commit()
    return {"message": "Password updated successfully."}


@router.delete("/delete", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    user: models.User = Depends(get_current_user),
    session: Session = Depends(database.get_session),
):

    documents = session.exec(
        select(models.Document).where(models.Document.owner_id == user.id)
    ).all()

    document_ids = [doc.id for doc in documents]

    # Delete files from storage first
    for doc in documents:
        storage_client.delete_from_storage(doc.filename)

    # Delete dependent rows
    if document_ids:
        session.exec(
            delete(models.Extraction).where(
                models.Extraction.document_id.in_(document_ids)
            )
        )

    session.exec(
        delete(models.Document).where(models.Document.owner_id == user.id)
    )

    session.exec(
        delete(models.Vendor).where(models.Vendor.user_id == user.id)
    )

    session.exec(
        delete(models.UsageRecord).where(models.UsageRecord.user_id == user.id)
    )

    session.exec(
        delete(models.Subscription).where(models.Subscription.user_id == user.id)
    )

    session.exec(
        delete(models.Feedback).where(models.Feedback.user_id == user.id)
    )

    session.exec(
        delete(models.PasswordResetToken).where(
            models.PasswordResetToken.user_id == user.id
        )
    )

    session.flush()

    session.delete(user)
    session.commit()
    return {"message": "Account deleted successfully."}

@router.post("/forgot-password")
def forgot_password(email: str = Body(..., embed=True)):
    with Session(database.engine) as session:
        user = session.exec(select(models.User).where(models.User.username == email)).first()
        
        # IMPORTANT: Always return the same message to prevent email-enumeration attacks
        success_msg = "If an account with that email exists, a reset link has been sent."
        
        if not user:
            return {"message": success_msg}
        
        # Generate token
        token_str = auth.generate_password_reset_token()
        
        # Save to DB
        db_token = models.PasswordResetToken(
            user_id=user.id,
            token=token_str
        )
        session.add(db_token)
        session.commit()
        
        frontend_url = os.getenv("FRONTEND_URL")
        reset_url = f"{frontend_url}/reset-password?token={token_str}"
        
        # --- SEND EMAIL VIA RESEND ---
        resend.api_key = os.getenv("RESEND_API_KEY")
        mail_from = os.getenv("MAIL_FROM", "Tallyhawk <onboarding@resend.dev>")

        try:
            resend.Emails.send({
                "from": mail_from,
                "to": [email],
                "subject": "Reset your Tallyhawk password",
                "html": f"""
                    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
                        <h2 style="color: #111;">Reset your password</h2>
                        <p style="color: #555; font-size: 16px; line-height: 1.5;">
                            We received a request to reset your password for your Tallyhawk account.
                            Click the button below to choose a new one:
                        </p>
                        <div style="margin: 30px 0;">
                            <a href="{reset_url}" style="background-color: #000; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 16px;">
                                Reset Password
                            </a>
                        </div>
                        <p style="color: #888; font-size: 14px;">
                            If you didn't request this, you can safely ignore this email. This link expires in 1 hour.
                        </p>
                    </div>
                """
            })
        except Exception as e:
            # Log error but don't expose to user to prevent email enumeration
            print(f"❌ Resend Email Error: {e}")
        
        # Keep console log for easy local testing
        print(f"🔗 PASSWORD RESET LINK for {email}: {reset_url}")
        
        return {"message": success_msg}


@router.post("/reset-password")
def reset_password(
    token: str = Body(..., embed=True),
    new_password: str = Body(..., embed=True)
):
    with Session(database.engine) as session:
        # 1. Find the token
        db_token = session.exec(
            select(models.PasswordResetToken).where(models.PasswordResetToken.token == token)
        ).first()

        if not db_token:
            raise HTTPException(status_code=400, detail="Invalid or expired token.")

        # 2. Check if already used or expired
        if db_token.used:
            raise HTTPException(status_code=400, detail="Token has already been used.")

        if db_token.expires_at < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Token has expired.")

        # 3. Find the user and update password
        user = session.get(models.User, db_token.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")

        user.hashed_password = auth.get_password_hash(new_password)
        session.add(user)

        # 4. Mark token as used
        db_token.used = True
        session.add(db_token)

        session.commit()

        return {"message": "Password updated successfully."}


# Settings endpoints
@router.get("/settings", response_model=UserSettingsRead)
def get_settings(
    user: models.User = Depends(get_current_user)
):
    """Get current user settings."""
    return UserSettingsRead(
        id=user.id,
        username=user.username,
        plan=user.plan,
        base_currency=user.base_currency,
        created_at=user.created_at
    )


@router.patch("/settings", response_model=UserSettingsRead)
def update_settings(
    settings: UserSettingsUpdate,
    user: models.User = Depends(get_current_user),
    session: Session = Depends(database.get_session),
):
    """Update user settings (currently only base_currency)."""
    if settings.base_currency is not None:
        new_currency = settings.base_currency.upper()
        currency_changed = new_currency != user.base_currency

        user.base_currency = new_currency
        session.add(user)
        session.commit()
        session.refresh(user)

        if currency_changed:
            # Re-convert every existing document into the new base_currency so
            # invoices, dashboard totals, analytics, and tax summaries don't
            # keep showing amounts denominated in the currency the user just
            # left (see tasks.reconvert_user_currency_task).
            reconvert_user_currency_task.delay(user.id)

    return UserSettingsRead(
        id=user.id,
        username=user.username,
        plan=user.plan,
        base_currency=user.base_currency,
        created_at=user.created_at
    )