from sqlmodel import Session, select

from app.core.security import hash_password, verify_password, create_access_token
from app.models.user import User


class AuthService:
    def __init__(self, session: Session):
        self.session = session

    def authenticate(self, username: str, password: str) -> str | None:
        user = self.session.exec(select(User).where(User.username == username)).first()
        if not user or not verify_password(password, user.hashed_password):
            return None
        return create_access_token(subject=user.username)

    def get_current_user(self, username: str) -> User | None:
        return self.session.exec(select(User).where(User.username == username)).first()
